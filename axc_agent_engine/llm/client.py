"""OpenAIClient — OpenAI Chat Completions 适配器。

把 OpenAI 专有响应格式转换为标准化 LLMResponse/LLMStreamChunk。
core 层不能看到 OpenAI dict 结构。
"""
import asyncio
import json
import logging
from typing import Any, AsyncIterator

import httpx

from axc_agent_engine.llm.config import LLMConfig
from axc_agent_engine.core.errors import (
	LLMTimeoutError,
	ProviderAuthError,
	ProviderBadRequestError,
	ProviderError,
	RetryableProviderError,
)
from axc_agent_engine.core.schema import LLMMessage, LLMResponse, LLMStreamChunk, LLMUsage
from axc_agent_engine.tools.name_mapping import ToolNameMappingConfig

logger = logging.getLogger(__name__)


class OpenAIErrorMapper:
	"""Maps httpx failures into provider-neutral exceptions.
中文：此文档说明相关引擎组件的行为。"""

	def provider_error_from_status(self, error: httpx.HTTPStatusError, prefix: str) -> ProviderError:
		status = error.response.status_code
		message = f"{prefix} {status}: {error.response.text}"
		if status in (401, 403):
			return ProviderAuthError(message)
		if status in (400, 404, 422):
			return ProviderBadRequestError(message)
		if status == 429 or status >= 500:
			return RetryableProviderError(message)
		return ProviderError(message)


class OpenAIResponseParser:
	"""Converts OpenAI-compatible payloads into engine schema objects.
中文：此文档说明相关引擎组件的行为。"""

	def parse_response(self, raw: dict[str, Any]) -> LLMResponse:
		choices = raw.get("choices", [])
		msg_dict = choices[0].get("message", {}) if choices else {}
		usage_dict = raw.get("usage", {})
		message = LLMMessage(
			role=msg_dict.get("role", "assistant"),
			content=msg_dict.get("content", "") or "",
			tool_calls=msg_dict.get("tool_calls", []),
			raw=msg_dict,
		)
		usage = self._usage(usage_dict)
		return LLMResponse(message=message, usage=usage, raw=raw)

	def parse_chunk(self, raw: dict[str, Any]) -> LLMStreamChunk:
		choices = raw.get("choices", [])
		delta = choices[0].get("delta", {}) if choices else {}
		finish_reason = choices[0].get("finish_reason") if choices else None
		usage = self._usage(raw["usage"]) if raw.get("usage") else None
		content_delta = delta.get("content") or ""
		thinking_delta = delta.get("thinking") or delta.get("reasoning_content") or ""
		tool_call_delta = None
		tc_deltas = delta.get("tool_calls")
		if tc_deltas:
			tool_call_delta = tc_deltas[0] if len(tc_deltas) == 1 else {"tool_calls": tc_deltas}
		return LLMStreamChunk(
			content_delta=content_delta,
			thinking_delta=thinking_delta,
			tool_call_delta=tool_call_delta,
			usage=usage,
			finish_reason=finish_reason,
			raw=raw,
		)

	def _usage(self, usage_dict: dict[str, Any]) -> LLMUsage:
		return LLMUsage(
			input_tokens=usage_dict.get("prompt_tokens", 0),
			output_tokens=usage_dict.get("completion_tokens", 0),
			cached_tokens=usage_dict.get("prompt_tokens_details", {}).get("cached_tokens", 0),
		)


_OPENAI_ERROR_MAPPER = OpenAIErrorMapper()
_OPENAI_RESPONSE_PARSER = OpenAIResponseParser()


