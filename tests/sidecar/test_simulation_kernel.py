"""Tests for the standalone simulation kernel."""
from __future__ import annotations

from axc_agent_engine.sidecar.simulation import (
	ActionType,
	AgentAction,
	AgentProfile,
	DefaultObservationBuilder,
	Scenario,
	ScriptedPolicy,
	SimulationEventType,
	LLMSimulationReportGenerator,
	SimulationRunner,
	WorldState,
)


async def test_simulation_runner_applies_state_delta():
	state = WorldState(
		variables={"alert_level": "low"},
		risks={"data_loss": 0.2},
		facts=["vpn login observed"],
	)
	scenario = Scenario(
		id="redblue-1",
		title="Red blue tabletop",
		initial_state=state,
		agents=[AgentProfile(name="blue", role="defender")],
		max_steps=1,
	)
	action = AgentAction(
		actor="blue",
		type=ActionType.DEFEND,
		intent="triage suspicious login",
		parameters={
			"variables_changed": {"alert_level": "medium"},
			"risks_changed": {"data_loss": 0.1},
			"facts_added": ["blue started triage"],
		},
		confidence=0.8,
		expected_effect="reduce data loss risk",
	)
	runner = SimulationRunner(policies={"blue": ScriptedPolicy("blue", [action])})

	report = await runner.run(scenario)

	assert report.metrics["steps"] == 1
	assert report.final_state.variables["alert_level"] == "medium"
	assert report.final_state.risks["data_loss"] == 0.1
	assert "blue started triage" in report.final_state.facts
	assert report.timeline[0].action.intent == "triage suspicious login"
	assert report.timeline[0].scorecard.goal_progress == 0.8


def test_observation_builder_adds_private_facts_without_mutating_state():
	state = WorldState(facts=["public fact"])
	scenario = Scenario(
		id="info-1",
		title="Information asymmetry",
		agents=[AgentProfile(name="red", private_facts=["stolen credential available"])],
	)
	builder = DefaultObservationBuilder()

	observation = builder.build(state, "red", scenario)

	assert observation.known_facts == ["public fact"]
	assert observation.private_facts == ["stolen credential available"]
	observation.visible_state.facts.append("local only")
	assert state.facts == ["public fact"]


async def test_runner_uses_actor_order_and_waits_when_script_exhausted():
	scenario = Scenario(
		id="order-1",
		title="Actor order",
		initial_state=WorldState(),
		agents=[AgentProfile(name="a"), AgentProfile(name="b")],
		max_steps=3,
	)
	runner = SimulationRunner(
		policies={
			"a": ScriptedPolicy("a", [AgentAction(actor="a", type=ActionType.DECIDE, intent="choose option")]),
			"b": ScriptedPolicy("b", [AgentAction(actor="b", type=ActionType.COMMUNICATE, intent="respond")]),
		}
	)

	report = await runner.run(scenario)

	assert [step.actor for step in report.timeline] == ["a", "b", "a"]
	assert report.timeline[-1].action.type == ActionType.WAIT
	assert report.summary == "Reached max steps: 3"


async def test_runner_returns_empty_report_without_actors():
	scenario = Scenario(id="empty", title="Empty", max_steps=1)
	runner = SimulationRunner(policies={})

	report = await runner.run(scenario)

	assert report.timeline == []
	assert report.summary == "No actors configured"
	assert report.metrics["steps"] == 0


async def test_runner_stream_emits_step_and_done_events():
	scenario = Scenario(
		id="stream-1",
		title="Streamed simulation",
		agents=[AgentProfile(name="blue")],
		max_steps=1,
	)
	action = AgentAction(actor="blue", type=ActionType.INVESTIGATE, intent="inspect alert", confidence=0.5)
	runner = SimulationRunner(policies={"blue": ScriptedPolicy("blue", [action])})

	events = [event async for event in runner.stream(scenario)]

	assert [event.type for event in events] == [
		SimulationEventType.START,
		SimulationEventType.ACTOR_SELECTED,
		SimulationEventType.STEP_STARTED,
		SimulationEventType.STEP_COMPLETED,
		SimulationEventType.DONE,
	]
	assert events[-1].report is not None
	assert events[-1].report.metrics["steps"] == 1


