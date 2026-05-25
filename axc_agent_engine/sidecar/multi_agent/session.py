"""MultiAgentSession — 通过 dispatcher/bus 编排多 Agent。
MultiAgentSession — orchestrates multiple Agents through dispatcher/bus.

所有跨 Agent 通信都走 AgentMessageDispatcher。
All cross-Agent communication goes through AgentMessageDispatcher.

Scheduler 只选择接收者，dispatcher.request 负责驱动执行。
Schedulers only choose recipients; dispatcher.request drives execution.
"""
from __future__ import annotations

import json
import logging
import uuid
from typing import Any, AsyncIterator, TYPE_CHECKING

from axc_agent_engine.sidecar.multi_agent.shared_context import SharedContext
from axc_agent_engine.sidecar.multi_agent.events import MultiAgentEvent
from axc_agent_engine.sidecar.multi_agent.modes import (
	ModeRuntime,
	build_scheduler_for_mode,
	build_stop_condition_for_mode,
	mode_prompt_guidance,
)
from axc_agent_engine.sidecar.multi_agent.persona import build_persona_prompt
from axc_agent_engine.sidecar.multi_agent.types import MultiAgentEventType, SessionMode

if TYPE_CHECKING:
	from axc_agent_engine.agent import Agent
	from axc_agent_engine.core.dispatcher import AgentMessageDispatcher
	from axc_agent_engine.sidecar.multi_agent.scheduler import Scheduler
	from axc_agent_engine.sidecar.multi_agent.stop_condition import StopCondition

logger = logging.getLogger(__name__)
E = MultiAgentEventType


class MultiAgentEventSink:
	"""构建公开多 Agent 事件和非流式结果文本。
	Builds public multi-agent events and non-stream result text.
	"""

	def round_start(self, round_num: int) -> MultiAgentEvent:
		return MultiAgentEvent(type=E.ROUND_START, round_num=round_num)

	def round_end(self, round_num: int) -> MultiAgentEvent:
		return MultiAgentEvent(type=E.ROUND_END, round_num=round_num)

	def done(self, content: str, round_num: int, metadata: dict | None = None) -> MultiAgentEvent:
		return MultiAgentEvent(type=E.DONE, content=content, round_num=round_num, metadata=metadata or {})

	def message(self, agent_name: str, content: str, round_num: int) -> MultiAgentEvent:
		return MultiAgentEvent(type=E.MESSAGE, agent_name=agent_name, content=content, round_num=round_num)

	def error(self, agent_name: str, content: str, round_num: int) -> MultiAgentEvent:
		return MultiAgentEvent(type=E.ERROR, agent_name=agent_name, content=content, round_num=round_num)

	def result_line(self, event: MultiAgentEvent) -> str:
		if event.type == E.MESSAGE:
			return f"[{event.agent_name}] {event.content}"
		if event.type == E.ERROR:
			return f"[错误] {event.content}"
		return ""


class AgentOrchestrationWorker:
	"""通过 dispatcher 边界发送一个会话轮次请求。
	Sends one session turn through the dispatcher boundary.
	"""

	def __init__(self, dispatcher: "AgentMessageDispatcher", conversation_id: str, session_id: str) -> None:
		self._dispatcher = dispatcher
		self._conversation_id = conversation_id
		self._session_id = session_id

	async def request(self, agent: Any, prompt: str) -> str:
		from axc_agent_engine.core.dispatcher import AgentEnvelope
		envelope = AgentEnvelope(
			sender="session",
			recipient=agent.name,
			content=prompt,
			conversation_id=self._conversation_id,
			trace_id=self._session_id,
		)
		reply = await self._dispatcher.request(envelope, timeout=60.0)
		if reply.type == "error":
			raise RuntimeError(reply.content)
		return reply.content


class SocialFeedBuilder:
	"""根据轮次消息维护 social 模式 feed artifact。
	Maintains the social-mode feed artifact from round messages.
	"""

	def update(self, shared: SharedContext, round_num: int) -> None:
		if "feed" not in shared.artifacts:
			shared.artifacts["feed"] = []
		feed = shared.artifacts["feed"]
		round_msgs = [m for m in shared.messages if m["round"] == round_num]
		for msg in round_msgs:
			if msg["agent"] == "__system__":
				continue
			try:
				action = json.loads(msg["content"])
				action["agent"] = msg["agent"]
				action["round"] = round_num
				feed.append(action)
			except (json.JSONDecodeError, TypeError):
				feed.append({"type": "post", "agent": msg["agent"], "content": msg["content"], "round": round_num})


