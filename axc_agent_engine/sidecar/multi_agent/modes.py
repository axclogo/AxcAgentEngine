"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
多 Agent 会话的模式画像。
Mode profiles for multi-agent sessions."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable

from axc_agent_engine.sidecar.multi_agent.scheduler.all_parallel import AllParallelScheduler
from axc_agent_engine.sidecar.multi_agent.scheduler.debate import DebateScheduler
from axc_agent_engine.sidecar.multi_agent.scheduler.redblue import RedBlueScheduler
from axc_agent_engine.sidecar.multi_agent.scheduler.round_robin import RoundRobinScheduler
from axc_agent_engine.sidecar.multi_agent.scheduler.supervisor import SupervisorScheduler
from axc_agent_engine.sidecar.multi_agent.stop_condition.causal_chain import CausalChainStop
from axc_agent_engine.sidecar.multi_agent.stop_condition.consensus import ConsensusStop
from axc_agent_engine.sidecar.multi_agent.stop_condition.goal_reached import GoalReachedStop
from axc_agent_engine.sidecar.multi_agent.stop_condition.max_rounds import MaxRoundsStop
from axc_agent_engine.sidecar.multi_agent.types import SessionMode

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ModeProfile:
	"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
一个多 Agent 模式的调度、停止条件和提示配置。
	Scheduler, stop-condition, and prompt configuration for one multi-agent mode.
	"""

	mode: SessionMode
	scheduler_factory: Callable[["ModeRuntime"], Any]
	stop_factory: Callable[["ModeRuntime"], Any]
	prompt_guidance: str = ""
	requires_supervisor: bool = False
	min_agents: int = 1
	utility_llm_recommended: bool = False


@dataclass(frozen=True)
class ModeRuntime:
	"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
构建模式运行组件所需的运行时输入。
	Runtime inputs needed to build mode components.
	"""

	agents: list[Any]
	supervisor: Any | None = None
	persona: dict[str, dict] | None = None
	max_rounds: int = 10
	utility_llm: Any = None


