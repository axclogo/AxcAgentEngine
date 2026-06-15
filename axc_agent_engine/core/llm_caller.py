"""LLMCaller — LLM 调用封装，负责 fallback、重试、计时和 hook 通知。"""
from __future__ import annotations

import asyncio
import logging
import random
import time
from copy import deepcopy
from typing import Any, Awaitable, Callable, TYPE_CHECKING

import httpx

from axc_agent_engine.core.context import ExecutionContext
from axc_agent_engine.core.stream_aggregator import StreamAggregator
from axc_agent_engine.core.stream_sink import StreamSink
from axc_agent_engine.core.errors import (
	LLMTimeoutError,
	ProviderContractError,
	ProviderError,
	RetryableProviderError,
)
from axc_agent_engine.core.events import Event, EventType
from axc_agent_engine.plugins import model_info_from_models
from axc_agent_engine.core.schema import LLMResponse

if TYPE_CHECKING:
	from axc_agent_engine.core.plugin_manager import PluginManager
	from axc_agent_engine.llm.provider import LLMProvider

logger = logging.getLogger(__name__)
PROVIDER_MESSAGE_KEYS = {"role", "content", "name", "tool_call_id", "tool_calls", "function_call"}
RETRYABLE_LLM_ERRORS = (httpx.NetworkError, httpx.TimeoutException, RetryableProviderError, LLMTimeoutError)


class StreamEventEmitter:
	"""Emits realtime stream events while preserving the existing queue callback contract.
中文：此文档说明相关引擎组件的行为。"""

	def __init__(self, ctx: ExecutionContext, sink: StreamSink | None = None) -> None:
		self._ctx = ctx
		self._sink = sink
		self._in_thinking = False
		self._stream_started = False
		self.preview_events: list[Event] = []

	async def on_delta(self, event_type: str, content: str, meta: dict) -> None:
		if event_type == "thinking_delta":
			await self._thinking_delta(content)
		elif event_type == "content_delta":
			await self._content_delta(content)
		elif event_type == "tool_args_delta":
			await self._tool_args_delta(content, meta)

	async def finish_thinking(self, content: str) -> None:
		if self._in_thinking and self._sink:
			await self._sink.emit(Event(type=EventType.THINKING_END, content=content))
		self._in_thinking = False

	async def _thinking_delta(self, content: str) -> None:
		if not self._sink:
			return
		if not self._in_thinking:
			self._in_thinking = True
			await self._sink.emit(Event(type=EventType.THINKING_START))
		await self._sink.emit(Event(type=EventType.THINKING_DELTA, content=content))

	async def _content_delta(self, content: str) -> None:
		self._ctx.runtime.stream_delta_emitted = True
		if not self._sink:
			return
		if self._in_thinking:
			self._in_thinking = False
			await self._sink.emit(Event(type=EventType.THINKING_END))
		if not self._stream_started:
			self._stream_started = True
			await self._sink.emit(Event(type=EventType.STREAM_START))
		await self._sink.emit(Event(type=EventType.STREAM_DELTA, content=content))

	async def _tool_args_delta(self, content: str, meta: dict) -> None:
		event = Event.tool_args_preview(
			meta.get("tool_name", ""),
			meta.get("tool_call_id", ""),
			content,
			meta.get("arguments_preview", ""),
			int(meta.get("index", 0) or 0),
		)
		if self._sink:
			await self._sink.emit(event)
		else:
			self.preview_events.append(event)


class StreamUsageReporter:
	"""Builds usage-related stream events without changing LLMCaller output shape.
中文：此文档说明相关引擎组件的行为。"""

	def apply_usage(self, ctx: ExecutionContext, result) -> list[Event]:
		if result.usage_input or result.usage_output:
			ctx.add_usage(result.usage_input, result.usage_output)
		events: list[Event] = []
		if result.cached_tokens > 0:
			metadata = {"cached_tokens": result.cached_tokens}
			metadata.update(getattr(result, "usage_metadata", {}) or {})
			events.append(Event(type=EventType.CACHE_HIT, metadata=metadata))
		if result.usage_input or result.usage_output:
			events.append(Event(type=EventType.COST_UPDATE, metadata={
				"input_tokens": ctx.state.total_input_tokens,
				"output_tokens": ctx.state.total_output_tokens,
			}))
		return events


