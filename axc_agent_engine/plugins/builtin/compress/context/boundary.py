"""Compression boundary persistence."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass
class CompressionBoundary:
	"""Persistent summary boundary for one agent/session."""

	agent_name: str
	session_id: str
	summary: str = ""
	round_count: int = 0
	compressed_rounds: int = 0
	last_message_index: int = 0
	buffer: list[str] = field(default_factory=list)
	file_cache: list[dict[str, Any]] = field(default_factory=list)
	tool_summaries: list[str] = field(default_factory=list)
	updated_at: float = field(default_factory=time.time)

	def to_dict(self) -> dict[str, Any]:
		return {
			"agent_name": self.agent_name,
			"session_id": self.session_id,
			"summary": self.summary,
			"round_count": self.round_count,
			"compressed_rounds": self.compressed_rounds,
			"last_message_index": self.last_message_index,
			"buffer": list(self.buffer),
			"file_cache": list(self.file_cache),
			"tool_summaries": list(self.tool_summaries),
			"updated_at": self.updated_at,
		}

	@classmethod
	def from_dict(cls, data: dict[str, Any]) -> "CompressionBoundary":
		return cls(
			agent_name=str(data.get("agent_name") or ""),
			session_id=str(data.get("session_id") or ""),
			summary=str(data.get("summary") or ""),
			round_count=int(data.get("round_count", 0)),
			compressed_rounds=int(data.get("compressed_rounds", 0)),
			last_message_index=int(data.get("last_message_index", 0)),
			buffer=[str(item) for item in data.get("buffer", []) if str(item)],
			file_cache=[dict(item) for item in data.get("file_cache", []) if isinstance(item, dict)],
			tool_summaries=[str(item) for item in data.get("tool_summaries", []) if str(item)],
			updated_at=float(data.get("updated_at", time.time())),
		)


@runtime_checkable
class CompressionBoundaryStore(Protocol):
	async def load(self, agent_name: str, session_id: str) -> CompressionBoundary | None: ...
	async def save(self, boundary: CompressionBoundary) -> None: ...
	async def delete(self, agent_name: str, session_id: str) -> None: ...


class InMemoryCompressionBoundaryStore:
	"""No-database compression boundary fallback."""

	def __init__(self) -> None:
		self._items: dict[tuple[str, str], CompressionBoundary] = {}

	async def load(self, agent_name: str, session_id: str) -> CompressionBoundary | None:
		item = self._items.get((agent_name, session_id))
		return CompressionBoundary.from_dict(item.to_dict()) if item else None

	async def save(self, boundary: CompressionBoundary) -> None:
		boundary.updated_at = time.time()
		self._items[(boundary.agent_name, boundary.session_id)] = CompressionBoundary.from_dict(boundary.to_dict())

	async def delete(self, agent_name: str, session_id: str) -> None:
		self._items.pop((agent_name, session_id), None)


class KVCompressionBoundaryStore:
	"""CompressionBoundaryStore backed by the generic KVStore protocol."""

	def __init__(self, kv_store: Any, prefix: str = "compress:boundary:") -> None:
		self.kv_store = kv_store
		self.prefix = prefix

	async def load(self, agent_name: str, session_id: str) -> CompressionBoundary | None:
		if not self.kv_store or not session_id:
			return None
		data = await self.kv_store.get(self._key(agent_name, session_id))
		return CompressionBoundary.from_dict(data) if isinstance(data, dict) else None

	async def save(self, boundary: CompressionBoundary) -> None:
		if not self.kv_store or not boundary.session_id:
			return
		boundary.updated_at = time.time()
		await self.kv_store.set(self._key(boundary.agent_name, boundary.session_id), boundary.to_dict())

	async def delete(self, agent_name: str, session_id: str) -> None:
		if self.kv_store and session_id:
			await self.kv_store.delete(self._key(agent_name, session_id))

	def _key(self, agent_name: str, session_id: str) -> str:
		return f"{self.prefix}{agent_name}:{session_id}"
