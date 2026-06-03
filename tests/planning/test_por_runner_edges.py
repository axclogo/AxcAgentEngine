from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from axc_agent_engine.core.context import ExecutionConfig, ExecutionContext, ExecutionServices
from axc_agent_engine.core.events import Event, EventType
from axc_agent_engine.core.message_store import MessageStore
from axc_agent_engine.core.plugin_manager import PluginManager
from axc_agent_engine.core.schema import StepStatus
from axc_agent_engine.planning.planner import Plan, PlanStep
from axc_agent_engine.planning.por_runner import (
	PORExecutionService,
	PORRunner,
	StepExecutionResult,
	StepRunner,
	_checkpoint_step_id,
)
from axc_agent_engine.planning.checkpointing import plan_to_state
from axc_agent_engine.planning.runtime import PlanRuntime
from axc_agent_engine.runtime.checkpoint import InMemoryCheckpointStore, CheckpointStatus
from axc_agent_engine.tools.registry import ToolRegistry


def _runtime(ctx: ExecutionContext | None = None) -> PlanRuntime:
	return PlanRuntime(
		llm_caller=MagicMock(),
		message_store=MessageStore(),
		registry=ToolRegistry(),
		plugin_manager=PluginManager([]),
		ctx=ctx or ExecutionContext(config=ExecutionConfig(stream=False, max_rounds=20)),
	)


@pytest.mark.asyncio
async def test_step_runner_shared_handles_round_limit_exception_no_result_and_failed_turn(monkeypatch):
	plan = Plan(goal="goal", steps=[PlanStep(step_id=1, description="step")])
	ctx = ExecutionContext(config=ExecutionConfig(stream=False, max_rounds=0))
	runner = StepRunner(_runtime(ctx))

	limit_result, limit_events = await runner.run_shared(plan, plan.steps[0])
	assert limit_result == "步骤超过总轮次限制"
	assert limit_events == []
	assert plan.steps[0].status == StepStatus.FAILED

	class RaisingKernel:
		async def run_step(self, **kwargs):
			raise RuntimeError("llm down")
			yield

	class EmptyKernel:
		async def run_step(self, **kwargs):
			if False:
				yield

	class FailedKernel:
		async def run_step(self, **kwargs):
			from axc_agent_engine.core.react_loop import ReActTurnResult
			yield Event.delta("visible")
			yield ReActTurnResult(message={"role": "assistant"}, content="failed content", failed=True)

	ctx = ExecutionContext(config=ExecutionConfig(stream=False, max_rounds=20))
	runner = StepRunner(_runtime(ctx))
	runner._shared_kernel = RaisingKernel()
	assert (await runner.run_shared(plan, plan.steps[0]))[0] == "LLM 调用失败：llm down"
	runner._shared_kernel = EmptyKernel()
	assert (await runner.run_shared(plan, plan.steps[0]))[0] == "LLM 调用失败：没有结果"
	runner._shared_kernel = FailedKernel()
	result, events = await runner.run_shared(plan, plan.steps[0])
	assert result == "failed content"
	assert events[0].type == EventType.STREAM_DELTA


@pytest.mark.asyncio
async def test_step_runner_isolated_merge_and_context_summary(monkeypatch):
	completed = PlanStep(step_id=1, description="done", status=StepStatus.DONE, result="r1")
	step = PlanStep(step_id=2, description="next")
	plan = Plan(goal="goal", steps=[completed, step])
	ctx = ExecutionContext(config=ExecutionConfig(stream=False, max_rounds=20, system_prompt="sys"))
	runner = StepRunner(_runtime(ctx))

	child_messages, child_ctx = runner._isolated_context(plan, step)
	failed = runner._isolated_result(step, "bad", [Event.delta("x")], child_ctx, failed=True)
	runner.merge(StepExecutionResult(step_id=2, result="ok", input_tokens=3, output_tokens=4, isolated=True))

	assert any("已完成步骤" in msg["content"] for msg in child_messages.get_all())
	assert failed.isolated is True
	assert step.status == StepStatus.FAILED
	assert ctx.state.total_input_tokens == 3
	assert ctx.state.total_output_tokens == 4
	assert "POR 步骤 2 结果" in runner._messages.get_all()[-1]["content"]


