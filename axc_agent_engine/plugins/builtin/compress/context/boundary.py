"""Compression boundary persistence. 压缩边界的持久化接口。"""

from __future__ import annotations

import time
from copy import deepcopy
from dataclasses import asdict, field
from typing import Any, Protocol, runtime_checkable
from urllib.parse import quote

from pydantic import ConfigDict
from pydantic.dataclasses import dataclass


@dataclass(config=ConfigDict(strict=True))
class CompressionBoundary:
	"""Persistent summary boundary for one agent/session. 一个 Agent 会话的持久化摘要边界。"""

	agent_name: str
	session_id: str
	summary: str = ""
	round_count: int = 0
	compressed_rounds: int = 0
	last_message_index: int = 0
	buffer: list[str] = field(default_factory=list)
	file_cache: list[dict[str, Any]] = field(default_factory=list)
	tool_summaries: list[str] = field(default_factory=list)
	durable_results: list[str] = field(default_factory=list)
	updated_at: float = field(default_factory=time.time)

	def to_dict(self) -> dict[str, Any]:
		return asdict(self)

	@classmethod
	def from_dict(cls, data: dict[str, Any]) -> CompressionBoundary:
		return cls(**data)


@runtime_checkable
class CompressionBoundaryStore(Protocol):
	async def load(self, agent_name: str, session_id: str) -> CompressionBoundary | None: ...
	async def save(self, boundary: CompressionBoundary) -> None: ...
	async def delete(self, agent_name: str, session_id: str) -> None: ...
	async def clear(self, agent_name: str) -> None: ...


class InMemoryCompressionBoundaryStore:
	"""No-database compression boundary fallback. 无需数据库的压缩边界存储。"""

	def __init__(self) -> None:
		self._items: dict[tuple[str, str], CompressionBoundary] = {}

	async def load(self, agent_name: str, session_id: str) -> CompressionBoundary | None:
		item = self._items.get((agent_name, session_id))
		return deepcopy(item)

	async def save(self, boundary: CompressionBoundary) -> None:
		boundary.updated_at = time.time()
		self._items[(boundary.agent_name, boundary.session_id)] = deepcopy(boundary)

	async def delete(self, agent_name: str, session_id: str) -> None:
		self._items.pop((agent_name, session_id), None)

	async def clear(self, agent_name: str) -> None:
		for key in list(self._items):
			if key[0] == agent_name:
				del self._items[key]


class KVCompressionBoundaryStore:
	"""CompressionBoundaryStore backed by the generic KVStore protocol. 使用 KVStore 持久化压缩边界。"""

	def __init__(self, kv_store: Any, prefix: str = "compress:boundary:") -> None:
		self.kv_store = kv_store
		self.prefix = prefix

	async def load(self, agent_name: str, session_id: str) -> CompressionBoundary | None:
		if not self.kv_store or not session_id:
			return None
		key = self._key(agent_name, session_id)
		data = await self.kv_store.get(key)
		legacy = f"{self.prefix}{agent_name}:{session_id}"
		if data is None and key != legacy:
			data = await self.kv_store.get(legacy)
		if isinstance(data, dict) and (data.get("agent_name"), data.get("session_id")) == (agent_name, session_id):
			return CompressionBoundary.from_dict(data)
		return None

	async def save(self, boundary: CompressionBoundary) -> None:
		if not self.kv_store or not boundary.session_id:
			return
		boundary.updated_at = time.time()
		await self.kv_store.set(self._key(boundary.agent_name, boundary.session_id), boundary.to_dict())

	async def delete(self, agent_name: str, session_id: str) -> None:
		if self.kv_store and session_id:
			key = self._key(agent_name, session_id)
			await self.kv_store.delete(key)
			legacy = f"{self.prefix}{agent_name}:{session_id}"
			if legacy != key:
				data = await self.kv_store.get(legacy)
				if isinstance(data, dict) and (data.get("agent_name"), data.get("session_id")) == (
					agent_name,
					session_id,
				):
					await self.kv_store.delete(legacy)

	async def clear(self, agent_name: str) -> None:
		if not self.kv_store:
			return
		for prefix in (f"{self.prefix}{agent_name}:", f"v2:{self.prefix}{quote(agent_name, safe='')}:"):
			for key in await self.kv_store.list_keys(prefix):
				data = await self.kv_store.get(key)
				if isinstance(data, dict) and data.get("agent_name") == agent_name:
					await self.kv_store.delete(key)

	def _key(self, agent_name: str, session_id: str) -> str:
		encoded = f"{quote(agent_name, safe='')}:{quote(session_id, safe='')}"
		prefix = self.prefix if encoded == f"{agent_name}:{session_id}" else f"v2:{self.prefix}"
		return f"{prefix}{encoded}"
