"""LLMProvider protocol — LLM 客户端抽象接口。

所有 provider 都返回标准化 LLMResponse / LLMStreamChunk。
core 层不能看到 OpenAI/Anthropic 等原始响应格式。
"""
from __future__ import annotations

from typing import Any, AsyncIterator, Protocol, runtime_checkable, TYPE_CHECKING

if TYPE_CHECKING:
	from axc_agent_engine.core.schema import LLMResponse, LLMStreamChunk
	from axc_agent_engine.tools.name_mapping import ToolNameMappingConfig


@runtime_checkable
class LLMProvider(Protocol):
	"""LLM provider 协议，返回标准化响应类型。"""

	@property
	def model(self) -> str: ...

	@property
	def tool_name_mapping(self) -> "ToolNameMappingConfig | None": ...

	async def chat(self, messages: list[dict], tools: list[dict] | None = None,
				   **kwargs: Any) -> "LLMResponse": ...

	async def stream(self, messages: list[dict], tools: list[dict] | None = None,
					 **kwargs: Any) -> "AsyncIterator[LLMStreamChunk]": ...

	async def ask(self, prompt: str, **kwargs: Any) -> str: ...

	async def close(self) -> None: ...


@runtime_checkable
class EmbeddingProvider(Protocol):
	"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
向量操作使用的 Embedding provider 协议。"""

	async def embed(self, texts: list[str]) -> list[list[float]]: ...
	async def close(self) -> None: ...
