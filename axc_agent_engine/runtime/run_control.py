"""Run cancellation control primitives.
中文：运行取消控制基础组件。"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any


@dataclass
class RunControlToken:
	"""Tracks one run and propagates cancellation.
中文：跟踪一次运行并传播取消。"""
	run_id: str
	cancelled: bool = False
	reason: str = ""
	tasks: set[asyncio.Task] = field(default_factory=set)
	contexts: list[Any] = field(default_factory=list)

	def cancel(self, reason: str = "cancelled") -> None:
		self.cancelled = True
		self.reason = reason or "cancelled"
		for ctx in list(self.contexts):
			cancel = getattr(ctx, "cancel", None)
			if callable(cancel):
				cancel(self.reason)
		for task in list(self.tasks):
			if not task.done():
				task.cancel()


class RunControlRegistry:
	"""Process-local run cancellation registry.
中文：进程内运行取消注册表。"""

	def __init__(self) -> None:
		self._tokens: dict[str, RunControlToken] = {}

	def register(self, run_id: str, ctx: Any, task: asyncio.Task | None = None) -> RunControlToken:
		if not run_id:
			raise ValueError("run_id is required")
		token = self._tokens.get(run_id)
		if token is None:
			token = RunControlToken(run_id=run_id)
			self._tokens[run_id] = token
		token.contexts.append(ctx)
		if task:
			token.tasks.add(task)
		if token.cancelled:
			ctx.cancel(token.reason)
		return token

	def unregister(self, run_id: str, ctx: Any, task: asyncio.Task | None = None) -> None:
		token = self._tokens.get(run_id)
		if not token:
			return
		token.contexts = [item for item in token.contexts if item is not ctx]
		if task:
			token.tasks.discard(task)
		if not token.contexts and not token.tasks:
			self._tokens.pop(run_id, None)

	def cancel(self, run_id: str, reason: str = "cancelled") -> bool:
		if not run_id:
			raise ValueError("run_id is required")
		token = self._tokens.get(run_id)
		if token is None:
			return False
		token.cancel(reason)
		return True

	def token(self, run_id: str) -> RunControlToken | None:
		return self._tokens.get(run_id)
