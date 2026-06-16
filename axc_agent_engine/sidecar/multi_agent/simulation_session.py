"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
连接多 Agent 编排和仿真状态的仿真会话。
Simulation session that bridges multi-agent orchestration and simulation state."""
from __future__ import annotations

from typing import Any

from axc_agent_engine.sidecar.multi_agent.shared_context import SharedContext
from axc_agent_engine.sidecar.multi_agent.scheduler.round_robin import RoundRobinScheduler
from axc_agent_engine.sidecar.simulation.adapters import AgentPolicyAdapter
from axc_agent_engine.sidecar.simulation.interfaces import ActorSelector, AgentPolicy, Environment, Evaluator, ObservationBuilder, StopCondition
from axc_agent_engine.sidecar.simulation.models import Scenario, SimulationReport, SimulationStep, WorldState
from axc_agent_engine.sidecar.simulation.runner import SimulationRunner


class SchedulerActorSelector:
	"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
把现有多 Agent Scheduler 适配到仿真 ActorSelector 接口。
	Adapt an existing multi-agent Scheduler to the simulation ActorSelector interface.
	"""

	def __init__(self, agents: list[Any], scheduler: Any | None = None, topic: str = "") -> None:
		self._agents = list(agents)
		self._agent_by_name = {agent.name: agent for agent in agents}
		self._scheduler = scheduler or RoundRobinScheduler()
		self._shared = SharedContext(topic=topic)

	def select(self, state: WorldState, timeline: list[SimulationStep], scenario: Scenario, actors: list[str]) -> list[str]:
		available_agents = [self._agent_by_name[name] for name in actors if name in self._agent_by_name]
		selected = self._scheduler.select_speakers(self._shared, available_agents, len(timeline))
		return [agent.name for agent in selected]

	def record(self, step: SimulationStep) -> None:
		"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
把已应用动作记录到 SharedContext，供 scheduler 选择下一步。
		Record applied actions into SharedContext so schedulers can select the next actor.
		"""
		self._shared.add_message(
			step.actor,
			f"{step.action.type}: {step.action.intent}",
			step.step_id,
		)


class SimulationSession:
	"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
使用现有 Agent 对象运行结构化仿真。
	Run a structured simulation using existing Agent objects.

	不同于 MultiAgentSession，本会话通过 AgentAction -> Environment -> StateDelta 推进 WorldState。
	Unlike MultiAgentSession, this session advances a WorldState through
	AgentAction -> Environment -> StateDelta.

	现有 scheduler 仍可选择下一个行动的 Agent。
	Existing schedulers can still choose which agent acts next.
	"""

	def __init__(
		self,
		agents: list[Any],
		scenario: Scenario,
		policies: dict[str, AgentPolicy] | None = None,
		scheduler: Any | None = None,
		environment: Environment | None = None,
		observation_builder: ObservationBuilder | None = None,
		evaluator: Evaluator | None = None,
		stop_condition: StopCondition | None = None,
		actor_selector: ActorSelector | None = None,
		session_id: str = "",
	) -> None:
		self._agents = list(agents)
		self._scenario = scenario
		self._scheduler_selector = None if actor_selector else SchedulerActorSelector(
			self._agents,
			scheduler=scheduler,
			topic=scenario.title,
		)
		self._actor_selector = actor_selector or self._scheduler_selector
		self._policies = policies or {
			agent.name: AgentPolicyAdapter(agent, session_id=session_id)
			for agent in self._agents
		}
		self._runner = SimulationRunner(
			policies=self._policies,
			environment=environment,
			observation_builder=observation_builder,
			evaluator=evaluator,
			stop_condition=stop_condition,
			actor_selector=self._actor_selector,
		)

	async def run(self, initial_state: WorldState | None = None) -> SimulationReport:
		return await self._runner.run(self._scenario, initial_state=initial_state)
