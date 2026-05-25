"""In-memory storage implementations — zero external dependencies, ready to use.

NOTE: InMemoryVectorStore uses linear scan O(n) for search.
Suitable for up to ~1000 entries. For production use, inject an external VectorStore.
"""
from __future__ import annotations

import asyncio
import time
import uuid
from collections import OrderedDict
from typing import AsyncIterator

from axc_agent_engine.utils.math_utils import cosine_similarity


class InMemoryKVStore:
	"""Dict-based in-memory KV store with optional TTL and capacity limit."""

	def __init__(self, max_size: int = 10000, ttl: int = 0) -> None:
		self._data: OrderedDict[str, tuple[dict, float]] = OrderedDict()
		self._max_size = max_size
		self._ttl = ttl  # 0 = no expiry
		self._lock = asyncio.Lock()

	async def get(self, key: str) -> dict | None:
		async with self._lock:
			entry = self._data.get(key)
			if entry is None:
				return None
			value, ts = entry
			if self._ttl > 0 and time.time() - ts > self._ttl:
				del self._data[key]
				return None
			self._data.move_to_end(key)
			return value

	async def set(self, key: str, value: dict) -> None:
		async with self._lock:
			self._data[key] = (value, time.time())
			self._data.move_to_end(key)
			while len(self._data) > self._max_size:
				self._data.popitem(last=False)

	async def delete(self, key: str) -> None:
		async with self._lock:
			self._data.pop(key, None)

	async def list_keys(self, prefix: str = "") -> list[str]:
		async with self._lock:
			now = time.time()
			keys = []
			for k, (_, ts) in list(self._data.items()):
				if self._ttl > 0 and now - ts > self._ttl:
					del self._data[k]
					continue
				if k.startswith(prefix):
					keys.append(k)
			return keys


class InMemoryMessagePersistence:
	"""Dict-based in-memory message persistence with capacity limit."""

	def __init__(self, max_sessions: int = 1000) -> None:
		self._store: OrderedDict[str, list[dict]] = OrderedDict()
		self._max_sessions = max_sessions
		self._lock = asyncio.Lock()

	async def save(self, session_id: str, messages: list[dict]) -> None:
		async with self._lock:
			self._store[session_id] = list(messages)
			self._store.move_to_end(session_id)
			while len(self._store) > self._max_sessions:
				self._store.popitem(last=False)

	async def load(self, session_id: str) -> list[dict]:
		async with self._lock:
			return list(self._store.get(session_id, []))

	async def delete(self, session_id: str) -> None:
		async with self._lock:
			self._store.pop(session_id, None)


class InMemorySpanStore:
	"""List-based in-memory span store with capacity limit."""

	def __init__(self, max_spans: int = 50000) -> None:
		self._spans: list[dict] = []
		self._max_spans = max_spans
		self._lock = asyncio.Lock()

	async def save_span(self, span: dict) -> None:
		async with self._lock:
			self._spans.append(span)
			if len(self._spans) > self._max_spans:
				self._spans = self._spans[-self._max_spans:]

	async def query_by_trace(self, trace_id: str) -> list[dict]:
		async with self._lock:
			return [s for s in self._spans if s.get("trace_id") == trace_id]

	async def query_by_session(self, session_id: str, limit: int = 50) -> list[dict]:
		async with self._lock:
			results = [s for s in self._spans if s.get("session_id") == session_id]
			return results[-limit:]


class InMemoryVectorStore:
	"""Cosine-similarity based in-memory vector store with capacity limit.

	NOTE: Linear scan O(n). Suitable for up to ~1000 entries.
	For production, inject an external VectorStore implementation.
	"""

	def __init__(self, max_entries: int = 1000) -> None:
		self._entries: list[dict] = []
		self._max_entries = max_entries
		self._lock = asyncio.Lock()

	async def add(self, texts: list[str], embeddings: list[list[float]], metadata: list[dict]) -> list[str]:
		async with self._lock:
			ids = []
			for text, emb, meta in zip(texts, embeddings, metadata):
				entry_id = uuid.uuid4().hex[:12]
				self._entries.append({"id": entry_id, "text": text, "embedding": emb, "metadata": meta})
				ids.append(entry_id)
			# Evict oldest if over capacity
			if len(self._entries) > self._max_entries:
				self._entries = self._entries[-self._max_entries:]
			return ids

	async def search(self, embedding: list[float], top_k: int = 5) -> list[dict]:
		async with self._lock:
			scored = []
			for entry in self._entries:
				sim = cosine_similarity(embedding, entry["embedding"])
				scored.append((sim, entry))
			scored.sort(key=lambda x: x[0], reverse=True)
			return [
				{"id": e["id"], "text": e["text"], "score": s, "metadata": e["metadata"]}
				for s, e in scored[:top_k] if s > 0
			]

	async def delete(self, ids: list[str]) -> None:
		async with self._lock:
			id_set = set(ids)
			self._entries = [e for e in self._entries if e["id"] not in id_set]


class InMemoryMessageBus:
	"""asyncio.Queue-based in-memory message bus with graceful shutdown."""

	def __init__(self, max_idle_rounds: int = 30) -> None:
		self._channels: dict[str, list[asyncio.Queue]] = {}
		self._closed = False
		self._max_idle_rounds = max_idle_rounds

	async def publish(self, channel: str, message: dict) -> None:
		if channel in self._channels:
			for queue in self._channels[channel]:
				await queue.put(message)

	async def subscribe(self, channel: str) -> AsyncIterator[dict]:
		if channel not in self._channels:
			self._channels[channel] = []
		queue: asyncio.Queue = asyncio.Queue()
		self._channels[channel].append(queue)
		idle_count = 0
		try:
			while not self._closed:
				try:
					msg = await asyncio.wait_for(queue.get(), timeout=60)
					idle_count = 0
					yield msg
				except asyncio.TimeoutError:
					idle_count += 1
					if idle_count >= self._max_idle_rounds:
						return
					continue
		finally:
			self._channels[channel].remove(queue)

	async def close(self) -> None:
		"""Signal all subscribers to stop."""
		self._closed = True

	async def request(self, channel: str, message: dict, timeout: float = 30) -> dict:
		reply_channel = f"_reply_{uuid.uuid4().hex[:8]}"
		message["_reply_to"] = reply_channel
		reply_queue: asyncio.Queue = asyncio.Queue()
		if reply_channel not in self._channels:
			self._channels[reply_channel] = []
		self._channels[reply_channel].append(reply_queue)
		try:
			await self.publish(channel, message)
			return await asyncio.wait_for(reply_queue.get(), timeout=timeout)
		finally:
			self._channels[reply_channel].remove(reply_queue)
			if not self._channels[reply_channel]:
				del self._channels[reply_channel]
