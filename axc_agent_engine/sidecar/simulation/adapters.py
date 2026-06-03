"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
连接仿真内核和现有 Agent 对象的适配器。
Adapters that connect the simulation kernel to existing agent objects."""
from __future__ import annotations

import json
from typing import Any

from axc_agent_engine.sidecar.simulation.action_parser import ActionParser
from axc_agent_engine.sidecar.simulation.models import AgentAction, Observation, Scenario


class AgentPolicyAdapter:
	"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
把现有类 Agent 对象作为仿真 AgentPolicy 使用。
	Use an existing Agent-like object as a simulation AgentPolicy.

	被包装对象只需要提供 async `chat(message, session_id="")` 方法。
	The wrapped object only needs an async `chat(message, session_id="")` method.

	这让仿真内核不依赖具体 Agent 类。
	This keeps the simulation kernel independent from the concrete Agent class.
	"""

	def __init__(
		self,
		agent: Any,
		parser: ActionParser | None = None,
		session_id: str = "",
	) -> None:
		self._agent = agent
		self._parser = parser or ActionParser()
		self._session_id = session_id

	async def act(
		self,
		observation: Observation,
		scenario: Scenario,
		run_options: dict[str, Any],
		metadata: dict[str, Any],
	) -> AgentAction:
		prompt = build_action_prompt(observation, scenario)
		raw = await self._agent.chat(prompt, session_id=self._session_id, run_options=run_options, metadata=metadata)
		return self._parser.parse(raw, default_actor=observation.agent)


def build_action_prompt(observation: Observation, scenario: Scenario) -> str:
	"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
构建要求 Agent 返回一个结构化动作的紧凑 prompt。
	Build the compact prompt that asks an agent for one structured action.
	"""
	payload = {
		"scenario": {
			"id": scenario.id,
			"title": scenario.title,
			"objective": scenario.objective,
			"background": scenario.background,
			"rules": scenario.rules,
			"constraints": scenario.constraints,
			"success_criteria": scenario.success_criteria,
		},
		"observation": {
			"agent": observation.agent,
			"step": observation.visible_state.step,
			"variables": observation.visible_state.variables,
			"resources": observation.visible_state.resources,
			"facts": observation.known_facts,
			"private_facts": observation.private_facts,
			"risks": observation.visible_state.risks,
			"open_events": observation.visible_state.open_events[-5:],
		},
	}
	return (
		"你正在一个结构化仿真场景中行动。"
		"请只返回一个 JSON 对象作为下一步动作，JSON 外不要输出任何说明文字。\n\n"
		"允许的动作字段：actor、type、intent、parameters、rationale、"
		"confidence、expected_effect、metadata。\n"
		"允许的 type 取值：communicate、investigate、attack、defend、negotiate、"
		"allocate_resource、escalate、wait、tool_call、decide、custom。\n\n"
		f"上下文：\n{json.dumps(payload, ensure_ascii=False, default=str)}"
	)
