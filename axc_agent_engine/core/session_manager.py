"""SessionManager — 独立的会话管理组件"""
from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from typing import Any, TYPE_CHECKING

from axc_agent_engine.core.session import Session

if TYPE_CHECKING:
	from axc_agent_engine.storage.protocols import MessagePersistence


class SessionManager:
	"""Session 的创建、获取、TTL 淘汰，支持可选持久化"""

	def __init__(self, max_sessions: int = 1000, ttl: int = 3600,
				 persistence: "MessagePersistence | None" = None) -> None:
		self._sessions: OrderedDict[str, _SessionEntry] = OrderedDict()
		self._max_sessions = max_sessions
		self._ttl = ttl
		self._lock = asyncio.Lock()
		self._persistence = persistence

	async def get_or_create(self, session_id: str) -> Session:
		"""获取或创建 Session；缓存未命中时从持久化存储加载。"""
		async with self._lock:
			self._evict_expired()
			if session_id in self._sessions:
				entry = self._sessions[session_id]
				entry.last_access = time.time()
				self._sessions.move_to_end(session_id)
				return entry.session
			session = Session(session_id=session_id)
			if self._persistence:
				messages = await self._persistence.load(session_id)
				if messages:
					session.messages = messages
			self._sessions[session_id] = _SessionEntry(session=session, last_access=time.time())
			if len(self._sessions) > self._max_sessions:
				self._sessions.popitem(last=False)
			return session

	def restore_context(self, session: Session, message_store: Any) -> None:
		"""把 Session 上下文恢复到 MessageStore。

		会把会话历史插入 system prompt 后、用户消息前，保持消息顺序正确。
		"""
		if session.messages:
			existing = message_store.get_all()
			# 查找插入点：system 消息之后、user/assistant 消息之前
			insert_idx = 0
			for i, msg in enumerate(existing):
				if msg.get("role") == "system":
					insert_idx = i + 1
				else:
					break
			# 在正确位置插入会话历史
			new_messages = existing[:insert_idx] + session.messages + existing[insert_idx:]
			message_store.set_all(new_messages)
		# 中断恢复：检查是否存在被中断的 snapshot
		snapshot = session.metadata.get("snapshot")
		if snapshot and isinstance(snapshot, dict) and snapshot.get("status") == "interrupted":
			resume_prompt = (
				f"[系统] 上一次执行被中断（原因：{snapshot.get('interrupt_reason', 'unknown')}），"
				f"已从第 {snapshot.get('current_round', 0)} 轮恢复。请继续。"
			)
			message_store.append({"role": "system", "content": resume_prompt})

	async def get(self, session_id: str) -> Session | None:
		"""获取 Session，不存在返回 None"""
		async with self._lock:
			entry = self._sessions.get(session_id)
			if entry:
				entry.last_access = time.time()
				return entry.session
			return None

	async def save(self, session_id: str) -> None:
		"""持久化指定 Session"""
		if not self._persistence:
			return
		async with self._lock:
			entry = self._sessions.get(session_id)
			if entry:
				await self._persistence.save(session_id, entry.session.messages)

	async def remove(self, session_id: str) -> None:
		"""移除指定 Session"""
		async with self._lock:
			self._sessions.pop(session_id, None)
			if self._persistence:
				await self._persistence.delete(session_id)

	async def clear(self) -> None:
		"""清空所有 Session"""
		async with self._lock:
			self._sessions.clear()

	@property
	def count(self) -> int:
		return len(self._sessions)

	def _evict_expired(self) -> None:
		"""淘汰过期 Session（在锁内调用）。持久化由 get_or_create 的调用方保证。"""
		if self._ttl <= 0:
			return
		now = time.time()
		expired = [k for k, v in self._sessions.items() if now - v.last_access > self._ttl]
		for k in expired:
			del self._sessions[k]


class _SessionEntry:
	__slots__ = ("session", "last_access")
	def __init__(self, session: Session, last_access: float) -> None:
		self.session = session
		self.last_access = last_access
