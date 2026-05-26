"""InMemoryResultStore — stores large tool outputs for paged retrieval.
中文：此文档说明相关引擎组件的行为。"""
from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from typing import Any

from axc_agent_engine.tools.tool_output import ArtifactRef, generate_artifact_id


class InMemoryResultStore:
	"""In-memory implementation of ResultStore protocol.

	Stores artifacts in an OrderedDict with TTL, entry-count, and byte-size
	eviction. It is intended for local development and tests; production systems
	should inject a durable ResultStore implementation.
	
中文：此文档说明相关引擎组件的行为。"""

	def __init__(self, max_entries: int = 500, max_bytes: int = 50 * 1024 * 1024, ttl: int = 3600) -> None:
		self._store: OrderedDict[str, dict[str, Any]] = OrderedDict()
		self._max_entries = max_entries
		self._max_bytes = max_bytes
		self._ttl = ttl
		self._total_bytes = 0
		self._lock = asyncio.Lock()

	async def put(self, content: str | bytes, metadata: dict[str, Any] | None = None) -> ArtifactRef:
		"""Store content and return an ArtifactRef.
中文：此文档说明相关引擎组件的行为。"""
		artifact_id = generate_artifact_id()
		if isinstance(content, bytes):
			content_str = content.decode("utf-8", errors="replace")
			kind = "binary"
			size = len(content)
		else:
			content_str = content
			kind = "text"
			size = len(content.encode("utf-8"))
		meta = dict(metadata or {})
		kind = meta.pop("kind", kind)
		now = time.time()
		async with self._lock:
			self._evict_expired(now)
			self._store[artifact_id] = {
				"content": content_str,
				"metadata": meta,
				"kind": kind,
				"size": size,
				"created_at": now,
				"accessed_at": now,
			}
			self._total_bytes += size
			self._evict_to_limits()
		return ArtifactRef(id=artifact_id, kind=kind, size=size, metadata=meta)

	async def get(self, artifact_id: str, offset: int = 0, limit: int = 4000) -> str:
		"""Retrieve content by artifact_id with pagination.
中文：此文档说明相关引擎组件的行为。"""
		async with self._lock:
			entry = self._get_live_entry(artifact_id)
			if entry is None:
				return ""
			entry["accessed_at"] = time.time()
			self._store.move_to_end(artifact_id)
			content = entry["content"]
			safe_offset = max(0, offset)
			safe_limit = max(0, limit)
			return content[safe_offset:safe_offset + safe_limit]

	async def search(self, artifact_id: str, query: str) -> list[dict[str, Any]]:
		"""Search within an artifact's content for lines matching query.
中文：此文档说明相关引擎组件的行为。"""
		async with self._lock:
			entry = self._get_live_entry(artifact_id)
			if entry is None:
				return []
			entry["accessed_at"] = time.time()
			self._store.move_to_end(artifact_id)
			content = entry["content"]
		results = []
		for i, line in enumerate(content.split("\n")):
			if query.lower() in line.lower():
				results.append({"line": i + 1, "text": line})
				if len(results) >= 20:
					break
		return results

	async def delete(self, artifact_id: str) -> None:
		"""Delete an artifact if it exists.
中文：此文档说明相关引擎组件的行为。"""
		async with self._lock:
			self._delete_unlocked(artifact_id)

	def has(self, artifact_id: str) -> bool:
		"""Check if artifact exists.
中文：此文档说明相关引擎组件的行为。"""
		return self._get_live_entry(artifact_id) is not None

	def stats(self) -> dict[str, Any]:
		"""Return current store capacity stats.
中文：此文档说明相关引擎组件的行为。"""
		return {
			"entries": len(self._store),
			"max_entries": self._max_entries,
			"total_bytes": self._total_bytes,
			"max_bytes": self._max_bytes,
			"ttl": self._ttl,
		}

	def _get_live_entry(self, artifact_id: str) -> dict[str, Any] | None:
		entry = self._store.get(artifact_id)
		if entry is None:
			return None
		if self._is_expired(entry, time.time()):
			self._delete_unlocked(artifact_id)
			return None
		return entry

	def _is_expired(self, entry: dict[str, Any], now: float) -> bool:
		return self._ttl > 0 and now - entry["created_at"] > self._ttl

	def _evict_expired(self, now: float) -> None:
		if self._ttl <= 0:
			return
		for artifact_id, entry in list(self._store.items()):
			if self._is_expired(entry, now):
				self._delete_unlocked(artifact_id)

	def _evict_to_limits(self) -> None:
		while self._max_entries > 0 and len(self._store) > self._max_entries:
			oldest_key = next(iter(self._store))
			self._delete_unlocked(oldest_key)
		while self._max_bytes > 0 and self._total_bytes > self._max_bytes and self._store:
			oldest_key = next(iter(self._store))
			self._delete_unlocked(oldest_key)

	def _delete_unlocked(self, artifact_id: str) -> None:
		entry = self._store.pop(artifact_id, None)
		if entry is not None:
			self._total_bytes = max(0, self._total_bytes - entry.get("size", 0))
