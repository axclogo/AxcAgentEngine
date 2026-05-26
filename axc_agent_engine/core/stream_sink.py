"""Explicit streaming event sinks.
中文：此文档说明相关引擎组件的行为。"""
from __future__ import annotations

import asyncio
from typing import Protocol

from axc_agent_engine.core.events import Event


class StreamSink(Protocol):
	async def emit(self, event: Event) -> None: ...


class QueueStreamSink:
	def __init__(self, queue: "asyncio.Queue[Event | None]") -> None:
		self._queue = queue

	async def emit(self, event: Event) -> None:
		await self._queue.put(event)
