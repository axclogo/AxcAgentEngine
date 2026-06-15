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
	SimulationReport,
	WorldState,
	get_simulation_mode_adapter,
)
from axc_agent_engine.sidecar.simulation.report import (
	GeneratedSimulationReport,
	LLMSimulationReportGenerator,
	_parse_generated_report,
	apply_generated_report,
)


class FakeAgent:
	def __init__(self, name: str, responses: list[str]) -> None:
		self.name = name
		self.description = ""
		self._responses = list(responses)
		self.prompts: list[str] = []
		self.calls: list[dict] = []

	async def chat(
		self,
		message: str,
		session_id: str = "",
		llm_options: dict | None = None,
		run_options: dict | None = None,
		metadata: dict | None = None,
	) -> str:
		self.prompts.append(message)
		self.calls.append({"run_options": run_options, "metadata": metadata})
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


def test_action_parser_rejects_missing_actor_type_and_bad_parameters():
	parser = ActionParser()
	for text, error in [
		('{"type":"wait"}', "actor"),
		('{"actor":"red"}', "type"),
		('{"actor":"red","type":"wait","parameters":["bad"]}', "parameters"),
	]:
		try:
			parser.parse(text)
		except ActionParseError as exc:
			assert error in str(exc)
		else:
			raise AssertionError(f"expected ActionParseError for {text}")


def test_action_parser_defaults_unknown_type_and_clamps_confidence():
	action = ActionParser().parse(
		'{"type":"invented","intent":"x","parameters":null,"confidence":"bad","metadata":[]}',
		default_actor="blue",
	)
	assert action.actor == "blue"
	assert action.type == ActionType.CUSTOM
	assert action.parameters == {}
	assert action.confidence == 0.0
	assert action.metadata == {}


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
	assert "结构化仿真场景" in agent.prompts[0]


async def test_simulation_session_accepts_custom_policies():
	agent = FakeAgent("red", [])
	scenario = Scenario(
		id="sim-policy",
		title="Policy simulation",
		agents=[AgentProfile(name="red")],
		max_steps=1,
	)

	class StaticPolicy:
		async def act(self, observation, scenario, run_options, metadata):
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
	assert "不写入外部数据库或外部会话" in scenario.rules
	assert runner is not None


def test_simulation_mode_adapter_copies_metadata_override():
	adapter = get_simulation_mode_adapter("sandbox")
	metadata = {"nested": {"value": "original"}}

	scenario = adapter.build_scenario("Dry run", metadata=metadata)
	metadata["nested"]["value"] = "mutated"

	assert scenario.metadata["nested"] == {"value": "original"}


def test_simulation_mode_adapter_supports_interview():
	adapter = get_simulation_mode_adapter("interview")
	scenario = adapter.build_scenario("Discovery interview")
	assert [agent.name for agent in scenario.agents] == ["interviewer", "candidate"]
	assert "一问一答推进访谈" in scenario.rules


def test_generated_report_parser_handles_empty_invalid_and_fences():
	assert _parse_generated_report("", fallback="base").summary == "base"
	assert _parse_generated_report("not json", fallback="base").summary == "base"
	assert _parse_generated_report("[1, 2]", fallback="base").summary == "base"
	report = _parse_generated_report(
		'```json\n{"summary":"s","key_findings":[" a ",""],"risks":"bad","recommendations":[3]}\n```',
		fallback="base",
	)
	assert report.summary == "s"
	assert report.key_findings == ["a"]
	assert report.risks == []
	assert report.recommendations == ["3"]


async def test_llm_report_generator_no_model_and_failure_paths():
	report = SimulationReport(scenario_id="r", title="Report", final_state=WorldState(), timeline=[], summary="base")
	assert (await LLMSimulationReportGenerator(None).generate(report)).summary == report.summary

	class BrokenModel:
		async def ask(self, prompt):
			raise RuntimeError("down")

	assert (await LLMSimulationReportGenerator(BrokenModel()).generate(report)).summary == report.summary


def test_apply_generated_report_overwrites_summary_and_metrics():
	report = SimulationReport(scenario_id="r", title="Report", final_state=WorldState(), timeline=[], summary="base")
	result = apply_generated_report(
		report,
		GeneratedSimulationReport(summary="new", key_findings=["k"], risks=["r"], recommendations=["do"]),
	)
	assert result.summary == "new"
	assert result.metrics["key_findings"] == ["k"]
