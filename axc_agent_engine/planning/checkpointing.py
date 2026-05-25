"""POR checkpoint serialization helpers."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from axc_agent_engine.runtime.checkpoint import Checkpoint, CheckpointStatus
from axc_agent_engine.planning.planner import Plan, PlanStep
from axc_agent_engine.core.schema import StepStatus


@dataclass(frozen=True)
class PlanResumeState:
	"""A restored POR plan checkpoint ready for step-level resume."""
	plan: Plan
	run_id: str
	current_step_id: int | None = None
	phase: str = "por"
	sequence: int = 0
	status: str = ""
	metadata: dict[str, Any] | None = None


@dataclass(frozen=True)
class PlanResumeSummary:
	"""User-facing POR resume summary, independent of host todo models."""
	run_id: str
	goal: str
	status: str
	phase: str
	current_step_id: int | None
	completed_steps: list[PlanStep]
	pending_steps: list[PlanStep]
	failed_steps: list[PlanStep]
	can_resume: bool
	prompt: str


def plan_to_state(plan: Plan, current_step_id: int | None = None, phase: str = "por") -> dict[str, Any]:
	"""Serialize a Plan into checkpoint state."""
	return {
		"phase": phase,
		"current_step_id": current_step_id,
		"plan": {
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
		},
	}


def plan_from_state(state: dict[str, Any]) -> Plan | None:
	"""Deserialize a Plan from checkpoint state."""
	raw = state.get("plan")
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


def prepare_plan_for_resume(plan: Plan, current_step_id: int | None = None) -> Plan:
	"""Reset in-flight steps to pending while preserving completed step results."""
	for step in plan.steps:
		if step.status == StepStatus.RUNNING:
			step.status = StepStatus.PENDING
		if current_step_id is not None and step.step_id == current_step_id and step.status != StepStatus.DONE:
			step.status = StepStatus.PENDING
	return plan


async def load_plan_checkpoint(store: Any, run_id: str) -> PlanResumeState | None:
	"""Load latest POR checkpoint for a run and normalize it for resume."""
	if not store or not run_id:
		return None
	checkpoint = await store.latest(run_id)
	if not checkpoint or checkpoint.kind != "por":
		return None
	plan = plan_from_state(checkpoint.state)
	if not plan:
		return None
	current_step_id = checkpoint.state.get("current_step_id")
	if current_step_id is not None:
		try:
			current_step_id = int(current_step_id)
		except (TypeError, ValueError):
			current_step_id = None
	phase = str(checkpoint.metadata.get("phase") or checkpoint.state.get("phase") or "por")
	return PlanResumeState(
		plan=prepare_plan_for_resume(plan, current_step_id=current_step_id),
		run_id=run_id,
		current_step_id=current_step_id,
		phase=phase,
		sequence=checkpoint.sequence,
		status=str(checkpoint.status),
		metadata=dict(checkpoint.metadata),
	)


async def build_plan_resume_summary(store: Any, run_id: str) -> PlanResumeSummary | None:
	"""Build a storage-neutral prompt for host recovery UX."""
	resume = await load_plan_checkpoint(store, run_id)
	if not resume:
		return None
	completed = [step for step in resume.plan.steps if step.status == StepStatus.DONE]
	failed = [step for step in resume.plan.steps if step.status == StepStatus.FAILED]
	pending = [step for step in resume.plan.steps if step.status != StepStatus.DONE]
	prompt = _resume_prompt(resume, completed, pending, failed)
	return PlanResumeSummary(
		run_id=resume.run_id,
		goal=resume.plan.goal,
		status=resume.status,
		phase=resume.phase,
		current_step_id=resume.current_step_id,
		completed_steps=completed,
		pending_steps=pending,
		failed_steps=failed,
		can_resume=bool(pending) and resume.status != str(CheckpointStatus.COMPLETED),
		prompt=prompt,
	)


async def save_plan_checkpoint(
	store: Any,
	run_id: str,
	sequence: int,
	plan: Plan,
	status: CheckpointStatus = CheckpointStatus.RUNNING,
	current_step_id: int | None = None,
	metadata: dict[str, Any] | None = None,
) -> None:
	"""Persist a POR checkpoint through the generic CheckpointStore protocol."""
	if not store or not run_id:
		return
	await store.save(Checkpoint(
		run_id=run_id,
		sequence=sequence,
		status=status,
		kind="por",
		state=plan_to_state(plan, current_step_id=current_step_id),
		metadata=metadata or {},
	))


def _resume_prompt(
	resume: PlanResumeState,
	completed: list[PlanStep],
	pending: list[PlanStep],
	failed: list[PlanStep],
) -> str:
	lines = [
		"检测到一个未完成的计划执行。",
		f"目标：{resume.plan.goal}",
		f"运行 ID：{resume.run_id}",
		f"阶段：{resume.phase}",
		f"已完成步骤：{len(completed)} / {len(resume.plan.steps)}",
	]
	if resume.current_step_id is not None:
		lines.append(f"当前步骤：{resume.current_step_id}")
	if failed:
		lines.append("失败步骤：")
		for step in failed[:5]:
			lines.append(f"- {step.step_id}. {step.description}: {step.error}")
	if pending:
		lines.append("待继续步骤：")
		for step in pending[:5]:
			lines.append(f"- {step.step_id}. {step.description}")
	lines.append("可以从最近的 checkpoint 继续执行。")
	return "\n".join(lines)
