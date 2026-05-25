"""Tests for simulation parser and multi-agent adapters."""
from __future__ import annotations

from axc_agent_engine.sidecar.multi_agent.simulation_session import SimulationSession
from axc_agent_engine.sidecar.simulation import (
	ActionParseError,
	ActionParser,
	ActionType,
	AgentAction,
	AgentProfile,
	Scenario,
	WorldState,
	get_simulation_mode_adapter,
)


class FakeAgent:
	def __init__(self, name: str, responses: list[str]) -> None:
		self.name = name
		self.description = ""
		self._responses = list(responses)
		self.prompts: list[str] = []

	async def chat(self, message: str, session_id: str = "", llm_options: dict | None = None) -> str:
		self.prompts.append(message)
		return self._responses.pop(0)


def test_action_parser_extracts_nested_json_from_fence():
	text = """```json
{
  "actor": "red",
  "type": "attack",
  "intent": "try credential reuse",
  "parameters": {"variables_changed": {"access": "user"}},
  "confidence": 1.4
}
```"""

	action = ActionParser().parse(text)

	assert action.actor == "red"
	assert action.type == ActionType.ATTACK
	assert action.parameters["variables_changed"]["access"] == "user"
	assert action.confidence == 1.0


def test_action_parser_rejects_missing_json():
	try:
		ActionParser().parse("I will investigate", default_actor="blue")
	except ActionParseError as exc:
		assert "No JSON object" in str(exc)
	else:
		raise AssertionError("expected ActionParseError")


async def test_simulation_session_uses_agent_chat_policy():
	agent = FakeAgent("blue", [
		'{"type":"defend","intent":"isolate endpoint","parameters":{"variables_changed":{"isolated":true}},"confidence":0.7}'
	])
	scenario = Scenario(
		id="sim-agent",
		title="Agent backed simulation",
		objective="contain incident",
		initial_state=WorldState(variables={"isolated": False}),
		agents=[AgentProfile(name="blue", role="defender")],
		max_steps=1,
	)
	session = SimulationSession([agent], scenario)

	report = await session.run()

	assert report.final_state.variables["isolated"] is True
	assert report.timeline[0].action.intent == "isolate endpoint"
	assert "structured simulation" in agent.prompts[0]


async def test_simulation_session_accepts_custom_policies():
	agent = FakeAgent("red", [])
	scenario = Scenario(
		id="sim-policy",
		title="Policy simulation",
		agents=[AgentProfile(name="red")],
		max_steps=1,
	)

	class StaticPolicy:
		async def act(self, observation, scenario):
			return AgentAction(actor="red", type=ActionType.WAIT, intent="hold")

	session = SimulationSession([agent], scenario, policies={"red": StaticPolicy()})

	report = await session.run()

	assert report.timeline[0].action.intent == "hold"
	assert agent.prompts == []


def test_simulation_mode_adapter_builds_generic_sandbox_scenario():
	adapter = get_simulation_mode_adapter("sandbox")
	scenario = adapter.build_scenario("Dry run", objective="test assumptions", max_steps=2)
	runner = adapter.build_runner({})

	assert scenario.metadata["mode"] == "sandbox"
	assert "不写入业务数据库或外部会话" in scenario.rules
	assert runner is not None


def test_simulation_mode_adapter_supports_interview():
	adapter = get_simulation_mode_adapter("interview")
	scenario = adapter.build_scenario("Discovery interview")
	assert [agent.name for agent in scenario.agents] == ["interviewer", "candidate"]
	assert "一问一答推进访谈" in scenario.rules