class MultiAgentSession:
	"""通过 dispatcher/bus 编排多 Agent。
	Orchestrates multiple Agents through dispatcher/bus.
	"""

	def __init__(
		self,
		agents: list["Agent"],
		dispatcher: "AgentMessageDispatcher",
		mode: SessionMode = SessionMode.GROUP_CHAT,
		topic: str = "",
		max_rounds: int = 10,
		supervisor: "Agent | None" = None,
		persona: dict[str, dict] | None = None,
		scheduler: "Scheduler | None" = None,
		stop_condition: "StopCondition | None" = None,
		utility_llm: Any = None,
	) -> None:
		self._agents = agents
		self._dispatcher = dispatcher
		self._mode = mode
		self._topic = topic
		self._max_rounds = max_rounds
		self._supervisor = supervisor
		self._persona = persona or {}
		self._stopped = False
		self._utility_llm = utility_llm
		self._total_speaks = 0
		self._agent_speaks: dict[str, int] = {}
		self._scheduler = scheduler or self._default_scheduler()
		self._stop_condition = stop_condition or self._default_stop_condition()
		self._shared = SharedContext(topic=topic)
		self._session_id = uuid.uuid4().hex[:16]
		self._conversation_id = uuid.uuid4().hex[:12]
		self._events = MultiAgentEventSink()
		self._worker = AgentOrchestrationWorker(self._dispatcher, self._conversation_id, self._session_id)
		self._social_feed = SocialFeedBuilder()

	def stop(self) -> None:
		self._stopped = True

	async def _ensure_persona_generated(self) -> None:
		if not self._persona or not self._utility_llm:
			return
		from axc_agent_engine.sidecar.multi_agent.persona import generate_persona
		for agent_name, persona_data in list(self._persona.items()):
			if isinstance(persona_data, str):
				self._persona[agent_name] = await generate_persona(self._topic, persona_data, self._utility_llm)

	async def run(self) -> str:
		"""非流式执行。
		Run without streaming.
		"""
		result_parts: list[str] = []
		async for event in self._execute():
			if line := self._events.result_line(event):
				result_parts.append(line)
		return "\n\n".join(result_parts)

	async def stream(self) -> AsyncIterator[MultiAgentEvent]:
		"""事件流执行。
		Run and yield events as a stream.
		"""
		async for event in self._execute():
			yield event

	async def _execute(self) -> AsyncIterator[MultiAgentEvent]:
		await self._ensure_persona_generated()
		step = 0
		round_num = 0
		step_in_round = 0
		self._total_speaks = 0
		self._agent_speaks = {}
		all_agents = self._get_all_agents()
		spr = self._get_steps_per_round(all_agents)
		yield self._events.round_start(round_num)
		while not self._stopped:
			should_stop, reason = await self._check_stop(round_num)
			if should_stop:
				yield self._events.done(reason, round_num, self._build_stats())
				return
			speakers = self._scheduler.select_speakers(self._shared, all_agents, step)
			if not speakers:
				yield self._events.done("无可用发言者", round_num, self._build_stats())
				return
			async for event in self._execute_speakers(speakers, round_num):
				yield event
			step += 1
			step_in_round += 1
			if self._mode == SessionMode.SOCIAL:
				self._update_social_feed(round_num)
			if step_in_round >= spr:
				yield self._events.round_end(round_num)
				round_num += 1
				step_in_round = 0
				if not self._stopped:
					yield self._events.round_start(round_num)
		yield self._events.done("手动停止", round_num, self._build_stats())

	async def _execute_speakers(self, speakers: list, round_num: int) -> AsyncIterator[MultiAgentEvent]:
		"""通过 dispatcher.request 执行发言者。"""
		for agent in speakers:
			self._record_speak(agent.name)
			prompt = self._build_prompt(agent)
			try:
				content = await self._worker.request(agent, prompt)
				self._shared.add_message(agent.name, content, round_num)
				yield self._events.message(agent.name, content, round_num)
			except Exception as e:
				yield self._events.error(agent.name, str(e), round_num)

	def _build_prompt(self, agent: Any) -> str:
		"""基于共享上下文和角色设定构建 Agent prompt。"""
		parts: list[str] = [f"讨论主题：{self._shared.topic}"]
		if agent.name in self._persona:
			persona_text = build_persona_prompt(agent.name, self._persona[agent.name])
			if persona_text:
				parts.append(persona_text)
		guidance = mode_prompt_guidance(self._mode) if self._mode != SessionMode.CUSTOM else ""
		if guidance:
			parts.append(f"模式要求：{guidance}")
		history = self._shared.get_history(exclude_agent=agent.name, limit=10)
		for msg in history:
			parts.append(f"[{msg['agent']}] {msg['content']}")
		if self._mode == SessionMode.SOCIAL and "feed" in self._shared.artifacts:
			feed = self._shared.artifacts["feed"]
			if feed:
				parts.append("--- 信息流 ---")
				parts.extend(json.dumps(item, ensure_ascii=False) for item in feed[-5:])
		last = self._shared.get_last_message(exclude_agent=agent.name)
		if last:
			parts.append(f"请回应 [{last['agent']}] 的发言，给出你的观点。")
		else:
			parts.append("请就主题发表你的观点。")
		return "\n".join(parts)

	async def _check_stop(self, round_num: int) -> tuple[bool, str]:
		return await self._stop_condition.should_stop(self._shared, round_num)

	def _get_steps_per_round(self, agents: list) -> int:
		return self._scheduler.steps_per_round(agents)

	def _record_speak(self, agent_name: str) -> None:
		self._total_speaks += 1
		self._agent_speaks[agent_name] = self._agent_speaks.get(agent_name, 0) + 1

	def _build_stats(self) -> dict:
		return {"total_speaks": self._total_speaks, "agent_speaks": dict(self._agent_speaks)}

	def _get_all_agents(self) -> list:
		agents = list(self._agents)
		if self._supervisor and self._supervisor not in agents:
			agents.insert(0, self._supervisor)
		return agents

	def _default_scheduler(self) -> Any:
		return build_scheduler_for_mode(self._mode, self._mode_runtime())

	def _default_stop_condition(self) -> Any:
		return build_stop_condition_for_mode(self._mode, self._mode_runtime())

	def _mode_runtime(self) -> ModeRuntime:
		return ModeRuntime(
			agents=self._agents,
			supervisor=self._supervisor,
			persona=self._persona,
			max_rounds=self._max_rounds,
			utility_llm=self._utility_llm,
		)

	def _update_social_feed(self, round_num: int) -> None:
		self._social_feed.update(self._shared, round_num)