MODE_PROFILES: dict[SessionMode, ModeProfile] = {
	SessionMode.DISCUSSION: ModeProfile(
		mode=SessionMode.DISCUSSION,
		scheduler_factory=lambda rt: RoundRobinScheduler(),
		stop_factory=lambda rt: MaxRoundsStop(rt.max_rounds),
		prompt_guidance="多方围绕议题轮流提出信息、约束、方案和结论。",
	),
	SessionMode.GROUP_CHAT: ModeProfile(
		mode=SessionMode.GROUP_CHAT,
		scheduler_factory=lambda rt: RoundRobinScheduler(),
		stop_factory=lambda rt: MaxRoundsStop(rt.max_rounds),
		prompt_guidance="多方轮流补充观点，避免重复上一位发言。",
	),
	SessionMode.SUPERVISOR: ModeProfile(
		mode=SessionMode.SUPERVISOR,
		scheduler_factory=lambda rt: SupervisorScheduler(rt.supervisor, rt.agents),
		stop_factory=lambda rt: MaxRoundsStop(rt.max_rounds),
		prompt_guidance="Supervisor 负责分派任务，Worker 按分派产出可执行结果。",
		requires_supervisor=True,
	),
	SessionMode.DEBATE: ModeProfile(
		mode=SessionMode.DEBATE,
		scheduler_factory=lambda rt: DebateScheduler(rt.agents),
		stop_factory=lambda rt: ConsensusStop(
			llm_client=rt.utility_llm,
			check_interval=3,
			max_rounds=rt.max_rounds,
		) if rt.utility_llm else MaxRoundsStop(rt.max_rounds),
		prompt_guidance="围绕同一命题提出论点、反驳和证据，直到分歧收敛。",
		min_agents=2,
		utility_llm_recommended=True,
	),
	SessionMode.INTERVIEW: ModeProfile(
		mode=SessionMode.INTERVIEW,
		scheduler_factory=lambda rt: RoundRobinScheduler(),
		stop_factory=lambda rt: MaxRoundsStop(rt.max_rounds),
		prompt_guidance="访谈者提出澄清问题，被访者回答事实、约束、偏好和风险，每轮聚焦一个主题。",
		min_agents=2,
	),
	SessionMode.SIMULATION: ModeProfile(
		mode=SessionMode.SIMULATION,
		scheduler_factory=lambda rt: RoundRobinScheduler(),
		stop_factory=lambda rt: MaxRoundsStop(rt.max_rounds),
		prompt_guidance="以角色身份推进场景状态，明确行动、观察和后果。",
	),
	SessionMode.BACKCAST: ModeProfile(
		mode=SessionMode.BACKCAST,
		scheduler_factory=lambda rt: RoundRobinScheduler(),
		stop_factory=lambda rt: GoalReachedStop(
			llm_client=rt.utility_llm,
			check_interval=3,
			max_rounds=rt.max_rounds,
		) if rt.utility_llm else MaxRoundsStop(rt.max_rounds),
		prompt_guidance="从目标倒推关键里程碑、前置条件和当前应做动作。",
		utility_llm_recommended=True,
	),
	SessionMode.RETROSPECTIVE: ModeProfile(
		mode=SessionMode.RETROSPECTIVE,
		scheduler_factory=lambda rt: RoundRobinScheduler(),
		stop_factory=lambda rt: CausalChainStop(
			llm_client=rt.utility_llm,
			check_interval=3,
			max_rounds=rt.max_rounds,
		) if rt.utility_llm else MaxRoundsStop(rt.max_rounds),
		prompt_guidance="从结果回溯原因，逐步形成可验证的因果链。",
		utility_llm_recommended=True,
	),
	SessionMode.REDBLUE: ModeProfile(
		mode=SessionMode.REDBLUE,
		scheduler_factory=lambda rt: RedBlueScheduler(rt.agents, rt.persona),
		stop_factory=lambda rt: MaxRoundsStop(rt.max_rounds),
		prompt_guidance="红方寻找突破路径，蓝方防御和修补，裁判归纳风险。",
		min_agents=2,
	),
	SessionMode.SOCIAL: ModeProfile(
		mode=SessionMode.SOCIAL,
		scheduler_factory=lambda rt: AllParallelScheduler(),
		stop_factory=lambda rt: MaxRoundsStop(rt.max_rounds),
		prompt_guidance="以社交信息流形式并行发布、回应和扩散观点。",
	),
	SessionMode.SANDBOX: ModeProfile(
		mode=SessionMode.SANDBOX,
		scheduler_factory=lambda rt: RoundRobinScheduler(),
		stop_factory=lambda rt: MaxRoundsStop(rt.max_rounds),
		prompt_guidance="在隔离假设中推演行动、观察和风险，不写入外部业务状态。",
	),
}


def get_mode_profile(mode: SessionMode) -> ModeProfile:
	profile = MODE_PROFILES.get(mode)
	if not profile:
		if mode == SessionMode.CUSTOM:
			raise ValueError("Custom mode requires explicit scheduler and stop_condition")
		raise ValueError(f"Unknown mode: {mode}")
	return profile


def build_scheduler_for_mode(mode: SessionMode, runtime: ModeRuntime) -> Any:
	profile = get_mode_profile(mode)
	_validate_mode_runtime(profile, runtime)
	return profile.scheduler_factory(runtime)


def build_stop_condition_for_mode(mode: SessionMode, runtime: ModeRuntime) -> Any:
	profile = get_mode_profile(mode)
	if profile.utility_llm_recommended and not runtime.utility_llm:
		logger.warning("[session] Mode %s needs utility_llm for semantic stop condition, falling back to MaxRoundsStop", mode)
	return profile.stop_factory(runtime)


def mode_prompt_guidance(mode: SessionMode) -> str:
	return get_mode_profile(mode).prompt_guidance


def _validate_mode_runtime(profile: ModeProfile, runtime: ModeRuntime) -> None:
	if profile.requires_supervisor and runtime.supervisor is None:
		raise ValueError("supervisor mode requires a supervisor Agent")
	if len(runtime.agents) < profile.min_agents:
		raise ValueError(f"{profile.mode} mode requires at least {profile.min_agents} agents")
