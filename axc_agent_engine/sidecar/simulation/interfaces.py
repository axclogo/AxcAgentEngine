"""Protocols for simulation kernel extension points.
仿真内核扩展点协议。"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from axc_agent_engine.sidecar.simulation.models import (
	AgentAction,
	Observation,
	Scenario,
	Scorecard,
	SimulationStep,
	StateDelta,
	WorldState,
)


@runtime_checkable
class AgentPolicy(Protocol):
	"""Produces one structured action from an observation.
	根据 observation 生成一个结构化动作。"""

	async def act(
		self,
		observation: Observation,
		scenario: Scenario,
		run_options: dict[str, Any],
		metadata: dict[str, Any],
	) -> AgentAction: ...


@runtime_checkable
class Environment(Protocol):
	"""Applies an action to a world state and returns a delta.
	把动作应用到世界状态，并返回 delta。"""

	async def apply(self, state: WorldState, action: AgentAction, scenario: Scenario) -> StateDelta: ...


@runtime_checkable
class ObservationBuilder(Protocol):
	"""Builds an actor-specific observation from world state.
	根据世界状态构建面向 actor 的 observation。"""

	def build(self, state: WorldState, actor: str, scenario: Scenario) -> Observation: ...


@runtime_checkable
class Evaluator(Protocol):
	"""Scores one action and its state delta.
	为一个动作及其状态 delta 评分。"""

	async def evaluate(
		self,
		state: WorldState,
		action: AgentAction,
		delta: StateDelta,
		scenario: Scenario,
	) -> Scorecard: ...


@runtime_checkable
class StopCondition(Protocol):
	"""Decides whether a simulation run should stop.
	判断一次仿真运行是否应该停止。"""

	def should_stop(
		self, state: WorldState, timeline: list[SimulationStep], scenario: Scenario
	) -> tuple[bool, str]: ...


@runtime_checkable
class ActorSelector(Protocol):
	"""Chooses which actors act at the next simulation tick.
	选择下一个仿真 tick 中行动的 actor。"""

	def select(
		self, state: WorldState, timeline: list[SimulationStep], scenario: Scenario, actors: list[str]
	) -> list[str]: ...
