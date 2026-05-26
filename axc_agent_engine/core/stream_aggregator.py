"""StreamAggregator — 把 LLMStreamChunk 聚合成完整消息。"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Awaitable, Callable

from axc_agent_engine.core.constants import STREAM_MAX_CHUNKS, STREAM_MAX_CONTENT_LENGTH
from axc_agent_engine.core.errors import CancelledError
from axc_agent_engine.core.schema import LLMStreamChunk

logger = logging.getLogger(__name__)

StreamCallback = Callable[[str, str, dict[str, Any]], Awaitable[None]]
"""回调签名：(event_type, content, metadata) -> None。"""


@dataclass
class AggregatedMessage:
	"""流式聚合后的结果。"""
	message: dict[str, Any] = field(default_factory=dict)
	thinking_content: str = ""
	usage_input: int = 0
	usage_output: int = 0
	cached_tokens: int = 0
	usage_metadata: dict[str, Any] = field(default_factory=dict)
	has_content: bool = False
	partial: bool = False


@dataclass
class StreamAggregateState:
	content_parts: list[str] = field(default_factory=list)
	tool_calls_map: dict[int, dict] = field(default_factory=dict)
	thinking_parts: list[str] = field(default_factory=list)
	usage_input: int = 0
	usage_output: int = 0
	cached_tokens: int = 0
	usage_metadata: dict[str, Any] = field(default_factory=dict)
	chunk_count: int = 0
	content_length: int = 0
	partial: bool = False

	def limit_exceeded(self) -> str:
		if self.chunk_count >= STREAM_MAX_CHUNKS:
			self.partial = True
			return f"Stream exceeded max chunks limit ({STREAM_MAX_CHUNKS})"
		if self.content_length >= STREAM_MAX_CONTENT_LENGTH:
			self.partial = True
			return f"Stream exceeded max content length ({STREAM_MAX_CONTENT_LENGTH})"
		return ""

	async def merge_chunk(self, chunk: LLMStreamChunk, on_delta: StreamCallback | None = None) -> None:
		if not isinstance(chunk, LLMStreamChunk):
			raise TypeError(f"LLMProvider.stream 必须产出 LLMStreamChunk，实际得到 {type(chunk).__name__}")
		self.chunk_count += 1
		if chunk.thinking_delta:
			self.thinking_parts.append(chunk.thinking_delta)
			if on_delta:
				await on_delta("thinking_delta", chunk.thinking_delta, {})
		if chunk.content_delta:
			self.content_parts.append(chunk.content_delta)
			self.content_length += len(chunk.content_delta)
			if on_delta:
				await on_delta("content_delta", chunk.content_delta, {})
		if chunk.tool_call_delta:
			await self.merge_tool_delta(chunk.tool_call_delta, on_delta)
		if chunk.usage:
			self.usage_input += chunk.usage.input_tokens
			self.usage_output += chunk.usage.output_tokens
			self.cached_tokens += chunk.usage.cached_tokens
			if chunk.metadata:
				self.usage_metadata.update(chunk.metadata)

	async def merge_tool_delta(self, tool_call_delta: dict, on_delta: StreamCallback | None = None) -> None:
		if "tool_calls" in tool_call_delta:
			for tc in tool_call_delta["tool_calls"]:
				self._merge_tool_call(tc)
				if on_delta:
					await on_delta("tool_args_delta", _tool_args_delta(tc), _tool_preview_meta(self.tool_calls_map, tc))
			return
		self._merge_tool_call(tool_call_delta)
		if on_delta:
			await on_delta("tool_args_delta", _tool_args_delta(tool_call_delta), _tool_preview_meta(self.tool_calls_map, tool_call_delta))

	def build_message(self) -> AggregatedMessage:
		content = "".join(self.content_parts)
		message: dict[str, Any] = {"role": "assistant", "content": content}
		if self.tool_calls_map:
			message["tool_calls"] = [self.tool_calls_map[i] for i in sorted(self.tool_calls_map.keys())]
		return AggregatedMessage(
			message=message,
			thinking_content="".join(self.thinking_parts),
			usage_input=self.usage_input,
			usage_output=self.usage_output,
			cached_tokens=self.cached_tokens,
			usage_metadata=dict(self.usage_metadata),
			has_content=bool(self.content_parts),
			partial=self.partial,
		)

	def has_partial_payload(self) -> bool:
		return bool(self.content_parts or self.tool_calls_map)

	def _merge_tool_call(self, tc_delta: dict) -> None:
		"""Merge one tool_call delta into accumulated OpenAI tool_call shape."""
		idx = tc_delta.get("index", 0)
		if idx not in self.tool_calls_map:
			self.tool_calls_map[idx] = {"id": tc_delta.get("id", ""), "function": {"name": "", "arguments": ""}}
		tc_entry = self.tool_calls_map[idx]
		if tc_delta.get("id"):
			tc_entry["id"] = tc_delta["id"]
		fn_delta = tc_delta.get("function", {})
		if fn_delta.get("name"):
			tc_entry["function"]["name"] = fn_delta["name"]
		if fn_delta.get("arguments"):
			tc_entry["function"]["arguments"] += fn_delta["arguments"]


class StreamAggregator:
	"""把 LLMStreamChunk 聚合成完整 message dict。"""

	async def aggregate(
		self, aiter: AsyncIterator[LLMStreamChunk], idle_timeout: int,
		on_delta: StreamCallback | None = None,
	) -> AggregatedMessage:
		"""把流式 chunk 聚合成 AggregatedMessage。"""
		state = StreamAggregateState()
		iterator = aiter.__aiter__()
		while True:
			limit_error = state.limit_exceeded()
			if limit_error:
				logger.warning(limit_error)
				break
			try:
				chunk: LLMStreamChunk = await asyncio.wait_for(iterator.__anext__(), timeout=idle_timeout)
			except StopAsyncIteration:
				break
			except asyncio.TimeoutError:
				if state.has_partial_payload():
					logger.warning(f"Stream idle timeout ({idle_timeout}s), returning partial")
					state.partial = True
					break
				raise CancelledError(f"Stream idle timeout ({idle_timeout}s) with no content")
			await state.merge_chunk(chunk, on_delta)
		return state.build_message()


def _tool_args_delta(tc_delta: dict) -> str:
	fn_delta = tc_delta.get("function", {})
	return str(fn_delta.get("arguments") or "")


def _tool_preview_meta(tool_calls_map: dict[int, dict], tc_delta: dict) -> dict[str, Any]:
	idx = int(tc_delta.get("index", 0) or 0)
	entry = tool_calls_map.get(idx, {})
	fn = entry.get("function", {})
	return {
		"index": idx,
		"tool_call_id": entry.get("id", ""),
		"tool_name": fn.get("name", ""),
		"arguments_preview": fn.get("arguments", ""),
	}
