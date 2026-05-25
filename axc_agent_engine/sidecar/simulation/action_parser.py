"""把 LLM 文本解析成结构化仿真动作。
Parse LLM text into structured simulation actions.
"""
from __future__ import annotations

from typing import Any

from axc_agent_engine.sidecar.simulation.models import ActionType, AgentAction
from axc_agent_engine.utils.json_utils import extract_json_object


class ActionParseError(ValueError):
	"""当文本无法转换为 AgentAction 时抛出。
	Raised when text cannot be converted to an AgentAction.
	"""


class ActionParser:
	"""AgentAction JSON payload 的严格解析器。
	Strict parser for AgentAction JSON payloads.
	"""

	def parse(self, text: str, default_actor: str = "") -> AgentAction:
		"""解析文本并返回 AgentAction。
		Parse text and return an AgentAction.

		输入可以是 JSON 对象，也可以被 Markdown JSON fence 包裹。
		Accepted input is a JSON object, optionally wrapped in a Markdown JSON
		fence.

		对象必须包含动作 `type`，如果提供了 default_actor 则可以省略 `actor`。
		The object must contain an action `type` and may omit `actor` when
		default_actor is provided.
		"""
		data = extract_json_object(text)
		if not data:
			raise ActionParseError("No JSON object found in action response")
		actor = str(data.get("actor") or default_actor)
		action_type = str(data.get("type") or "")
		if not actor:
			raise ActionParseError("Action actor is required")
		if not action_type:
			raise ActionParseError("Action type is required")
		if action_type not in {item.value for item in ActionType}:
			action_type = ActionType.CUSTOM
		parameters = data.get("parameters", {})
		if parameters is None:
			parameters = {}
		if not isinstance(parameters, dict):
			raise ActionParseError("Action parameters must be an object")
		confidence = _clamp_float(data.get("confidence", 0.0), 0.0, 1.0)
		return AgentAction(
			actor=actor,
			type=action_type,
			intent=str(data.get("intent", "")),
			parameters=parameters,
			rationale=str(data.get("rationale", "")),
			confidence=confidence,
			expected_effect=str(data.get("expected_effect", "")),
			metadata=data.get("metadata", {}) if isinstance(data.get("metadata", {}), dict) else {},
		)


def _clamp_float(value: Any, min_value: float, max_value: float) -> float:
	try:
		number = float(value)
	except (TypeError, ValueError):
		number = min_value
	return max(min_value, min(max_value, number))
