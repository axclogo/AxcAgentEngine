import asyncio

import pytest

from axc_agent_engine.runtime.tasks import cancel_and_wait


@pytest.mark.asyncio
async def test_cancel_and_wait_cancels_pending_task():
	cancelled = False

	async def worker():
		nonlocal cancelled
		try:
			await asyncio.sleep(60)
		except asyncio.CancelledError:
			cancelled = True
			raise

	task = asyncio.create_task(worker())
	await asyncio.sleep(0)

	await cancel_and_wait(task)

	assert task.cancelled()
	assert cancelled is True


@pytest.mark.asyncio
async def test_cancel_and_wait_ignores_completed_task_result():
	async def worker():
		return "done"

	task = asyncio.create_task(worker())
	assert await task == "done"

	await cancel_and_wait(task)

	assert task.done()
	assert task.result() == "done"


@pytest.mark.asyncio
async def test_cancel_and_wait_suppresses_cleanup_errors_after_cancel():
	async def worker():
		try:
			await asyncio.sleep(60)
		except asyncio.CancelledError:
			raise RuntimeError("cleanup failed")

	task = asyncio.create_task(worker())
	await asyncio.sleep(0)

	await cancel_and_wait(task)

	assert task.done()
	assert isinstance(task.exception(), RuntimeError)
