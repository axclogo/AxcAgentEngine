"""POR observer — 评估步骤执行结果。"""
import logging
from dataclasses import dataclass
from typing import Any

from axc_agent_engine.core.schema import StepStatus
from axc_agent_engine.utils.json_utils import extract_json_object

from axc_agent_engine.planning.prompts import OBSERVE_PROMPT

logger = logging.getLogger(__name__)


@dataclass
class StepObservation:
	"""步骤观察结果。"""
	step_id: int
	step_ok: bool = True
	key_info: str = ""
	plan_still_valid: bool = True
	goal_achieved: bool = False
	action: str = "continue"
	reason: str = ""


async def observe_step(step_id: int, step_status: StepStatus | str, step_result: str,
					   step_description: str, plan_goal: str, remaining_steps: int,
					   llm: Any = None) -> StepObservation:
	"""评估步骤结果；提供 LLM 时使用 LLM，否则使用启发式规则。"""
	if llm:
		prompt = OBSERVE_PROMPT.format(
			goal=plan_goal, step_id=step_id, description=step_description,
			result=step_result, remaining=remaining_steps)
		try:
			content = await llm.ask(prompt)
			data = extract_json_object(content)
			if data and "action" in data:
				return StepObservation(
					step_id=step_id, step_ok=data.get("step_ok", step_status in (StepStatus.DONE, "done")),
					key_info=step_result, goal_achieved=data.get("goal_achieved", False),
					action=data.get("action", "continue"), reason=data.get("reason", ""))
		except Exception as e:
			logger.warning(f"[observer] LLM evaluation failed, falling back to heuristic: {e}")
	# 启发式 fallback
	step_ok = step_status in (StepStatus.DONE, "done")
	if not step_ok and remaining_steps > 0:
		return StepObservation(
			step_id=step_id, step_ok=False, key_info=step_result,
			plan_still_valid=False, action="replan", reason=f"Step {step_id} failed")
	if remaining_steps == 0:
		return StepObservation(
			step_id=step_id, step_ok=step_ok, key_info=step_result,
			goal_achieved=step_ok, action="done")
	return StepObservation(step_id=step_id, step_ok=step_ok, key_info=step_result, action="continue")
