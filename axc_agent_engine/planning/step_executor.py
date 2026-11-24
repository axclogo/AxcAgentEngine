"""POR 步骤执行器"""

import logging

from axc_agent_engine.core.schema import StepStatus
from axc_agent_engine.planning.planner import Plan, PlanStep

logger = logging.getLogger(__name__)

RECENT_COMPLETED_LIMIT = 3


def build_step_prompt(plan: Plan, step: PlanStep) -> str:
	"""Build the prompt for a plan step.
	构建步骤执行的 prompt"""
	completed = [s for s in plan.steps if s.status == StepStatus.DONE]
	context_parts = [f"总目标: {plan.goal}", f"当前步骤 {step.step_id}: {step.description}"]
	if completed:
		context_parts.append("已完成步骤:")
		recent = {s.step_id for s in completed[-RECENT_COMPLETED_LIMIT:]}
		include = recent.union(step.depends_on)
		for s in completed:
			if s.step_id in include:
				context_parts.append(f"  - 步骤{s.step_id}: {s.result}")
	if step.tools_needed:
		context_parts.append(f"建议使用工具: {', '.join(step.tools_needed)}")
	context_parts.append("请执行当前步骤，完成后给出结果。")
	return "\n".join(context_parts)
