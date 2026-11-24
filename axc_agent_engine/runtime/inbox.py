"""Ordered input for a single active run. 保存一次运行收到的有序用户输入。"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from contextlib import ExitStack
from typing import TYPE_CHECKING

if TYPE_CHECKING:
	from axc_agent_engine.core.message_store import MessageStore


def receipt(entries: dict[str, str], consumed: int, message_id: str, text: str, active: bool) -> str:
	if message_id not in entries:
		return "closed"
	if entries[message_id] != text:
		raise ValueError("message_id was already used with different text")
	if list(entries).index(message_id) < consumed:
		return "processed"
	return "accepted" if active else "unprocessed"


class RunInbox:
	def __init__(self, messages: MessageStore, persist: Callable[[dict | None], Awaitable[None]]) -> None:
		self.messages = messages
		self.persist = persist
		self.lock = asyncio.Lock()
		self.entries: dict[str, str] = {}
		self.consumed = 0
		self.open = True

	async def send(self, message_id: str, text: str) -> str:
		if not message_id.strip() or not text.strip():
			raise ValueError("message_id and text must be nonempty")
		async with self.lock:
			if message_id in self.entries or not self.open:
				return receipt(self.entries, self.consumed, message_id, text, self.open)
			if not self._enqueue(message_id, text):
				return "full"
			with ExitStack() as rollback:
				rollback.callback(self.entries.pop, message_id)
				await self.persist(None)
				rollback.pop_all()
			return "accepted"

	def _enqueue(self, message_id: str, text: str) -> bool:
		if len(self.entries) - self.consumed >= 128:
			return False
		self.entries[message_id] = text
		return True

	def pending(self) -> list[str]:
		if self.consumed == len(self.entries):
			return []
		return list(self.entries.values())[self.consumed :]

	async def peek(self) -> list[str]:
		async with self.lock:
			return self.pending()

	async def take(self, *, finish: bool = False, count: int | None = None, progress: dict | None = None) -> list[str]:
		async with self.lock:
			pending = self.pending()[:count]
			if pending:
				with ExitStack() as rollback:
					rollback.callback(self.messages.rollback, self.messages.count)
					rollback.callback(setattr, self, "consumed", self.consumed)
					for text in pending:
						self.messages.append({"role": "user", "content": text})
					self.consumed += len(pending)
					await self.persist(progress)
					rollback.pop_all()
			elif finish:
				self.open = False
			return pending

	def snapshot(self) -> dict:
		return {"entries": list(self.entries.items()), "consumed": self.consumed}

	def restore(self, snapshot: dict, *, message: str = "") -> None:
		if not isinstance(snapshot, dict):
			raise TypeError("Invalid checkpoint inbox")
		entries = snapshot.get("entries", [])
		consumed = snapshot.get("consumed", 0)
		if not isinstance(entries, list) or not all(
			isinstance(pair, (list, tuple))
			and len(pair) == 2
			and all(isinstance(part, str) and part.strip() for part in pair)
			for pair in entries
		):
			raise ValueError("Invalid checkpoint inbox entries")
		items = dict(entries)
		if type(consumed) is not int or len(entries) != len(items) or not 0 <= consumed <= len(items):
			raise ValueError("Invalid checkpoint inbox cursor or duplicate message IDs")
		self.entries, self.consumed = items, consumed
		if message:
			if not message.strip():
				raise ValueError("Resume message must contain text")
			if not self._enqueue(f"resume-{uuid.uuid4().hex}", message):
				raise ValueError("Run inbox is full. Resume without additional text first")