class OpenAIClient:
	"""OpenAI-compatible LLM 客户端，返回标准化类型。"""

	def __init__(self, config: LLMConfig) -> None:
		self._config = config
		self._client: httpx.AsyncClient | None = None
		self._client_lock = asyncio.Lock()

	@property
	def model(self) -> str:
		return self._config.model

	@property
	def tool_name_mapping(self) -> ToolNameMappingConfig | None:
		return self._config.tool_name_mapping

	async def _get_client(self) -> httpx.AsyncClient:
		if self._client is not None and not self._client.is_closed:
			return self._client
		async with self._client_lock:
			if self._client is not None and not self._client.is_closed:
				return self._client
			self._client = httpx.AsyncClient(
				base_url=self._config.base_url.rstrip("/"),
				headers={
					"Authorization": f"Bearer {self._config.api_key}",
					"Content-Type": "application/json",
				},
				timeout=httpx.Timeout(self._config.timeout, connect=10.0),
				limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
			)
			return self._client

	def _build_payload(self, messages: list[dict], tools: list[dict] | None = None,
					   **kwargs: Any) -> dict[str, Any]:
		payload: dict[str, Any] = {
			"model": self._config.model,
			"messages": messages,
			"temperature": kwargs.get("temperature", self._config.temperature),
		}
		if self._config.max_tokens:
			payload["max_tokens"] = self._config.max_tokens
		if tools:
			payload["tools"] = tools
		if kwargs.get("parallel_tool_calls") is not None:
			payload["parallel_tool_calls"] = kwargs["parallel_tool_calls"]
		if kwargs.get("thinking") in ("always", "auto"):
			payload["thinking"] = {"type": kwargs["thinking"], "budget_tokens": kwargs.get("thinking_budget", 10000)}
		for key in ("top_p", "stop", "response_format", "presence_penalty", "frequency_penalty", "seed", "user"):
			if key in kwargs:
				payload[key] = kwargs[key]
		if self._config.extra_params:
			for k, v in self._config.extra_params.items():
				if k not in payload:
					payload[k] = v
		return payload

	async def chat(self, messages: list[dict], tools: list[dict] | None = None,
				   **kwargs: Any) -> LLMResponse:
		"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
非流式调用，返回标准化 LLMResponse。"""
		client = await self._get_client()
		payload = self._build_payload(messages, tools, **kwargs)
		try:
			resp = await client.post("/chat/completions", json=payload)
			resp.raise_for_status()
			return _OPENAI_RESPONSE_PARSER.parse_response(resp.json())
		except httpx.TimeoutException as e:
			raise LLMTimeoutError(f"LLM call timed out: {e}") from e
		except httpx.HTTPStatusError as e:
			raise _OPENAI_ERROR_MAPPER.provider_error_from_status(e, "LLM returned error") from e
		except httpx.HTTPError as e:
			raise RetryableProviderError(f"LLM request failed: {e}") from e

	async def stream(self, messages: list[dict], tools: list[dict] | None = None,
					 **kwargs: Any) -> AsyncIterator[LLMStreamChunk]:
		"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
流式调用，产出标准化 LLMStreamChunk。"""
		client = await self._get_client()
		payload = self._build_payload(messages, tools, **kwargs)
		payload["stream"] = True
		try:
			async with client.stream("POST", "/chat/completions", json=payload) as resp:
				resp.raise_for_status()
				async for line in resp.aiter_lines():
					if not line.startswith("data: "):
						continue
					data_str = line[6:]
					if data_str.strip() == "[DONE]":
						break
					try:
						raw = json.loads(data_str)
						yield _OPENAI_RESPONSE_PARSER.parse_chunk(raw)
					except json.JSONDecodeError:
						logger.warning(f"Failed to parse SSE data: {data_str}")
		except httpx.TimeoutException as e:
			raise LLMTimeoutError(f"LLM stream timed out: {e}") from e
		except httpx.HTTPStatusError as e:
			raise _OPENAI_ERROR_MAPPER.provider_error_from_status(e, "LLM returned error") from e
		except httpx.HTTPError as e:
			raise RetryableProviderError(f"LLM request failed: {e}") from e

	async def ask(self, prompt: str, **kwargs: Any) -> str:
		"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
便捷方法：发送单条 prompt，返回文本回复。"""
		response = await self.chat([{"role": "user", "content": prompt}], **kwargs)
		return response.message.content

	async def close(self) -> None:
		if self._client and not self._client.is_closed:
			await self._client.aclose()
			self._client = None
