"""POR checkpoint serialization helpers.
中文：此文档说明相关引擎组件的行为。"""
from __future__ import annotations

from typing import Any

from axc_agent_engine.runtime.checkpoint import Checkpoint, CheckpointStatus
from axc_agent_engine.planning.planner import Plan, PlanStep
from axc_agent_engine.core.schema import StepStatus


def plan_to_state(plan: Plan, current_step_id: int | None = None, phase: str = "por") -> dict[str, Any]:
	"""Serialize a Plan into checkpoint state.
中文：此文档说明相关引擎组件的行为。"""
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
	"""Deserialize a Plan from checkpoint state.
中文：此文档说明相关引擎组件的行为。"""
	payload = state.get("payload") if isinstance(state.get("payload"), dict) else {}
	raw = payload.get("plan") if isinstance(payload, dict) else None
	if not isinstance(raw, dict):
		return None
	steps = []
	for item in raw.get("steps", []):
		if not isinstance(item, dict):
			continue
		status_value = item.get("status", StepStatus.PENDING)
		try:
			status = StepStatus(status_value)
		except ValueError:
			status = StepStatus.PENDING
		steps.append(PlanStep(
			step_id=int(item.get("step_id", 0)),
			description=str(item.get("description", "")),
			depends_on=list(item.get("depends_on") or []),
			tools_needed=list(item.get("tools_needed") or []),
			status=status,
			result=str(item.get("result", "")),
			error=str(item.get("error", "")),
		))
	return Plan(goal=str(raw.get("goal", "")), steps=steps, replan_count=int(raw.get("replan_count", 0)))


async def save_plan_checkpoint(
	store: Any,
	run_id: str,
	sequence: int,
	plan: Plan,
	status: CheckpointStatus = CheckpointStatus.RUNNING,
	current_step_id: int | None = None,
	metadata: dict[str, Any] | None = None,
) -> None:
	"""Persist a POR checkpoint through the generic CheckpointStore protocol.
中文：此文档说明相关引擎组件的行为。"""
	if not store or not run_id:
		return
	checkpoint_metadata = metadata or {}
	state = plan_to_state(plan, current_step_id=current_step_id, phase=str(checkpoint_metadata.get("phase") or "por"))
	state["metadata"] = dict(checkpoint_metadata)
	await store.save(Checkpoint(
		run_id=run_id,
		sequence=sequence,
		status=status,
		kind="por",
		state=state,
		metadata=checkpoint_metadata,
	))
