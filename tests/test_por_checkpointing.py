import pytest

from axc_agent_engine.runtime.checkpoint import InMemoryCheckpointStore
from axc_agent_engine.planning.checkpointing import build_plan_resume_summary, load_plan_checkpoint, plan_from_state, save_plan_checkpoint
from axc_agent_engine.planning.planner import Plan, PlanStep
from axc_agent_engine.core.schema import StepStatus


@pytest.mark.asyncio
async def test_plan_checkpoint_roundtrip():
	store = InMemoryCheckpointStore()
	plan = Plan(goal="ship", steps=[PlanStep(step_id=1, description="build", status=StepStatus.RUNNING)])
	await save_plan_checkpoint(store, "run1", 1, plan, current_step_id=1)
	checkpoint = await store.latest("run1")
	restored = plan_from_state(checkpoint.state)
	assert restored is not None
	assert restored.goal == "ship"
	assert restored.steps[0].status == StepStatus.RUNNING


@pytest.mark.asyncio
async def test_plan_checkpoint_resume_resets_running_step():
	store = InMemoryCheckpointStore()
	plan = Plan(goal="ship", steps=[
		PlanStep(step_id=1, description="build", status=StepStatus.DONE, result="ok"),
		PlanStep(step_id=2, description="test", status=StepStatus.RUNNING),
	])
	await save_plan_checkpoint(store, "run1", 2, plan, current_step_id=2, metadata={"phase": "step_start"})
	resume = await load_plan_checkpoint(store, "run1")
	assert resume is not None
	assert resume.current_step_id == 2
	assert resume.phase == "step_start"
	assert resume.plan.steps[0].status == StepStatus.DONE
	assert resume.plan.steps[1].status == StepStatus.PENDING


@pytest.mark.asyncio
async def test_plan_resume_summary_builds_user_prompt():
	store = InMemoryCheckpointStore()
	plan = Plan(goal="ship", steps=[
		PlanStep(step_id=1, description="build", status=StepStatus.DONE, result="ok"),
		PlanStep(step_id=2, description="test", status=StepStatus.RUNNING),
	])
	await save_plan_checkpoint(store, "run2", 2, plan, current_step_id=2, metadata={"phase": "step_start"})
	summary = await build_plan_resume_summary(store, "run2")
	assert summary is not None
	assert summary.can_resume is True
	assert summary.current_step_id == 2
	assert "待继续步骤" in summary.prompt
	assert summary.pending_steps[0].step_id == 2
