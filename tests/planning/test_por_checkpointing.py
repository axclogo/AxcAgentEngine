import pytest

from axc_agent_engine.core.schema import StepStatus
from axc_agent_engine.planning.checkpointing import plan_from_state, save_plan_checkpoint
from axc_agent_engine.planning.planner import Plan, PlanStep
from axc_agent_engine.runtime.checkpoint import InMemoryCheckpointStore


@pytest.mark.asyncio
async def test_plan_checkpoint_roundtrip():
	store = InMemoryCheckpointStore()
	plan = Plan(goal="ship", steps=[PlanStep(step_id=1, description="build", status=StepStatus.RUNNING)])
	await save_plan_checkpoint(store, "run1", 1, plan, current_step_id=1, metadata={"phase": "step_start"})
	checkpoint = await store.latest("run1")
	restored = plan_from_state(checkpoint.state)
	assert restored is not None
	assert restored.goal == "ship"
	assert restored.steps[0].status == StepStatus.RUNNING
	assert checkpoint.state["phase"] == "step_start"
	assert "plan" not in checkpoint.state
	assert "current_step_id" not in checkpoint.state
	assert checkpoint.metadata["phase"] == "step_start"


@pytest.mark.parametrize(
	("field", "value"),
	[
		("depends_on", None),
		("depends_on", False),
		("depends_on", "1,2"),
		("tools_needed", ""),
		("tools_needed", {}),
		("tools_needed", [1]),
	],
)
def test_plan_restore_rejects_malformed_step_lists(field, value):
	from axc_agent_engine.core.errors import SchemaError
	from axc_agent_engine.planning.checkpointing import plan_from_state

	step = {"step_id": 1, "description": "inspect", field: value}
	with pytest.raises(SchemaError):
		plan_from_state({"payload": {"plan": {"goal": "review", "steps": [step]}}})