async def test_runner_stream_converts_policy_error_to_error_report():
	class FailingPolicy:
		async def act(self, observation, scenario, run_options, metadata):
			raise RuntimeError("policy unavailable")

	scenario = Scenario(id="fail-1", title="Failure", agents=[AgentProfile(name="red")], max_steps=2)
	runner = SimulationRunner(policies={"red": FailingPolicy()})

	events = [event async for event in runner.stream(scenario)]

	assert events[-2].type == SimulationEventType.ERROR
	assert events[-2].metadata["actor"] == "red"
	assert events[-1].type == SimulationEventType.DONE
	assert events[-1].report is not None
	assert events[-1].report.error == "policy unavailable"


async def test_runner_passes_run_context_to_policy():
	class CapturePolicy:
		def __init__(self):
			self.context = None

		async def act(self, observation, scenario, run_options, metadata):
			self.context = (run_options, metadata)
			return AgentAction(actor=observation.agent, type=ActionType.DECIDE, intent="decide")

	policy = CapturePolicy()
	scenario = Scenario(id="ctx-1", title="Context", agents=[AgentProfile(name="blue")], max_steps=1)
	runner = SimulationRunner(policies={"blue": policy})

	await runner.run(
		scenario,
		run_options={"run_id": "sim-run"},
		metadata={"tenant": "t1"},
		actor_run_options=lambda actor, index: {"stream": False},
		actor_metadata=lambda actor, index: {"actor_exec": actor},
	)

	run_options, metadata = policy.context
	assert run_options == {"run_id": "sim-run", "stream": False}
	assert metadata["run_id"] == "sim-run"
	assert metadata["tenant"] == "t1"
	assert metadata["sidecar"] == "simulation"
	assert metadata["simulation_scenario_id"] == "ctx-1"
	assert metadata["simulation_actor"] == "blue"
	assert metadata["actor_exec"] == "blue"


async def test_runner_rejects_conflicting_run_ids():
	scenario = Scenario(id="bad-run-id", title="Bad run id", agents=[AgentProfile(name="blue")], max_steps=1)
	runner = SimulationRunner(policies={"blue": ScriptedPolicy("blue", [AgentAction(actor="blue", type=ActionType.WAIT, intent="wait")])})

	try:
		[event async for event in runner.stream(scenario, run_options={"run_id": "run-a"}, metadata={"run_id": "run-b"})]
	except ValueError as exc:
		assert "run_options.run_id conflicts with metadata.run_id" in str(exc)
	else:
		raise AssertionError("expected ValueError")


async def test_runner_rejects_invalid_run_context_inputs():
	scenario = Scenario(id="bad-ctx", title="Bad context", agents=[AgentProfile(name="blue")], max_steps=1)
	runner = SimulationRunner(policies={"blue": ScriptedPolicy("blue", [AgentAction(actor="blue", type=ActionType.WAIT, intent="wait")])})

	try:
		[event async for event in runner.stream(scenario, metadata="bad")]
	except TypeError as exc:
		assert "metadata" in str(exc)
	else:
		raise AssertionError("expected TypeError")

	try:
		fresh = SimulationRunner(policies={"blue": ScriptedPolicy("blue", [AgentAction(actor="blue", type=ActionType.WAIT, intent="wait")])})
		[event async for event in fresh.stream(scenario, actor_run_options=lambda actor, index: "bad")]
	except TypeError as exc:
		assert "actor_run_options" in str(exc)
	else:
		raise AssertionError("expected TypeError")


class FakeReportLLM:
	async def ask(self, prompt: str) -> str:
		return '{"summary":"LLM summary","key_findings":["finding"],"risks":["risk"],"recommendations":["fix"]}'


async def test_simulation_runner_can_generate_llm_report():
	scenario = Scenario(
		id="report-1",
		title="Report simulation",
		agents=[AgentProfile(name="blue")],
		max_steps=1,
	)
	action = AgentAction(actor="blue", type=ActionType.INVESTIGATE, intent="inspect", confidence=0.5)
	runner = SimulationRunner(
		policies={"blue": ScriptedPolicy("blue", [action])},
		report_generator=LLMSimulationReportGenerator(FakeReportLLM()),
	)

	report = await runner.run(scenario)

	assert report.summary == "LLM summary"
	assert report.metrics["key_findings"] == ["finding"]
	assert report.metrics["risks"] == ["risk"]
	assert report.metrics["recommendations"] == ["fix"]
