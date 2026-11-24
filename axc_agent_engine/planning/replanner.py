"""POR 重规划器。"""

import logging
from typing import Any

from axc_agent_engine.core.constants import MAX_REPLAN_COUNT
from axc_agent_engine.core.errors import LLMError, SchemaError
from axc_agent_engine.core.schema import StepStatus
from axc_agent_engine.planning.planner import Plan, create_plan, validate_plan
from axc_agent_engine.planning.prompts import REPLAN_PROMPT
from axc_agent_engine.utils.json_utils import extract_json_array

logger = logging.getLogger(__name__)


def should_replan(plan: Plan) -> bool:
	return plan.replan_count < MAX_REPLAN_COUNT


async def replan(plan: Plan, failed_step_id: int, llm: Any = None) -> Plan:
	"""Revise the remaining plan after failure.
	步骤失败后重规划；提供 LLM 时使用 LLM，否则使用启发式规则。"""
	if llm:
		completed_text = (
			"\n".join(f"  Step {s.step_id}: {s.result}" for s in plan.steps if s.status == StepStatus.DONE) or "  None"
		)
		pending_text = (
			"\n".join(f"  Step {s.step_id}: {s.description}" for s in plan.steps if s.status == StepStatus.PENDING)
			or "  None"
		)
		failed_step = next((s for s in plan.steps if s.step_id == failed_step_id), None)
		failed_error = failed_step.error if failed_step else "Unknown error"
		prompt = REPLAN_PROMPT.format(
			goal=plan.goal,
			completed=completed_text,
			failed_id=failed_step_id,
			failed_error=failed_error,
			pending=pending_text,
		)
		try:
			content = await llm.ask(prompt)
			new_steps = extract_json_array(content)
			if new_steps:
				revision = create_plan(plan.goal, new_steps)
				kept = [s for s in plan.steps if s.status in (StepStatus.DONE, StepStatus.FAILED)]
				revision.steps = kept + revision.steps
				validate_plan(revision)
				plan.steps = revision.steps
				plan.replan_count += 1
				return plan
		except (LLMError, SchemaError, OSError, RuntimeError, ValueError) as e:
			logger.warning(f"[replanner] LLM replan failed, falling back to heuristic: {e}")
	# Skip steps that depend on failed work. 中文：启发式 fallback：跳过依赖失败步骤的步骤
	plan.replan_count += 1
	failed_ids = {s.step_id for s in plan.steps if s.status == StepStatus.FAILED}
	for step in plan.steps:
		if step.status == StepStatus.PENDING and any(d in failed_ids for d in step.depends_on):
			step.status = StepStatus.SKIPPED
			step.error = f"Dependency steps {step.depends_on} failed, skipped"
	return plan
