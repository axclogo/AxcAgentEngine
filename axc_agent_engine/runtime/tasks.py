"""Async task cleanup helpers.
中文：异步任务清理辅助函数。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, TypeVar


async def cancel_and_wait(task: asyncio.Task[Any] | None) -> None:
	"""Cancel a cleanup task and suppress its terminal exception.
	中文：取消清理任务并吞掉任务结束异常。"""
	if not task:
		return
	if not task.done():
		task.cancel()
	await asyncio.gather(task, return_exceptions=True)


Item = TypeVar("Item")


@asynccontextmanager
async def managed_stream(stream: AsyncIterator[Item]) -> AsyncIterator[AsyncIterator[Item]]:
	"""Propagate consumer shutdown to streams that support aclose. 将关闭通知传递给支持关闭的流。"""
	try:
		yield stream
	finally:
		close = getattr(stream, "aclose", None)
		if close is not None:
			await close()


async def stream_until(
	stream: AsyncIterator[Item], deadline: float | None, expired: Item
) -> AsyncGenerator[Item, None]:
	"""Time producer awaits without cancelling the consumer. 只对生产端等待计时，避免取消消费方。"""
	loop = asyncio.get_running_loop()
	async with managed_stream(stream):
		while deadline is None or loop.time() < deadline:
			timer = asyncio.timeout_at(deadline)
			try:
				async with timer:
					item = await anext(stream)
			except StopAsyncIteration:
				if timer.expired():
					break
				return
			except TimeoutError:
				if not timer.expired():
					raise
				break
			if timer.expired():
				break
			yield item
	yield expired
