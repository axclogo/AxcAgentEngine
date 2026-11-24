"""Core simulation data models.
仿真核心数据模型。

simulation package 刻意独立于 Engine/Agent/Executor。
The simulation package is intentionally independent from Engine/Agent/Executor.

Agent 可以通过适配器作为 policy 使用，但仿真内核拥有场景状态、动作、delta 和时间线记录。
Agents may be used as policies by adapters, but the simulation kernel owns
scenario state, actions, deltas, and timeline records."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class ActionType(StrEnum):
	"""Common simulation action categories.
	常见仿真动作类别。"""

	COMMUNICATE = "communicate"
	INVESTIGATE = "investigate"
	ATTACK = "attack"
	DEFEND = "defend"
	NEGOTIATE = "negotiate"
	ALLOCATE_RESOURCE = "allocate_resource"
	ESCALATE = "escalate"
	WAIT = "wait"
	TOOL_CALL = "tool_call"
	DECIDE = "decide"
	CUSTOM = "custom"


class SimulationEventType(StrEnum):
	"""SimulationRunner.stream() 发出的事件类别。
	Event categories emitted by SimulationRunner.stream().
	"""

	START = "start"
	ACTOR_SELECTED = "actor_selected"
	STEP_STARTED = "step_started"
	STEP_COMPLETED = "step_completed"
	DONE = "done"
	ERROR = "error"


@dataclass
class AgentProfile:
	"""Static description of an actor participating in a scenario.
	参与场景的 actor 静态描述。"""

	name: str
	role: str = ""
	goal: str = ""
	private_facts: list[str] = field(default_factory=list)
	metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class WorldState:
	"""Structured state of the simulated world.
	仿真世界的结构化状态。"""

	step: int = 0
	variables: dict[str, Any] = field(default_factory=dict)
	resources: dict[str, Any] = field(default_factory=dict)
	actors: dict[str, dict[str, Any]] = field(default_factory=dict)
	relationships: dict[str, Any] = field(default_factory=dict)
	facts: list[str] = field(default_factory=list)
	risks: dict[str, float] = field(default_factory=dict)
	goals: dict[str, Any] = field(default_factory=dict)
	open_events: list[dict[str, Any]] = field(default_factory=list)
	artifacts: dict[str, Any] = field(default_factory=dict)
	metadata: dict[str, Any] = field(default_factory=dict)

	def clone(self) -> WorldState:
		"""Return a detached copy suitable for branch snapshots.
		返回适合分支快照使用的独立副本。"""
		import copy

		return copy.deepcopy(self)

	def apply_delta(self, delta: StateDelta) -> WorldState:
		"""Apply a state delta in place and return self for fluent use.
		原地应用状态 delta，并返回 self 以支持链式调用。"""
		for fact in delta.facts_removed:
			if fact in self.facts:
				self.facts.remove(fact)
		for fact in delta.facts_added:
			if fact not in self.facts:
				self.facts.append(fact)
		self.variables.update(delta.variables_changed)
		self.resources.update(delta.resources_changed)
		self.risks.update(delta.risks_changed)
		self.open_events.extend(delta.events_added)
		self.artifacts.update(delta.artifacts_added)
		self.metadata.update(delta.metadata)
		self.step += 1
		return self


@dataclass
class Scenario:
	"""Input specification for a simulation run.
	一次仿真运行的输入规格。"""

	id: str
	title: str
	objective: str = ""
	background: str = ""
	initial_state: WorldState = field(default_factory=WorldState)
	agents: list[AgentProfile] = field(default_factory=list)
	rules: list[str] = field(default_factory=list)
	constraints: list[str] = field(default_factory=list)
	success_criteria: list[str] = field(default_factory=list)
	max_steps: int = 10
	metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class Observation:
	"""Agent-specific view of the world.
	面向某个 Agent 的世界视图。"""

	agent: str
	visible_state: WorldState
	known_facts: list[str] = field(default_factory=list)
	private_facts: list[str] = field(default_factory=list)
	uncertainty: dict[str, Any] = field(default_factory=dict)
	metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentAction:
	"""Agent policy 提出的结构化动作。
	Structured action proposed by an agent policy.
	"""

	actor: str
	type: str = ActionType.CUSTOM
	intent: str = ""
	parameters: dict[str, Any] = field(default_factory=dict)
	rationale: str = ""
	confidence: float = 0.0
	expected_effect: str = ""
	metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class StateDelta:
	"""All world mutations produced by applying one action.
	应用一个动作后产生的所有世界状态变更。"""

	facts_added: list[str] = field(default_factory=list)
	facts_removed: list[str] = field(default_factory=list)
	variables_changed: dict[str, Any] = field(default_factory=dict)
	resources_changed: dict[str, Any] = field(default_factory=dict)
	risks_changed: dict[str, float] = field(default_factory=dict)
	events_added: list[dict[str, Any]] = field(default_factory=list)
	artifacts_added: dict[str, Any] = field(default_factory=dict)
	notes: str = ""
	metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class Scorecard:
	"""Evaluation of one simulation step.
	一个仿真步骤的评估结果。"""

	goal_progress: float = 0.0
	risk_level: float = 0.0
	cost: float = 0.0
	confidence: float = 0.0
	policy_violation: bool = False
	agent_effectiveness: float = 0.0
	scenario_success: bool = False
	notes: str = ""
	metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class SimulationStep:
	"""Replayable record of one applied action.
	一个已应用动作的可回放记录。"""

	step_id: int
	branch_id: str
	actor: str
	observation: Observation
	action: AgentAction
	delta: StateDelta
	scorecard: Scorecard


@dataclass
class SimulationReport:
	"""Final summary of a simulation run.
	一次仿真运行的最终摘要。"""

	scenario_id: str
	title: str
	final_state: WorldState
	timeline: list[SimulationStep]
	summary: str = ""
	success: bool = False
	error: str = ""
	metrics: dict[str, Any] = field(default_factory=dict)


@dataclass
class SimulationEvent:
	"""Structured event emitted during simulation execution.
	仿真执行期间发出的结构化事件。"""

	type: SimulationEventType
	content: str = ""
	step: SimulationStep | None = None
	report: SimulationReport | None = None
	metadata: dict[str, Any] = field(default_factory=dict)

	@classmethod
	def start(cls, scenario: Scenario) -> SimulationEvent:
		return cls(
			type=SimulationEventType.START,
			content=scenario.title,
			metadata={"scenario_id": scenario.id, "max_steps": scenario.max_steps},
		)

	@classmethod
	def done(cls, report: SimulationReport) -> SimulationEvent:
		return cls(type=SimulationEventType.DONE, content=report.summary, report=report)

	@classmethod
	def error(cls, message: str, metadata: dict[str, Any] | None = None) -> SimulationEvent:
		return cls(type=SimulationEventType.ERROR, content=message, metadata=deepcopy(metadata or {}))
