"""Async task cleanup helpers.
中文：异步任务清理辅助函数。"""
from __future__ import annotations

import asyncio
from typing import Any


async def cancel_and_wait(task: asyncio.Task[Any] | None) -> None:
	"""Cancel a cleanup task and suppress its terminal exception.
中文：取消清理任务并吞掉任务结束异常。"""
	if not task or task.done():
		return
	task.cancel()
	try:
		await task
	except (asyncio.CancelledError, Exception):
		pass
