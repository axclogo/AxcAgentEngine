"""POR 调度器 — 按依赖关系调度步骤执行"""

import logging

from axc_agent_engine.core.schema import StepStatus
from axc_agent_engine.planning.planner import Plan, PlanStep

logger = logging.getLogger(__name__)


def get_next_steps(plan: Plan) -> list[PlanStep]:
	"""Return pending steps whose dependencies are satisfied. 获取所有可执行的待执行步骤（依赖已满足）"""
	done = {step.step_id for step in plan.steps if step.status == StepStatus.DONE}
	return [step for step in plan.steps if step.status == StepStatus.PENDING and done.issuperset(step.depends_on)]


def get_remaining_count(plan: Plan) -> int:
	"""Count unfinished steps. 获取剩余待执行步骤数"""
	return sum(1 for s in plan.steps if s.status == StepStatus.PENDING)


def mark_step_done(plan: Plan, step_id: int, result: str) -> None:
	"""Mark a step as completed. 标记步骤完成"""
	step = _get_step(plan, step_id)
	if step:
		step.status = StepStatus.DONE
		step.result = result


def mark_step_failed(plan: Plan, step_id: int, error: str) -> None:
	"""Mark a step as failed. 标记步骤失败"""
	step = _get_step(plan, step_id)
	if step:
		step.status = StepStatus.FAILED
		step.error = error


def _get_step(plan: Plan, step_id: int) -> PlanStep | None:
	for s in plan.steps:
		if s.step_id == step_id:
			return s
	return None
