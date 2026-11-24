"""POR checkpoint serialization helpers. POR 计划与检查点之间的转换。"""

from __future__ import annotations

from typing import Any

from axc_agent_engine.core.schema import StepStatus
from axc_agent_engine.planning.planner import Plan, create_plan, validate_plan
from axc_agent_engine.runtime.checkpoint import Checkpoint, CheckpointStatus


def plan_to_state(plan: Plan, current_step_id: int | None = None, phase: str = "por") -> dict[str, Any]:
	"""Serialize a Plan into checkpoint state. 将计划序列化为检查点状态。"""
	plan_payload = {
		"goal": plan.goal,
		"replan_count": plan.replan_count,
		"steps": [
			{
				"step_id": step.step_id,
				"description": step.description,
				"depends_on": list(step.depends_on),
				"tools_needed": list(step.tools_needed),
				"status": str(step.status.value if isinstance(step.status, StepStatus) else step.status),
				"result": step.result,
				"error": step.error,
			}
			for step in plan.steps
		],
	}
	return {
		"kind": "por",
		"phase": phase,
		"cursor": {"current_step_id": current_step_id},
		"payload": {"plan": plan_payload},
		"metadata": {},
	}


def plan_from_state(state: dict[str, Any]) -> Plan | None:
	"""Deserialize a Plan from checkpoint state. 从检查点还原计划。"""
	raw = state.get("payload", {}).get("plan")
	if raw is None:
		return None
	items = raw["steps"]
	plan = create_plan(raw["goal"], items)
	validate_plan(plan)
	plan.replan_count = raw.get("replan_count", 0)
	for step, item in zip(plan.steps, items, strict=True):
		step.status = StepStatus(item.get("status", StepStatus.PENDING))
		step.result = item.get("result", "")
		step.error = item.get("error", "")
	return plan


async def save_plan_checkpoint(
	store: Any,
	run_id: str,
	sequence: int,
	plan: Plan,
	status: CheckpointStatus = CheckpointStatus.RUNNING,
	current_step_id: int | None = None,
	metadata: dict[str, Any] | None = None,
) -> None:
	"""Persist a POR checkpoint through the generic CheckpointStore protocol. 通过宿主提供的检查点存储持久化 POR 进度。"""
	if not store or not run_id:
		return
	checkpoint_metadata = metadata or {}
	state = plan_to_state(plan, current_step_id=current_step_id, phase=str(checkpoint_metadata.get("phase") or "por"))
	state["metadata"] = dict(checkpoint_metadata)
	await store.save(
		Checkpoint(
			run_id=run_id,
			sequence=sequence,
			status=status,
			kind="por",
			state=state,
			metadata=checkpoint_metadata,
		)
	)
