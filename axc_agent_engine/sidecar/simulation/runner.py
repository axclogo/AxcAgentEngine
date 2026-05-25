"""仿真运行器。
Simulation runner.
"""
from __future__ import annotations

from typing import AsyncIterator

from axc_agent_engine.sidecar.simulation.defaults import (
	DefaultEnvironment,
	DefaultEvaluator,
	DefaultObservationBuilder,
	MaxStepsStopCondition,
	RoundRobinActorSelector,
)
from axc_agent_engine.sidecar.simulation.interfaces import (
	ActorSelector,
	AgentPolicy,
	Environment,
	Evaluator,
	ObservationBuilder,
	StopCondition,
)
from axc_agent_engine.sidecar.simulation.models import (
	Scenario,
	SimulationEvent,
	SimulationEventType,
	SimulationReport,
	SimulationStep,
	WorldState,
)
from axc_agent_engine.sidecar.simulation.report import SimulationReportGenerator, apply_generated_report


class SimulationReportBuilder:
	"""根据仿真状态构建确定性报告。
	Builds deterministic reports from simulation state.
	"""

	def build(
		self,
		scenario: Scenario,
		state: WorldState,
		timeline: list[SimulationStep],
		summary: str,
		error: str = "",
	) -> SimulationReport:
		last_score = timeline[-1].scorecard if timeline else None
		success = bool(last_score.scenario_success) if last_score else False
		metrics = {
			"steps": len(timeline),
			"final_world_step": state.step,
			"max_risk": max(state.risks.values()) if state.risks else 0.0,
		}
		if last_score:
			metrics["last_goal_progress"] = last_score.goal_progress
			metrics["last_confidence"] = last_score.confidence
		return SimulationReport(
			scenario_id=scenario.id,
			title=scenario.title,
			final_state=state,
			timeline=timeline,
			summary=summary,
			success=success,
			error=error,
			metrics=metrics,
		)


class SimulationStepWorker:
	"""按 observe、act、apply、evaluate 流程运行一个 actor 步骤。
	Runs one actor step through observe, act, apply, and evaluate.
	"""

	def __init__(
		self,
		observation_builder: ObservationBuilder,
		environment: Environment,
		evaluator: Evaluator,
		branch_id: str,
	) -> None:
		self._observation_builder = observation_builder
		self._environment = environment
		self._evaluator = evaluator
		self._branch_id = branch_id

	async def run(
		self,
		actor: str,
		policy: AgentPolicy,
		state: WorldState,
		scenario: Scenario,
		timeline: list[SimulationStep],
	) -> SimulationStep:
		observation = self._observation_builder.build(state, actor, scenario)
		action = await policy.act(observation, scenario)
		delta = await self._environment.apply(state, action, scenario)
		scorecard = await self._evaluator.evaluate(state, action, delta, scenario)
		return SimulationStep(
			step_id=len(timeline) + 1,
			branch_id=self._branch_id,
			actor=actor,
			observation=observation,
			action=action,
			delta=delta,
			scorecard=scorecard,
		)


