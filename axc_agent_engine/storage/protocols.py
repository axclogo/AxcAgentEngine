"""Storage protocols used by the engine without requiring external
storage dependencies.
存储 Protocol 定义 — 引擎零外部存储依赖。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol, runtime_checkable

from axc_agent_engine.observability.audit import AuditSink
from axc_agent_engine.runtime.checkpoint import CheckpointStore
from axc_agent_engine.storage.artifact_store import ArtifactStore

__all__ = [
	"ArtifactStore",
	"AuditSink",
	"CheckpointStore",
	"KVStore",
	"MessageBus",
	"MessagePersistence",
	"SpanStore",
	"VectorStore",
]


@runtime_checkable
class KVStore(Protocol):
	"""Generic key-value storage protocol.
	通用键值存储。"""

	async def get(self, key: str) -> dict | None: ...
	async def set(self, key: str, value: dict) -> None: ...
	async def delete(self, key: str) -> None: ...
	async def list_keys(self, prefix: str = "") -> list[str]: ...


@runtime_checkable
class MessagePersistence(Protocol):
	"""Session message persistence isolated by an internal scope ID.
	按内部作用域隔离的会话消息持久化。"""

	async def save(self, scope_id: str, session_id: str, messages: list[dict]) -> None: ...
	async def load(self, scope_id: str, session_id: str) -> list[dict]: ...
	async def delete(self, scope_id: str, session_id: str) -> None: ...
	async def clear(self, scope_id: str) -> None: ...


@runtime_checkable
class SpanStore(Protocol):
	"""Trace span storage protocol.
	链路追踪 span 存储。"""

	async def save_span(self, span: dict) -> None: ...
	async def query_by_trace(self, trace_id: str) -> list[dict]: ...
	async def query_by_session(self, session_id: str, limit: int = 50) -> list[dict]: ...


@runtime_checkable
class VectorStore(Protocol):
	"""Vector storage protocol.
	向量存储接口。"""

	async def add(self, texts: list[str], embeddings: list[list[float]], metadata: list[dict]) -> list[str]: ...
	async def search(self, embedding: list[float], top_k: int = 5) -> list[dict]: ...
	async def delete(self, ids: list[str]) -> None: ...


@runtime_checkable
class MessageBus(Protocol):
	"""Agent 间异步通信。

	English: Asynchronous message bus protocol between agents.
	"""

	async def publish(self, channel: str, message: dict) -> None: ...
	async def subscribe(self, channel: str) -> AsyncIterator[dict]: ...
	async def request(self, channel: str, message: dict, timeout: float = 30) -> dict: ...