class LLMCaller:
	"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
封装 LLM 调用：fallback 切换、计时、pre/post hook。"""

	def __init__(
		self,
		primary: "LLMProvider",
		fallback: "LLMProvider | None",
		plugin_manager: "PluginManager",
	) -> None:
		self._primary = primary
		self._fallback = fallback
		self._pm = plugin_manager

	async def call(
		self,
		ctx: ExecutionContext,
		messages: list[dict],
		tools: list[dict] | None,
		stream_sink: StreamSink | None = None,
	) -> tuple[dict, list[Event]]:
		"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
统一入口：按 ctx.config.stream 分发到流式或非流式调用。"""
		messages, tools = self._pm.apply_pre_llm_call(ctx, messages, tools)
		messages = _provider_messages(messages)
		before_input_tokens = ctx.state.total_input_tokens
		before_output_tokens = ctx.state.total_output_tokens
		start = time.time()
		if ctx.config.stream:
			result = await self._call_stream(ctx, messages, tools, stream_sink)
		else:
			result = await self._call_sync(ctx, messages, tools)
		duration_ms = int((time.time() - start) * 1000)
		response_summary = {"usage": {
			"input_tokens": max(0, ctx.state.total_input_tokens - before_input_tokens),
			"output_tokens": max(0, ctx.state.total_output_tokens - before_output_tokens),
		}, "total_usage": {
			"input_tokens": ctx.state.total_input_tokens,
			"output_tokens": ctx.state.total_output_tokens,
		}}
		await self._pm.post_llm_call(ctx, messages, response_summary, duration_ms)
		return result

	async def _call_sync(
		self, ctx: ExecutionContext, messages: list[dict], tools: list[dict] | None,
	) -> tuple[dict, list[Event]]:
		"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
带重试和 fallback 的非流式调用。"""
		kwargs = self._build_kwargs(ctx, tools)
		async def run(client: "LLMProvider") -> tuple[dict, list[Event]]:
			llm_resp: LLMResponse = await client.chat(messages, tools, **kwargs)
			return self._process_sync_response(ctx, _ensure_llm_response(llm_resp))

		return await self._call_with_retry(ctx, run, fallback_allowed=lambda: True)

	async def _call_stream(
		self,
		ctx: ExecutionContext,
		messages: list[dict],
		tools: list[dict] | None,
		stream_sink: StreamSink | None = None,
	) -> tuple[dict, list[Event]]:
		"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
带重试和 fallback 的流式调用。"""
		kwargs = self._build_kwargs(ctx, tools)
		if ctx.config.thinking in ("always", "auto"):
			kwargs["thinking"] = ctx.config.thinking
		async def run(client: "LLMProvider") -> tuple[dict, list[Event]]:
			return await self._aggregate_stream(ctx, client, messages, tools, stream_sink=stream_sink, **kwargs)

		def fallback_allowed() -> bool:
			return not ctx.runtime.stream_delta_emitted

		return await self._call_with_retry(ctx, run, fallback_allowed)

	async def _call_with_retry(
		self,
		ctx: ExecutionContext,
		call: Callable[["LLMProvider"], Awaitable[tuple[dict, list[Event]]]],
		fallback_allowed: Callable[[], bool],
	) -> tuple[dict, list[Event]]:
		for attempt in range(2):
			try:
				return await call(self._primary)
			except RETRYABLE_LLM_ERRORS as e:
				if not fallback_allowed():
					raise ProviderError(f"Stream interrupted after partial output: {e}") from e
				if attempt == 0:
					await _sleep_before_retry(e)
					continue
				if self._fallback:
					logger.warning(f"Primary LLM retry failed, switching to fallback: {e}")
					self._mark_fallback(ctx, str(e))
					return await call(self._fallback)
				raise
		raise ProviderError("LLM call failed after retries")

	async def _aggregate_stream(
		self, ctx: ExecutionContext, client: "LLMProvider",
		messages: list[dict], tools: list[dict] | None, stream_sink: StreamSink | None = None, **kwargs,
	) -> tuple[dict[str, Any], list[Event]]:
		"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
