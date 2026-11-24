from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from axc_agent_engine.core.context import ExecutionConfig, ExecutionContext, ExecutionServices
from axc_agent_engine.core.events import Event, EventType
from axc_agent_engine.core.message_store import MessageStore
from axc_agent_engine.core.plugin_manager import PluginManager
from axc_agent_engine.core.schema import StepStatus
from axc_agent_engine.planning.checkpointing import plan_to_state
from axc_agent_engine.planning.graph_state import PORGraphState
from axc_agent_engine.planning.planner import Plan, PlanStep
from axc_agent_engine.planning.por_runner import (
	PORExecutionService,
	PORRunner,
	StepExecutionResult,
	StepRunner,
	_checkpoint_step_id,
)
from axc_agent_engine.planning.runtime import PlanRuntime
from axc_agent_engine.runtime.checkpoint import CheckpointStatus, InMemoryCheckpointStore
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
async def test_step_runner_shared_handles_round_limit_exception_and_failed_turn(monkeypatch):
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

	class FailedKernel:
		async def run_step(self, **kwargs):
			from axc_agent_engine.core.react_loop import ReActTurnResult

			yield Event.delta("visible")
			yield ReActTurnResult(message={"role": "assistant"}, content="failed content", failed=True)

	ctx = ExecutionContext(config=ExecutionConfig(stream=False, max_rounds=20))
	runner = StepRunner(_runtime(ctx))
	runner._shared_kernel = RaisingKernel()
	assert (await runner.run_shared(plan, plan.steps[0]))[0] == "LLM 调用失败：llm down"
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
	runtime.llm_caller.call = AsyncMock(return_value=({"content": "summary"}, []))
	service = PORExecutionService(runtime)
	plan = Plan(goal="goal", steps=[PlanStep(step_id=1, description="a")])
	state = SimpleNamespace(
		plan=plan,
		resume_state=None,
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

	state.emit = state.events.append
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
	assert runtime.message_store.get_all()[-1] == {"role": "assistant", "content": "summary"}
	assert not any("请根据计划执行结果" in m["content"] for m in runtime.message_store.get_all())


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
	events = []
	await service._execute_parallel(PORGraphState(service._plan, emit=events.append, next_steps=[failed, ok]))

	assert failed.status == StepStatus.FAILED
	assert any("Step execution error" in event.content for event in events if event.type == EventType.STEP_COMPLETED)

	service._plan = Plan(
		goal="goal",
		steps=[
			PlanStep(step_id=1, description="bad", status=StepStatus.FAILED),
			PlanStep(step_id=2, description="blocked", depends_on=[1]),
		],
	)
	state = SimpleNamespace(
		error="", finalized=False, goal_achieved=False, next_steps=[service._plan.steps[0]], should_continue=False
	)
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

	async def fake_graph_run(plan, user_message, initial_events=None, resume_state=None):
		captured["plan"] = plan
		captured["initial"] = initial_events
		captured["resume_state"] = resume_state
		yield initial_events[0]

	runner._graph_runtime.run = fake_graph_run
	checkpoint_plan = Plan(goal="goal", steps=[PlanStep(step_id=1, description="a", status=StepStatus.RUNNING)])
	checkpoint = plan_to_state(checkpoint_plan, current_step_id=1, phase="step_start")

	events = [event async for event in runner.run_from_checkpoint_state(checkpoint, "user", run_id="run")]

	assert events[0].metadata["resumed"] is True
	assert captured["resume_state"] == checkpoint
	assert captured["plan"].steps[0].status == StepStatus.PENDING
	assert _checkpoint_step_id(None) is None
	assert _checkpoint_step_id("bad") is None
	assert _checkpoint_step_id("7") == 7


@pytest.mark.asyncio
async def test_por_save_plan_checkpoint_propagates_store_errors():
	class BadStore(InMemoryCheckpointStore):
		async def save(self, checkpoint):
			raise RuntimeError("disk full")

	ctx = ExecutionContext(services=ExecutionServices(checkpoint_store=BadStore()))
	ctx.state.metadata["run_id"] = "bad-store"
	service = PORExecutionService(_runtime(ctx))
	service._plan = Plan(goal="goal", steps=[PlanStep(step_id=1, description="a")])

	with pytest.raises(RuntimeError, match="disk full"):
		await service._save_plan_checkpoint(CheckpointStatus.RUNNING, current_step_id=1, phase="x")


async def test_final_summary_failure_cannot_emit_done():
	runtime = _runtime()
	runtime.llm_caller.call = AsyncMock(side_effect=RuntimeError("summary provider down"))
	service = PORExecutionService(runtime)
	service._plan = Plan(goal="goal", steps=[])
	with pytest.raises(RuntimeError, match="summary provider down"):
		[event async for event in service._finalize_plan("goal")]


async def test_ready_steps_are_dispatched_in_bounded_batches():
	runtime = _runtime()
	runtime.max_parallel_steps = 2
	service = PORExecutionService(runtime)
	plan = Plan("project", [PlanStep(i, str(i)) for i in range(1, 6)])
	state = PORGraphState(plan, emit=lambda _: None)
	await service.select_steps(state)
	assert [step.step_id for step in state.next_steps] == [1, 2]
	for step in state.next_steps:
		step.status = StepStatus.DONE
	await service.select_steps(state)
	assert [step.step_id for step in state.next_steps] == [3, 4]
	runtime.ctx.state.current_round = runtime.ctx.config.max_rounds - 1
	await service.select_steps(state)
	assert [step.step_id for step in state.next_steps] == [3]


async def test_blocked_plan_cannot_report_success_or_request_summary():
	runtime = _runtime()
	runtime.llm_caller.call = AsyncMock()
	service = PORExecutionService(runtime)
	plan = Plan("project", [PlanStep(1, "build", status=StepStatus.FAILED), PlanStep(2, "package", depends_on=[1])])
	state = PORGraphState(plan, emit=lambda _: None)
	await service.select_steps(state)
	assert "未完成" in state.error and "[1, 2]" in state.error
	runtime.llm_caller.call.assert_not_awaited()


async def test_standalone_por_persists_final_summary_before_delivery():
	store = InMemoryCheckpointStore()
	ctx = ExecutionContext(services=ExecutionServices(checkpoint_store=store))
	ctx.state.metadata["run_id"] = "standalone"
	runtime = _runtime(ctx)
	runtime.llm_caller.call = AsyncMock(return_value=({"content": "ready"}, []))
	service = PORExecutionService(runtime)
	service._plan = Plan("project", [PlanStep(9, "done", status=StepStatus.DONE)])
	await service._save_plan_checkpoint(CheckpointStatus.RUNNING, 9, "step_done")
	async for event in service._finalize_plan("project"):
		saved = await store.latest("standalone")
		assert saved.sequence == 2 and saved.state["phase"] == "finalize"
		assert saved.state["metadata"]["summary"] == event.content == "ready"
	runner = PORRunner(_runtime())
	replay = [event async for event in runner.run_from_checkpoint_state(saved.state, "project")]
	assert replay[-1].type == EventType.DONE and replay[-1].content == "ready"


async def test_parallel_success_checkpoint_remembers_sibling_failure():
	import asyncio

	runtime = _runtime()
	checkpoints = []
	failure_saved = asyncio.Event()

	async def record(kind, status, fields):
		checkpoints.append(fields["strategy_state"])
		if fields["phase"] == "step_failed":
			failure_saved.set()

	runtime.checkpoint = record
	service = PORExecutionService(runtime)
	plan = Plan("project", [PlanStep(1, "fail"), PlanStep(2, "finish")])
	service._plan = plan

	async def execute(step):
		if step.step_id == 1:
			step.status = StepStatus.FAILED
		else:
			await failure_saved.wait()
			step.status = StepStatus.DONE
		return StepExecutionResult(step.step_id, step.description, isolated=True)

	service._execute_step_isolated = execute
	async with asyncio.timeout(2):
		await service._execute_parallel(PORGraphState(plan, emit=lambda _: None, next_steps=plan.steps))
	assert [s["phase"] for s in checkpoints] == ["step_failed", "step_done"]
	assert checkpoints[-1]["metadata"]["replan_step_id"] == 1
	assert checkpoints[-1]["cursor"]["current_step_id"] == 2