class SimulationRunner:
	"""按结构化 observation、action、delta 和 scoring 运行一个场景。
	Runs a scenario through structured observation, action, delta, and scoring.

	运行器不依赖 Engine、Agent、Executor 或 MultiAgentSession。
	The runner does not depend on Engine, Agent, Executor, or MultiAgentSession.

	这些系统后续可以提供 AgentPolicy adapter，而无需改动该内核。
	Those systems can provide AgentPolicy adapters later without changing this
	kernel.
	"""

	def __init__(
		self,
		policies: dict[str, AgentPolicy],
		environment: Environment | None = None,
		observation_builder: ObservationBuilder | None = None,
		evaluator: Evaluator | None = None,
		stop_condition: StopCondition | None = None,
		actor_selector: ActorSelector | None = None,
		report_generator: SimulationReportGenerator | None = None,
		branch_id: str = "main",
	) -> None:
		self._policies = policies
		self._environment = environment or DefaultEnvironment()
		self._observation_builder = observation_builder or DefaultObservationBuilder()
		self._evaluator = evaluator or DefaultEvaluator()
		self._stop_condition = stop_condition or MaxStepsStopCondition()
		self._actor_selector = actor_selector or RoundRobinActorSelector()
		self._report_generator = report_generator
		self._branch_id = branch_id
		self._report_builder = SimulationReportBuilder()
		self._step_worker = SimulationStepWorker(
			self._observation_builder,
			self._environment,
			self._evaluator,
			self._branch_id,
		)

	async def run(self, scenario: Scenario, initial_state: WorldState | None = None) -> SimulationReport:
		"""运行场景直到停止条件触发，并返回报告。
		Run the scenario until the stop condition triggers and return its report.
		"""
		report: SimulationReport | None = None
		async for event in self.stream(scenario, initial_state=initial_state):
			if event.report is not None:
				report = event.report
		if report is None:
			state = initial_state.clone() if initial_state else scenario.initial_state.clone()
			return await self._finalize_report(self._build_report(scenario, state, [], "Simulation ended without report", error="missing_report"))
		return report

	async def stream(
		self,
		scenario: Scenario,
		initial_state: WorldState | None = None,
	) -> AsyncIterator[SimulationEvent]:
		"""运行场景并发出结构化执行事件。
		Run the scenario and emit structured execution events.
		"""
		state = initial_state.clone() if initial_state else scenario.initial_state.clone()
		timeline: list[SimulationStep] = []
		yield SimulationEvent.start(scenario)
		actors = [agent.name for agent in scenario.agents]
		if not actors:
			actors = list(self._policies.keys())
		if not actors:
			report = self._build_report(scenario, state, timeline, "No actors configured")
			report = await self._finalize_report(report)
			yield SimulationEvent.done(report)
			return
		while True:
			stop, reason = self._stop_condition.should_stop(state, timeline, scenario)
			if stop:
				report = self._build_report(scenario, state, timeline, reason)
				report = await self._finalize_report(report)
				yield SimulationEvent.done(report)
				return
			selected_actors = self._actor_selector.select(state, timeline, scenario, actors)
			if not selected_actors:
				report = self._build_report(scenario, state, timeline, "No actors selected")
				report = await self._finalize_report(report)
				yield SimulationEvent.done(report)
				return
			yield SimulationEvent(
				type=SimulationEventType.ACTOR_SELECTED,
				content=", ".join(selected_actors),
				metadata={"actors": selected_actors},
			)
			for actor in selected_actors:
				policy = self._policies.get(actor)
				if policy is None:
					continue
				try:
					yield SimulationEvent(
						type=SimulationEventType.STEP_STARTED,
						content=actor,
						metadata={"actor": actor, "next_step_id": len(timeline) + 1},
					)
					step = await self._step_worker.run(actor, policy, state, scenario, timeline)
				except Exception as exc:
					message = f"Simulation step failed for actor '{actor}': {exc}"
					report = self._build_report(scenario, state, timeline, message, error=str(exc))
					report = await self._finalize_report(report)
					yield SimulationEvent.error(message, {"actor": actor, "error_type": type(exc).__name__})
					yield SimulationEvent.done(report)
					return
				timeline.append(step)
				record = getattr(self._actor_selector, "record", None)
				if callable(record):
					record(step)
				state.apply_delta(step.delta)
				yield SimulationEvent(
					type=SimulationEventType.STEP_COMPLETED,
					content=step.action.intent,
					step=step,
					metadata={"actor": actor, "world_step": state.step},
				)
				stop, reason = self._stop_condition.should_stop(state, timeline, scenario)
				if stop:
					report = self._build_report(scenario, state, timeline, reason)
					report = await self._finalize_report(report)
					yield SimulationEvent.done(report)
					return

	def _build_report(
		self,
		scenario: Scenario,
		state: WorldState,
		timeline: list[SimulationStep],
		summary: str,
		error: str = "",
	) -> SimulationReport:
		return self._report_builder.build(scenario, state, timeline, summary, error)

	async def _finalize_report(self, report: SimulationReport) -> SimulationReport:
		if not self._report_generator:
			return report
		generated = await self._report_generator.generate(report)
		return apply_generated_report(report, generated)
