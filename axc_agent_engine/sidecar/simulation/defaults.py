"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
默认仿真组件。
Default simulation components.

这些实现是确定性、无外部依赖的，为测试和后续接入更丰富 policy 或 LLM 环境提供安全基线。
These implementations are deterministic and dependency-free. They provide a
safe baseline for tests and for teams that want to plug in richer policies or
LLM-backed environments later."""
from __future__ import annotations

from axc_agent_engine.sidecar.simulation.models import (
	ActionType,
	AgentAction,
	Observation,
	Scenario,
	Scorecard,
	SimulationStep,
	StateDelta,
	WorldState,
)


class DefaultObservationBuilder:
	"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
用公开世界状态和 actor 私有事实构建 observation。
	Build observations using public world state plus an actor's private facts.
	"""

	def build(self, state: WorldState, actor: str, scenario: Scenario) -> Observation:
		private_facts: list[str] = []
		for profile in scenario.agents:
			if profile.name == actor:
				private_facts = list(profile.private_facts)
				break
		return Observation(
			agent=actor,
			visible_state=state.clone(),
			known_facts=list(state.facts),
			private_facts=private_facts,
		)


class DefaultEnvironment:
	"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
基于规则的基线环境。
	Rule-based baseline environment.

	动作不会直接修改状态，而是转换成记录 fact/event 的 StateDelta。
	The action does not directly mutate state. It is converted into a StateDelta
	that records the action as a fact and event.

	如果 action.parameters 提供了显式变更，则一并应用。
	It then applies explicit changes from action.parameters when provided.
	"""

	async def apply(self, state: WorldState, action: AgentAction, scenario: Scenario) -> StateDelta:
		fact = f"{action.actor} performed {action.type}: {action.intent}".strip()
		event = {
			"step": state.step + 1,
			"actor": action.actor,
			"type": action.type,
			"intent": action.intent,
		}
		variables_changed = dict(action.parameters.get("variables_changed", {}))
		resources_changed = dict(action.parameters.get("resources_changed", {}))
		risks_changed = dict(action.parameters.get("risks_changed", {}))
		facts_added = [fact]
		for extra_fact in action.parameters.get("facts_added", []):
			if isinstance(extra_fact, str):
				facts_added.append(extra_fact)
		return StateDelta(
			facts_added=facts_added,
			facts_removed=[f for f in action.parameters.get("facts_removed", []) if isinstance(f, str)],
			variables_changed=variables_changed,
			resources_changed=resources_changed,
			risks_changed=risks_changed,
			events_added=[event],
			notes=action.expected_effect,
		)


class DefaultEvaluator:
	"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
基于动作置信度和状态风险的简单评分卡。
	Simple scorecard based on action confidence and state risk.
	"""

	async def evaluate(
		self,
		state: WorldState,
		action: AgentAction,
		delta: StateDelta,
		scenario: Scenario,
	) -> Scorecard:
		risk_values = list(state.risks.values()) + list(delta.risks_changed.values())
		risk_level = max(risk_values) if risk_values else 0.0
		goal_progress = float(delta.metadata.get("goal_progress", 0.0)) if delta.metadata else 0.0
		if action.type in (ActionType.DEFEND, ActionType.INVESTIGATE, ActionType.DECIDE):
			goal_progress = max(goal_progress, min(1.0, action.confidence))
		return Scorecard(
			goal_progress=goal_progress,
			risk_level=risk_level,
			confidence=action.confidence,
			agent_effectiveness=action.confidence,
			notes=delta.notes,
		)


class MaxStepsStopCondition:
	"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
在时间线达到 scenario.max_steps 条记录后停止。
	Stop after scenario.max_steps timeline entries.
	"""

	def should_stop(self, state: WorldState, timeline: list[SimulationStep], scenario: Scenario) -> tuple[bool, str]:
		if len(timeline) >= scenario.max_steps:
			return True, f"Reached max steps: {scenario.max_steps}"
		return False, ""


class RoundRobinActorSelector:
	"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
按稳定轮询顺序每个 tick 选择一个 actor。
	Select one actor per tick in stable round-robin order.
	"""

	def select(self, state: WorldState, timeline: list[SimulationStep], scenario: Scenario, actors: list[str]) -> list[str]:
		if not actors:
			return []
		return [actors[len(timeline) % len(actors)]]


class AllActorsSelector:
	"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
每个 tick 选择所有 actor。
	Select all actors at every tick.
	"""

	def select(self, state: WorldState, timeline: list[SimulationStep], scenario: Scenario, actors: list[str]) -> list[str]:
		return list(actors)


class ScriptedPolicy:
	"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
适合测试和示例使用的确定性 policy。
	Deterministic policy useful for tests and examples.
	"""

	def __init__(self, actor: str, actions: list[AgentAction]) -> None:
		self._actor = actor
		self._actions = list(actions)
		self._index = 0

	async def act(self, observation: Observation, scenario: Scenario) -> AgentAction:
		if self._index < len(self._actions):
			action = self._actions[self._index]
			self._index += 1
			return action
		return AgentAction(
			actor=self._actor,
			type=ActionType.WAIT,
			intent="wait for new information",
			confidence=1.0,
		)
