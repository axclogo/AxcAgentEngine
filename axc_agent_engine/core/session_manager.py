"""SessionManager — 独立的会话管理组件"""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from copy import deepcopy
from typing import TYPE_CHECKING, Any
from uuid import NAMESPACE_URL, uuid5

from axc_agent_engine.core.session import Session

if TYPE_CHECKING:
	from axc_agent_engine.storage.protocols import MessagePersistence


_SESSION_SCOPE_NAMESPACE = uuid5(NAMESPACE_URL, "https://github.com/axclogo/AxcAgentEngine/session-scope")


def _session_scope_id(identity_seed: str) -> str:
	return uuid5(_SESSION_SCOPE_NAMESPACE, identity_seed).hex


class SessionManager:
	"""Creates, retrieves, evicts, and persists sessions within an internal scope.

	中文：在内部作用域中创建、获取、淘汰和持久化会话。
	"""

	def __init__(
		self,
		identity_seed: str,
		max_sessions: int = 1000,
		ttl: int = 3600,
		persistence: MessagePersistence | None = None,
	) -> None:
		self._scope_id = _session_scope_id(identity_seed)
		self._sessions: OrderedDict[str, _SessionEntry] = OrderedDict()
		self._max_sessions = max_sessions
		self._ttl = ttl
		self._lock = asyncio.Lock()
		self._persistence = persistence

	async def get_or_create(self, session_id: str) -> Session:
		"""Load a session on a cache miss or create it.
		获取或创建 Session；缓存未命中时从持久化存储加载。"""
		async with self._lock:
			self._evict_expired()
			if session_id in self._sessions:
				entry = self._sessions[session_id]
				entry.last_access = time.time()
				self._sessions.move_to_end(session_id)
				return entry.session
			session = Session(session_id=session_id)
			if self._persistence:
				messages = await self._persistence.load(self._scope_id, session_id)
				if messages:
					session.messages = messages
			self._sessions[session_id] = _SessionEntry(session=session, last_access=time.time())
			if len(self._sessions) > self._max_sessions:
				self._sessions.popitem(last=False)
			return session

	def restore_context(self, session: Session, message_store: Any) -> None:
		"""Insert session history into the run message store.
		把 Session 上下文恢复到 MessageStore。

		                会把会话历史插入 system prompt 后、用户消息前，保持消息顺序正确。"""
		if session.messages:
			existing = message_store.get_all()
			# Insert history after the system messages. 中文：查找插入点：system 消息之后、user/assistant 消息之前
			insert_idx = 0
			for i, msg in enumerate(existing):
				if msg.get("role") == "system":
					insert_idx = i + 1
				else:
					break
			# Insert session history into the current conversation. 在正确位置插入会话历史
			new_messages = existing[:insert_idx] + session.messages + existing[insert_idx:]
			message_store.set_all(new_messages)

	async def get(self, session_id: str) -> Session | None:
		"""Return the session if present.
		获取 Session，不存在返回 None"""
		async with self._lock:
			entry = self._sessions.get(session_id)
			if entry:
				entry.last_access = time.time()
				return entry.session
			return None

	async def save(self, session_id: str) -> None:
		"""Persist the named session.
		持久化指定 Session"""
		if not self._persistence:
			return
		async with self._lock:
			entry = self._sessions.get(session_id)
			if entry:
				await self._persistence.save(self._scope_id, session_id, entry.session.messages)

	async def replace_history(self, session_id: str, messages: list[dict[str, Any]]) -> int:
		"""Replace a session history in cache and persistence. 同时替换缓存与持久化中的会话历史。

		Returns the previous message count. The persistence write completes before the
		cache changes, so a storage failure leaves the old history intact.
		返回旧消息数；持久化成功后才更新缓存，存储失败时旧历史保持不变。"""
		if not isinstance(session_id, str) or not session_id.strip():
			raise ValueError("session_id must be a nonempty string")
		if not isinstance(messages, list) or not all(isinstance(message, dict) for message in messages):
			raise ValueError("session history must be a list of dicts")
		async with self._lock:
			entry = self._sessions.get(session_id)
			if entry is not None:
				previous = entry.session.messages
			elif self._persistence:
				previous = await self._persistence.load(self._scope_id, session_id) or []
			else:
				previous = []
			if self._persistence:
				await self._persistence.save(self._scope_id, session_id, messages)
			session = entry.session if entry else Session(session_id=session_id)
			session.messages = deepcopy(messages)
			if entry is None:
				self._sessions[session_id] = _SessionEntry(session=session, last_access=time.time())
				if len(self._sessions) > self._max_sessions:
					self._sessions.popitem(last=False)
			else:
				entry.last_access = time.time()
				self._sessions.move_to_end(session_id)
			return len(previous)

	async def remove(self, session_id: str) -> None:
		"""Remove the named session.
		移除指定 Session"""
		async with self._lock:
			self._sessions.pop(session_id, None)
			if self._persistence:
				await self._persistence.delete(self._scope_id, session_id)

	async def clear(self) -> None:
		"""Clear all sessions.
		清空所有 Session"""
		async with self._lock:
			self._sessions.clear()
			if self._persistence:
				await self._persistence.clear(self._scope_id)

	@property
	def count(self) -> int:
		return len(self._sessions)

	def _evict_expired(self) -> None:
		"""Evict expired entries while holding the cache lock.
		淘汰过期 Session（在锁内调用）。持久化由 get_or_create 的调用方保证。"""
		if self._ttl <= 0:
			return
		now = time.time()
		expired = [k for k, v in self._sessions.items() if now - v.last_access > self._ttl]
		for k in expired:
			del self._sessions[k]


class _SessionEntry:
	__slots__ = ("last_access", "session")

	def __init__(self, session: Session, last_access: float) -> None:
		self.session = session
		self.last_access = last_access
