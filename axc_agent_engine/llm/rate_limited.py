"""Rate-limited LLM provider wrapper.
中文：此文档说明相关引擎组件的行为。"""
from __future__ import annotations

from typing import Any, AsyncIterator

from axc_agent_engine.runtime.concurrency import RateLimiter
from axc_agent_engine.llm.provider import LLMProvider
from axc_agent_engine.core.schema import LLMResponse, LLMStreamChunk
from axc_agent_engine.tools.name_mapping import ToolNameMappingConfig


class RateLimitedProvider:
	"""Wrap an LLMProvider with provider-level backpressure.
中文：此文档说明相关引擎组件的行为。"""

	def __init__(
		self,
		inner: LLMProvider,
		max_concurrent: int = 0,
		requests_per_minute: int = 0,
		queue_timeout: float = 0.0,
	) -> None:
		self._inner = inner
		self._limiter = RateLimiter(max_concurrent, requests_per_minute, queue_timeout)

	@property
	def model(self) -> str:
		return self._inner.model

	@property
	def tool_name_mapping(self) -> ToolNameMappingConfig | None:
		return getattr(self._inner, "tool_name_mapping", None)

	async def chat(self, messages: list[dict], tools: list[dict] | None = None, **kwargs: Any) -> LLMResponse:
		async with self._limiter.slot():
			return await self._inner.chat(messages, tools, **kwargs)

	async def stream(
		self,
		messages: list[dict],
		tools: list[dict] | None = None,
		**kwargs: Any,
	) -> AsyncIterator[LLMStreamChunk]:
		async with self._limiter.slot():
			async for chunk in self._inner.stream(messages, tools, **kwargs):
				yield chunk

	async def ask(self, prompt: str, **kwargs: Any) -> str:
		async with self._limiter.slot():
			return await self._inner.ask(prompt, **kwargs)

	async def close(self) -> None:
		await self._inner.close()

	@property
	def inner(self) -> LLMProvider:
		return self._inner
