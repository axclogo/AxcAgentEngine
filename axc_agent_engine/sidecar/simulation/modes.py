"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
通用仿真内核的模式适配器。
Mode adapters for the generic simulation kernel."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from axc_agent_engine.sidecar.simulation.defaults import AllActorsSelector, RoundRobinActorSelector
from axc_agent_engine.sidecar.simulation.interfaces import ActorSelector
from axc_agent_engine.sidecar.simulation.models import AgentProfile, Scenario, WorldState
from axc_agent_engine.sidecar.simulation.runner import SimulationRunner


@dataclass(frozen=True)
class SimulationModeAdapter:
	"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
为一种通用仿真模式构建 Scenario/Runner 组合。
	Builds a Scenario/Runner pair for one general-purpose simulation mode.
	"""
	name: str
	actor_selector_factory: Callable[[], ActorSelector]
	default_agents: tuple[AgentProfile, ...]
	rules: tuple[str, ...] = ()
	constraints: tuple[str, ...] = ()

	def build_scenario(self, title: str, objective: str = "", **overrides: Any) -> Scenario:
		agents = overrides.pop("agents", None) or list(self.default_agents)
		initial_state = overrides.pop("initial_state", None) or WorldState(metadata={"mode": self.name})
		return Scenario(
			id=str(overrides.pop("id", self.name)),
			title=title,
			objective=objective,
			background=str(overrides.pop("background", "")),
			initial_state=initial_state,
			agents=agents,
			rules=list(overrides.pop("rules", self.rules)),
			constraints=list(overrides.pop("constraints", self.constraints)),
			success_criteria=list(overrides.pop("success_criteria", [])),
			max_steps=int(overrides.pop("max_steps", 10)),
			metadata={"mode": self.name, **dict(overrides.pop("metadata", {}))},
		)

	def build_runner(self, policies: dict[str, Any], **kwargs: Any) -> SimulationRunner:
		return SimulationRunner(
			policies=policies,
			actor_selector=kwargs.pop("actor_selector", self.actor_selector_factory()),
			environment=kwargs.pop("environment", None),
			observation_builder=kwargs.pop("observation_builder", None),
			evaluator=kwargs.pop("evaluator", None),
			stop_condition=kwargs.pop("stop_condition", None),
			branch_id=kwargs.pop("branch_id", self.name),
		)


SIMULATION_MODE_ADAPTERS: dict[str, SimulationModeAdapter] = {
	"discussion": SimulationModeAdapter(
		name="discussion",
		actor_selector_factory=RoundRobinActorSelector,
		default_agents=(
			AgentProfile(name="analyst", role="分析观点和约束"),
			AgentProfile(name="synthesizer", role="整合结论"),
		),
		rules=("轮流提出信息、约束、方案和结论",),
	),
	"debate": SimulationModeAdapter(
		name="debate",
		actor_selector_factory=RoundRobinActorSelector,
		default_agents=(
			AgentProfile(name="pro", role="正方"),
			AgentProfile(name="con", role="反方"),
		),
		rules=("提出论点、证据和反驳", "避免重复上一轮观点"),
	),
	"redblue": SimulationModeAdapter(
		name="redblue",
		actor_selector_factory=RoundRobinActorSelector,
		default_agents=(
			AgentProfile(name="red", role="寻找攻击路径"),
			AgentProfile(name="blue", role="防御和缓解"),
		),
		rules=("红方描述风险路径", "蓝方提出可执行防御"),
	),
	"interview": SimulationModeAdapter(
		name="interview",
		actor_selector_factory=RoundRobinActorSelector,
		default_agents=(
			AgentProfile(name="interviewer", role="提出澄清问题并追问细节"),
			AgentProfile(name="candidate", role="回答问题并暴露假设"),
		),
		rules=("一问一答推进访谈", "每轮围绕事实、约束、偏好或风险澄清一个主题"),
	),
	"social": SimulationModeAdapter(
		name="social",
		actor_selector_factory=AllActorsSelector,
		default_agents=(
			AgentProfile(name="poster", role="发布信息"),
			AgentProfile(name="responder", role="回应扩散"),
		),
		rules=("以信息流形式并行发布和回应",),
	),
	"backcast": SimulationModeAdapter(
		name="backcast",
		actor_selector_factory=RoundRobinActorSelector,
		default_agents=(
			AgentProfile(name="planner", role="从目标倒推路径"),
			AgentProfile(name="validator", role="验证前置条件"),
		),
		rules=("从目标结果倒推里程碑和前置条件",),
	),
	"retrospective": SimulationModeAdapter(
		name="retrospective",
		actor_selector_factory=RoundRobinActorSelector,
		default_agents=(
			AgentProfile(name="investigator", role="回溯原因"),
			AgentProfile(name="reviewer", role="归纳经验"),
		),
		rules=("从结果回溯因果链", "沉淀可验证经验"),
	),
	"sandbox": SimulationModeAdapter(
		name="sandbox",
		actor_selector_factory=RoundRobinActorSelector,
		default_agents=(AgentProfile(name="simulator", role="隔离推演"),),
		rules=("只改变模拟世界状态", "不写入业务数据库或外部会话"),
		constraints=("外部副作用必须通过适配层显式接入",),
	),
}


def get_simulation_mode_adapter(mode: str) -> SimulationModeAdapter:
	try:
		return SIMULATION_MODE_ADAPTERS[mode]
	except KeyError as exc:
		raise ValueError(f"Unknown simulation mode: {mode}") from exc
