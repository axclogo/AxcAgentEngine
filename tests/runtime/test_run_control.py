import asyncio

import pytest

from axc_agent_engine.core.context import ExecutionContext
from axc_agent_engine.runtime.run_control import RunControlRegistry, RunControlToken


def test_run_control_rejects_empty_run_id():
	registry = RunControlRegistry()
	with pytest.raises(ValueError, match="run_id"):
		registry.register("", ExecutionContext())
	with pytest.raises(ValueError, match="run_id"):
		registry.cancel("")
	assert registry.cancel("missing") is False


def test_run_control_pre_cancel_marks_late_context():
	registry = RunControlRegistry()
	ctx = ExecutionContext()
	registry._tokens["run-1"] = RunControlToken(run_id="run-1", cancelled=True, reason="already stopped")

	registry.register("run-1", ctx)

	assert ctx.state.cancelled is True
	assert ctx.state.cancel_reason == "already stopped"


def test_run_control_unregister_unknown_is_noop():
	registry = RunControlRegistry()
	registry.unregister("missing", ExecutionContext())
	assert registry.token("missing") is None


async def test_run_control_cancels_context_and_task():
	registry = RunControlRegistry()
	ctx = ExecutionContext()

	async def sleeper():
		await asyncio.sleep(10)

	task = asyncio.create_task(sleeper())
	registry.register("run-1", ctx, task)
	assert registry.cancel("run-1", "user stopped") is True
	await asyncio.sleep(0)

	assert ctx.state.cancelled is True
	assert ctx.state.cancel_reason == "user stopped"
	assert task.cancelled() is True
	registry.unregister("run-1", ctx, task)
	assert registry.token("run-1") is None
