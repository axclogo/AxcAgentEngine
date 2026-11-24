"""Concurrency primitives for engine-level backpressure.

These utilities protect the Agent runtime itself. Transport-level policies such
as per-WebSocket limits still belong to the host.
引擎背压使用的并发控制接口。"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from axc_agent_engine.core.errors import ExecutionTimeoutError


@dataclass(frozen=True)
class ConcurrencyConfig:
	"""Engine-level execution limits. 引擎级执行限制。"""

	max_engine_concurrent_runs: int = 0
	queue_timeout: float = 0.0


class ExecutionLimiter:
	"""Async semaphore wrapper with optional acquire timeout. 支持等待超时的异步信号量。"""

	def __init__(self, limit: int = 0, queue_timeout: float = 0.0, name: str = "execution") -> None:
		self._limit = int(limit or 0)
		self._queue_timeout = float(queue_timeout or 0.0)
		self._name = name
		self._semaphore = asyncio.Semaphore(self._limit) if self._limit > 0 else None

	@asynccontextmanager
	async def slot(self) -> AsyncIterator[None]:
		if self._semaphore is None:
			yield
			return
		try:
			if self._queue_timeout > 0:
				await asyncio.wait_for(self._semaphore.acquire(), timeout=self._queue_timeout)
			else:
				await self._semaphore.acquire()
		except TimeoutError as e:
			raise ExecutionTimeoutError(f"{self._name} concurrency queue timeout after {self._queue_timeout}s") from e
		try:
			yield
		finally:
			self._semaphore.release()

	@property
	def limit(self) -> int:
		return self._limit


class SessionExecutionGate:
	"""Per-session execution gate.

		By default each session runs one turn at a time. Different session IDs do not
		block each other. Empty session IDs are treated as stateless and bypassed.

	按会话控制执行互斥。"""

	def __init__(self, max_per_session: int = 1, queue_timeout: float = 0.0) -> None:
		self._max_per_session = max(0, int(max_per_session or 0))
		self._queue_timeout = float(queue_timeout or 0.0)
		self._guard = asyncio.Lock()
		self._entries: dict[str, _SessionGateEntry] = {}

	@asynccontextmanager
	async def slot(self, session_id: str) -> AsyncIterator[None]:
		if not session_id:
			yield
			return
		entry = await self._get_entry(session_id)
		try:
			async with entry.limiter.slot():
				yield
		finally:
			await self._release_entry(session_id)

	@asynccontextmanager
	async def reset(self, session_id: str) -> AsyncIterator[None]:
		"""Exclude named runs during reset and reject an occupied scope. 重置期间阻止新会话写入，占用时明确报错。"""
		async with self._guard:
			if session_id in self._entries or (not session_id and self._entries):
				raise RuntimeError("Cannot reset a session with active or queued runs")
			yield

	async def _get_entry(self, session_id: str) -> _SessionGateEntry:
		async with self._guard:
			entry = self._entries.get(session_id)
			if entry is None:
				entry = _SessionGateEntry(
					ExecutionLimiter(self._max_per_session, self._queue_timeout, f"session '{session_id}'")
				)
				self._entries[session_id] = entry
			entry.ref_count += 1
			return entry

	async def _release_entry(self, session_id: str) -> None:
		async with self._guard:
			entry = self._entries.get(session_id)
			if not entry:
				return
			entry.ref_count -= 1
			if entry.ref_count <= 0:
				self._entries.pop(session_id, None)


class _SessionGateEntry:
	__slots__ = ("limiter", "ref_count")

	def __init__(self, limiter: ExecutionLimiter) -> None:
		self.limiter = limiter
		self.ref_count = 0


class RateLimiter:
	"""Combined concurrency and fixed-window request limiter. 组合并发数和固定窗口请求数限制。"""

	def __init__(self, max_concurrent: int = 0, requests_per_minute: int = 0, queue_timeout: float = 0.0) -> None:
		self._concurrency = ExecutionLimiter(max_concurrent, queue_timeout, name="provider")
		self._rpm = int(requests_per_minute or 0)
		self._queue_timeout = float(queue_timeout or 0.0)
		self._lock = asyncio.Lock()
		self._timestamps: deque[float] = deque()

	@asynccontextmanager
	async def slot(self) -> AsyncIterator[None]:
		await self._acquire_rpm()
		async with self._concurrency.slot():
			yield

	async def _acquire_rpm(self) -> None:
		if self._rpm <= 0:
			return
		deadline = time.monotonic() + self._queue_timeout if self._queue_timeout > 0 else 0.0
		while True:
			async with self._lock:
				now = time.monotonic()
				while self._timestamps and now - self._timestamps[0] >= 60.0:
					self._timestamps.popleft()
				if len(self._timestamps) < self._rpm:
					self._timestamps.append(now)
					return
				sleep_for = max(0.0, 60.0 - (now - self._timestamps[0]))
			if deadline and time.monotonic() + sleep_for > deadline:
				raise ExecutionTimeoutError(f"provider rate limit queue timeout after {self._queue_timeout}s")
			await asyncio.sleep(sleep_for)