通过 StreamAggregator 聚合 LLMStreamChunk，并产生实时 delta 事件。"""
		aggregator = StreamAggregator()
		aiter = client.stream(messages, tools, **kwargs)
		emitter = StreamEventEmitter(ctx, stream_sink)
		usage_reporter = StreamUsageReporter()

		result = await aggregator.aggregate(aiter, ctx.config.stream_idle_timeout, on_delta=emitter.on_delta)
		await emitter.finish_thinking(result.thinking_content)
		events: list[Event] = []
		if not stream_sink:
			if result.has_content:
				events.append(Event(type=EventType.STREAM_START))
				events.append(Event(type=EventType.STREAM_DELTA, content=result.message.get("content", "")))
			if result.thinking_content:
				events.append(Event(type=EventType.THINKING_START))
				events.append(Event(type=EventType.THINKING_DELTA, content=result.thinking_content))
				events.append(Event(type=EventType.THINKING_END, content=result.thinking_content))
		events.extend(usage_reporter.apply_usage(ctx, result))
		events.extend(emitter.preview_events)
		return result.message, events

	def _process_sync_response(
		self, ctx: ExecutionContext, llm_resp: LLMResponse,
	) -> tuple[dict, list[Event]]:
		"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
处理 provider 返回的标准化 LLMResponse。"""
		if llm_resp.usage.input_tokens or llm_resp.usage.output_tokens:
			ctx.add_usage(llm_resp.usage.input_tokens, llm_resp.usage.output_tokens)
		events: list[Event] = []
		if llm_resp.usage.cached_tokens:
			metadata = {"cached_tokens": llm_resp.usage.cached_tokens}
			raw = llm_resp.raw if isinstance(llm_resp.raw, dict) else {}
			if raw.get("cache_type"):
				metadata["cache_type"] = raw["cache_type"]
			events.append(Event(type=EventType.CACHE_HIT, metadata=metadata))
		if llm_resp.usage.input_tokens or llm_resp.usage.output_tokens:
			events.append(Event(type=EventType.COST_UPDATE, metadata={
				"input_tokens": ctx.state.total_input_tokens,
				"output_tokens": ctx.state.total_output_tokens,
			}))
		return llm_resp.message.to_dict(), events

	def _build_kwargs(self, ctx: ExecutionContext, tools: list[dict] | None) -> dict:
		"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
构建 LLM 调用参数。"""
		kwargs: dict[str, Any] = {}
		if tools:
			kwargs["parallel_tool_calls"] = ctx.config.parallel_tool_calls
		llm_options = ctx.runtime.llm_options
		for key in ("temperature", "max_tokens", "top_p", "stop",
					"presence_penalty", "frequency_penalty", "seed", "user", "response_format"):
			if key in llm_options:
				kwargs[key] = llm_options[key]
		return kwargs

	def _mark_fallback(self, ctx: ExecutionContext, reason: str) -> None:
		ctx.state.fallback_triggered = True
		ctx.state.fallback_reason = reason
		model_info = model_info_from_models(
			self._primary, self._fallback, getattr(ctx, "utility_model", None), self._fallback)
		ctx.runtime.model_info = model_info
		ctx.state.metadata["model"] = model_info.to_dict()


def _provider_messages(messages: list[dict]) -> list[dict]:
	"""English: Strip engine-only message fields. 中文：剥离仅供引擎内部使用的消息字段。"""
	return [{key: deepcopy(value) for key, value in message.items() if key in PROVIDER_MESSAGE_KEYS} for message in messages]


def _ensure_llm_response(value: Any) -> LLMResponse:
	if not isinstance(value, LLMResponse):
		raise ProviderContractError(f"LLMProvider.chat 必须返回 LLMResponse，实际得到 {type(value).__name__}")
	return value


async def _sleep_before_retry(error: BaseException) -> None:
	delay = 1.0 + random.uniform(0, 0.5)
	logger.warning(f"Primary LLM network error, retrying in {delay:.1f}s: {error}")
	await asyncio.sleep(delay)
