"""POR 重规划器。"""
import logging
from typing import Any

from axc_agent_engine.core.constants import MAX_REPLAN_COUNT
from axc_agent_engine.planning.planner import Plan, PlanStep
from axc_agent_engine.core.schema import StepStatus
from axc_agent_engine.utils.json_utils import extract_json_array

from axc_agent_engine.planning.prompts import REPLAN_PROMPT

logger = logging.getLogger(__name__)


def should_replan(plan: Plan) -> bool:
	return plan.replan_count < MAX_REPLAN_COUNT


async def replan(plan: Plan, failed_step_id: int, llm: Any = None) -> Plan:
	"""步骤失败后重规划；提供 LLM 时使用 LLM，否则使用启发式规则。"""
	if llm:
		completed_text = "\n".join(
			f"  Step {s.step_id}: {s.result}" for s in plan.steps if s.status == StepStatus.DONE) or "  None"
		pending_text = "\n".join(
			f"  Step {s.step_id}: {s.description}" for s in plan.steps if s.status == StepStatus.PENDING) or "  None"
		failed_step = next((s for s in plan.steps if s.step_id == failed_step_id), None)
		failed_error = failed_step.error if failed_step else "Unknown error"
		prompt = REPLAN_PROMPT.format(
			goal=plan.goal, completed=completed_text,
			failed_id=failed_step_id, failed_error=failed_error, pending=pending_text)
		try:
			content = await llm.ask(prompt)
			new_steps = extract_json_array(content)
			if new_steps:
				plan.replan_count += 1
				kept = [s for s in plan.steps if s.status in (StepStatus.DONE, StepStatus.FAILED)]
				for s in new_steps:
					kept.append(PlanStep(
						step_id=s.get("step_id", 0), description=s.get("description", ""),
						depends_on=s.get("depends_on", []) or [], tools_needed=s.get("tools_needed", []) or []))
				plan.steps = kept
				return plan
		except Exception as e:
			logger.warning(f"[replanner] LLM replan failed, falling back to heuristic: {e}")
	# 启发式 fallback：跳过依赖失败步骤的步骤
	plan.replan_count += 1
	failed_ids = {s.step_id for s in plan.steps if s.status == StepStatus.FAILED}
	for step in plan.steps:
		if step.status == StepStatus.PENDING and any(d in failed_ids for d in step.depends_on):
			step.status = StepStatus.SKIPPED
			step.error = f"Dependency steps {step.depends_on} failed, skipped"
	return plan
