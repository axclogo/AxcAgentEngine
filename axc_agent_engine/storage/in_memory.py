"""In-memory storage implementations — zero external dependencies, ready to use.

NOTE: InMemoryVectorStore uses linear scan O(n) for search.
Suitable for up to ~1000 entries. For production use, inject an external VectorStore.
无需外部依赖的内存存储实现。"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections import OrderedDict
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager

from axc_agent_engine.utils.math_utils import cosine_similarity


class InMemoryKVStore:
	"""Dict-based in-memory KV store with optional TTL and capacity limit. 支持有效期和容量限制的内存键值存储。"""

	def __init__(self, max_size: int = 10000, ttl: int = 0) -> None:
		self._data: OrderedDict[str, tuple[dict, float]] = OrderedDict()
		self._max_size = max_size
		self._ttl = ttl  # English: 0 means no expiry. 中文：0 表示不过期。
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
	"""Scope-isolated in-memory message persistence with capacity limit.
	中文：按内部作用域隔离且带容量限制的内存会话持久化。"""

	def __init__(self, max_sessions: int = 1000) -> None:
		self._store: OrderedDict[tuple[str, str], list[dict]] = OrderedDict()
		self._max_sessions = max_sessions
		self._lock = asyncio.Lock()

	async def save(self, scope_id: str, session_id: str, messages: list[dict]) -> None:
		async with self._lock:
			key = (scope_id, session_id)
			self._store[key] = list(messages)
			self._store.move_to_end(key)
			while len(self._store) > self._max_sessions:
				self._store.popitem(last=False)

	async def load(self, scope_id: str, session_id: str) -> list[dict]:
		async with self._lock:
			return list(self._store.get((scope_id, session_id), []))

	async def delete(self, scope_id: str, session_id: str) -> None:
		async with self._lock:
			self._store.pop((scope_id, session_id), None)

	async def clear(self, scope_id: str) -> None:
		async with self._lock:
			keys = [key for key in self._store if key[0] == scope_id]
			for key in keys:
				del self._store[key]


class InMemorySpanStore:
	"""List-based in-memory span store with capacity limit. 有容量限制的内存链路跨度列表。"""

	def __init__(self, max_spans: int = 50000) -> None:
		self._spans: list[dict] = []
		self._max_spans = max_spans
		self._lock = asyncio.Lock()

	async def save_span(self, span: dict) -> None:
		async with self._lock:
			self._spans.append(span)
			if len(self._spans) > self._max_spans:
				self._spans = self._spans[-self._max_spans :]

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

	有容量限制的余弦相似度内存向量存储。"""

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
			# Evict oldest if over capacity 超出容量时移除最早的记录。
			if len(self._entries) > self._max_entries:
				self._entries = self._entries[-self._max_entries :]
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
				for s, e in scored[:top_k]
				if s > 0
			]

	async def delete(self, ids: list[str]) -> None:
		async with self._lock:
			id_set = set(ids)
			self._entries = [e for e in self._entries if e["id"] not in id_set]


class InMemoryMessageBus:
	"""asyncio.Queue-based in-memory message bus with graceful shutdown. 基于异步队列并支持有序关闭的内存消息总线。"""

	def __init__(self, max_idle_rounds: int | None = None) -> None:
		self._channels: dict[str, list[asyncio.Queue]] = {}
		self._closed = False
		self._idle_timeout = None if max_idle_rounds is None else max(1, max_idle_rounds) * 60

	@contextmanager
	def _listen(self, channel: str) -> Iterator[asyncio.Queue]:
		queue: asyncio.Queue = asyncio.Queue()
		listeners = self._channels.setdefault(channel, [])
		listeners.append(queue)
		try:
			yield queue
		finally:
			listeners.remove(queue)
			if not listeners:
				del self._channels[channel]

	async def publish(self, channel: str, message: dict) -> None:
		if not self._closed:
			for queue in self._channels.get(channel, ()):
				queue.put_nowait(message)

	async def subscribe(self, channel: str) -> AsyncIterator[dict]:
		with self._listen(channel) as queue:
			while not self._closed:
				try:
					async with asyncio.timeout(self._idle_timeout):
						message = await queue.get()
				except TimeoutError:
					return
				if message is None:
					return
				yield message

	async def close(self) -> None:
		"""Wake idle readers and pending requests. 唤醒空闲订阅和等待回复的请求。"""
		if not self._closed:
			self._closed = True
			for listeners in self._channels.values():
				for queue in listeners:
					queue.put_nowait(None)

	async def request(self, channel: str, message: dict, timeout: float = 30) -> dict:
		if self._closed:
			raise RuntimeError("Message bus is closed")
		reply_channel = f"_reply_{uuid.uuid4().hex}"
		with self._listen(reply_channel) as queue:
			await self.publish(channel, {**message, "_reply_to": reply_channel})
			async with asyncio.timeout(timeout):
				reply = await queue.get()
			if reply is None:
				raise RuntimeError("Message bus is closed")
			return reply