@pytest.mark.asyncio
async def test_por_service_announce_select_execute_and_finalize_edges(monkeypatch):
	store = InMemoryCheckpointStore()
	ctx = ExecutionContext(
		config=ExecutionConfig(stream=False, max_rounds=20),
		services=ExecutionServices(checkpoint_store=store),
	)
	ctx.state.metadata["run_id"] = "por-service"
	runtime = _runtime(ctx)
	service = PORExecutionService(runtime)
	plan = Plan(goal="goal", steps=[PlanStep(step_id=1, description="a")])
	state = SimpleNamespace(
		plan=plan,
		resumed=False,
		events=[],
		next_steps=[],
		current_step=None,
		should_continue=False,
		error="",
		finalized=False,
		user_message="user",
		step_result="",
		goal_achieved=False,
		final_content="",
	)

	await service.announce_plan(state)
	await service.select_steps(state)
	async def run_shared_success(plan, step):
		step.status = StepStatus.DONE
		return "step ok", [Event.delta("during")]

	service._step_runner.run_shared = AsyncMock(side_effect=run_shared_success)
	await service.execute_step(state)
	await service.observe_step(state)

	assert state.events[0].type == EventType.PLAN_CREATED
	assert state.next_steps[0].step_id == 1
	assert any(event.type == EventType.STEP_START for event in state.events)
	assert any(event.type == EventType.STEP_COMPLETED for event in state.events)
	assert plan.steps[0].status == StepStatus.DONE

	state.goal_achieved = True
	await service.replan_step(state)
	assert state.finalized is True
	assert state.events[-1].type == EventType.DONE


@pytest.mark.asyncio
async def test_por_service_parallel_exception_and_replan_branch(monkeypatch):
	ctx = ExecutionContext(config=ExecutionConfig(stream=False, max_rounds=20))
	service = PORExecutionService(_runtime(ctx))
	failed = PlanStep(step_id=1, description="bad")
	ok = PlanStep(step_id=2, description="ok")
	service._plan = Plan(goal="goal", steps=[failed, ok])

	async def execute_isolated(step):
		if step.step_id == 1:
			raise RuntimeError("isolated boom")
		return StepExecutionResult(step_id=2, result="ok", isolated=True)

	service._execute_step_isolated = execute_isolated
	events = [event async for event in service._execute_parallel([failed, ok])]

	assert failed.status == StepStatus.FAILED
	assert any("Step execution error" in event.content for event in events if event.type == EventType.STEP_COMPLETED)

	service._plan = Plan(goal="goal", steps=[
		PlanStep(step_id=1, description="bad", status=StepStatus.FAILED),
		PlanStep(step_id=2, description="blocked", depends_on=[1]),
	])
	state = SimpleNamespace(error="", finalized=False, goal_achieved=False, next_steps=[service._plan.steps[0]], should_continue=False)
	service._do_replan = AsyncMock()
	await service.replan_step(state)
	assert state.should_continue is True
	service._do_replan.assert_awaited_once_with(1)


@pytest.mark.asyncio
async def test_por_runner_resume_invalid_and_running_step_reset(monkeypatch):
	runner = PORRunner(_runtime())
	invalid_events = [event async for event in runner.run_from_checkpoint_state({}, "user", run_id="missing")]
	assert invalid_events[0].type == EventType.ERROR

	captured = {}

	async def fake_graph_run(plan, user_message, initial_events=None, resumed=False):
		captured["plan"] = plan
		captured["initial"] = initial_events
		captured["resumed"] = resumed
		yield initial_events[0]

	runner._graph_runtime.run = fake_graph_run
	checkpoint_plan = Plan(goal="goal", steps=[PlanStep(step_id=1, description="a", status=StepStatus.RUNNING)])
	checkpoint = plan_to_state(checkpoint_plan, current_step_id=1, phase="step_start")

	events = [event async for event in runner.run_from_checkpoint_state(checkpoint, "user", run_id="run")]

	assert events[0].metadata["resumed"] is True
	assert captured["resumed"] is True
	assert captured["plan"].steps[0].status == StepStatus.PENDING
	assert _checkpoint_step_id(None) is None
	assert _checkpoint_step_id("bad") is None
	assert _checkpoint_step_id("7") == 7


@pytest.mark.asyncio
async def test_por_save_plan_checkpoint_swallows_store_errors():
	class BadStore:
		async def save(self, checkpoint):
			raise RuntimeError("disk full")

	ctx = ExecutionContext(services=ExecutionServices(checkpoint_store=BadStore()))
	ctx.state.metadata["run_id"] = "bad-store"
	service = PORExecutionService(_runtime(ctx))
	service._plan = Plan(goal="goal", steps=[PlanStep(step_id=1, description="a")])

	await service._save_plan_checkpoint(CheckpointStatus.RUNNING, current_step_id=1, phase="x")
