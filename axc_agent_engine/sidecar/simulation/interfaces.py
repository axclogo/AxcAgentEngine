"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
仿真内核扩展点协议。
Protocols for simulation kernel extension points."""
from __future__ import annotations

from typing import Protocol, runtime_checkable

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
	"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
根据 observation 生成一个结构化动作。
	Produces one structured action from an observation.
	"""
	async def act(self, observation: Observation, scenario: Scenario) -> AgentAction: ...


@runtime_checkable
class Environment(Protocol):
	"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
把动作应用到世界状态，并返回 delta。
	Applies an action to a world state and returns a delta.
	"""
	async def apply(self, state: WorldState, action: AgentAction, scenario: Scenario) -> StateDelta: ...


@runtime_checkable
class ObservationBuilder(Protocol):
	"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
根据世界状态构建面向 actor 的 observation。
	Builds an actor-specific observation from world state.
	"""
	def build(self, state: WorldState, actor: str, scenario: Scenario) -> Observation: ...


@runtime_checkable
class Evaluator(Protocol):
	"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
为一个动作及其状态 delta 评分。
	Scores one action and its state delta.
	"""
	async def evaluate(
		self,
		state: WorldState,
		action: AgentAction,
		delta: StateDelta,
		scenario: Scenario,
	) -> Scorecard: ...


@runtime_checkable
class StopCondition(Protocol):
	"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
判断一次仿真运行是否应该停止。
	Decides whether a simulation run should stop.
	"""
	def should_stop(self, state: WorldState, timeline: list[SimulationStep], scenario: Scenario) -> tuple[bool, str]: ...


@runtime_checkable
class ActorSelector(Protocol):
	"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
选择下一个仿真 tick 中行动的 actor。
	Chooses which actors act at the next simulation tick.
	"""
	def select(self, state: WorldState, timeline: list[SimulationStep], scenario: Scenario, actors: list[str]) -> list[str]: ...
