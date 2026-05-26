"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
存储 Protocol 定义 — 引擎零外部存储依赖。

English: Storage protocols used by the engine without requiring external
storage dependencies."""
from __future__ import annotations

from typing import Any, AsyncIterator, Protocol, runtime_checkable


@runtime_checkable
class KVStore(Protocol):
	"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
通用键值存储。

	English: Generic key-value storage protocol.
	"""
	async def get(self, key: str) -> dict | None: ...
	async def set(self, key: str, value: dict) -> None: ...
	async def delete(self, key: str) -> None: ...
	async def list_keys(self, prefix: str = "") -> list[str]: ...


@runtime_checkable
class MessagePersistence(Protocol):
	"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
会话消息持久化。

	English: Session message persistence protocol.
	"""
	async def save(self, session_id: str, messages: list[dict]) -> None: ...
	async def load(self, session_id: str) -> list[dict]: ...
	async def delete(self, session_id: str) -> None: ...


@runtime_checkable
class SpanStore(Protocol):
	"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
链路追踪 span 存储。

	English: Trace span storage protocol.
	"""
	async def save_span(self, span: dict) -> None: ...
	async def query_by_trace(self, trace_id: str) -> list[dict]: ...
	async def query_by_session(self, session_id: str, limit: int = 50) -> list[dict]: ...


@runtime_checkable
class VectorStore(Protocol):
	"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
向量存储接口。

	English: Vector storage protocol.
	"""
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


@runtime_checkable
class ResultStore(Protocol):
	"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
大型工具结果存储 — 支持分页读取和搜索。

	English: Large tool-result storage with paged reads and search.
	"""
	async def put(self, content: str | bytes, metadata: dict[str, Any] | None = None) -> Any: ...
	async def get(self, artifact_id: str, offset: int = 0, limit: int = 4000) -> str: ...
	async def search(self, artifact_id: str, query: str) -> list[dict[str, Any]]: ...


@runtime_checkable
class AuditSink(Protocol):
	"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
结构化审计事件存储。

	English: Structured audit event sink protocol.
	"""
	async def record(self, event: Any) -> None: ...


@runtime_checkable
class CheckpointStore(Protocol):
	"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
执行 checkpoint 存储。

	English: Execution checkpoint storage protocol.
	"""
	async def save(self, checkpoint: Any) -> None: ...
	async def latest(self, run_id: str) -> Any | None: ...
	async def list(self, run_id: str) -> list[Any]: ...
	async def list_runs(self, status: str | None = None, kind: str | None = None) -> list[str]: ...
	async def delete_run(self, run_id: str) -> None: ...
