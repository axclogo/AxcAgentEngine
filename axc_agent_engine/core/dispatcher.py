"""AgentMessageDispatcher — 所有跨 Agent 通信都走 MessageBus。

架构：
- 每个 Agent 都有一个 consumer task（run_agent_consumer），订阅 agent.{name}.inbox
- 调用方通过 dispatcher.request() 发送消息并等待回复
- consumer 接收 envelope 后使用目标 Agent 自己的 runtime 执行，再发到 _reply:{correlation_id}
- consumer 外部不允许直接跨 Agent 调 agent.stream()
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, TYPE_CHECKING

from axc_agent_engine.core.run_context import copy_run_options, dict_or_empty
from axc_agent_engine.runtime.tasks import cancel_and_wait

if TYPE_CHECKING:
	from axc_agent_engine.agent import Agent
	from axc_agent_engine.core.events import Event
	from axc_agent_engine.storage.protocols import MessageBus

logger = logging.getLogger(__name__)

AgentEventCallback = Callable[["AgentEnvelope"], Awaitable[None] | None]


@dataclass
class AgentEnvelope:
	"""Agent 间通信使用的强类型消息信封。"""
	message_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
	conversation_id: str = ""
	correlation_id: str = ""
	sender: str = ""
	recipient: str = ""
	type: str = "request"  # English: request | reply | error | broadcast. 中文：请求 | 回复 | 错误 | 广播。
	content: str = ""
	reply_to: str = ""
	trace_id: str = ""
	run_options: dict[str, Any] = field(default_factory=dict)
	metadata: dict[str, Any] = field(default_factory=dict)

	def __post_init__(self) -> None:
		self.run_options = copy_run_options(self.run_options)
		self.metadata = deepcopy(self.metadata)

	def to_dict(self) -> dict[str, Any]:
		return {
			"message_id": self.message_id,
			"conversation_id": self.conversation_id,
			"correlation_id": self.correlation_id,
			"sender": self.sender,
			"recipient": self.recipient,
			"type": self.type,
			"content": self.content,
			"reply_to": self.reply_to,
			"trace_id": self.trace_id,
			"run_options": copy_run_options(self.run_options),
			"metadata": deepcopy(self.metadata),
		}

	@classmethod
	def from_dict(cls, d: dict[str, Any]) -> "AgentEnvelope":
		if not isinstance(d, dict):
			raise TypeError("AgentEnvelope data must be a dict")
		return cls(
			message_id=d.get("message_id", uuid.uuid4().hex[:12]),
			conversation_id=d.get("conversation_id", ""),
			correlation_id=d.get("correlation_id", ""),
			sender=d.get("sender", ""),
			recipient=d.get("recipient", ""),
			type=d.get("type", "request"),
			content=d.get("content", ""),
			reply_to=d.get("reply_to", ""),
			trace_id=d.get("trace_id", ""),
			run_options=dict_or_empty(d.get("run_options"), "run_options"),
			metadata=dict_or_empty(d.get("metadata"), "metadata"),
		)


class AgentMessageDispatcher:
	"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
通过 MessageBus pub/sub 在 Agent 之间路由消息。

	生命周期：
	1. Engine 使用 MessageBus 创建 dispatcher
	2. 每个已加载 Agent 调用 run_agent_consumer(agent) 启动 consumer
	3. 调用方使用 request(envelope) 发送并等待回复
	4. consumer 收到消息后通过目标 agent.stream() 执行，转发子事件，并用 AgentEnvelope 回复
	"""

	def __init__(self, message_bus: "MessageBus") -> None:
		self._bus = message_bus
		self._consumers: dict[str, asyncio.Task] = {}
		self._pending: dict[str, asyncio.Future[AgentEnvelope]] = {}
		self._event_callbacks: dict[str, AgentEventCallback] = {}

	def run_agent_consumer(self, agent: "Agent") -> asyncio.Task:
		"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
为 Agent 启动 consumer task，订阅 agent.{agent.name}.inbox。

		consumer 会：
		- 从总线接收 AgentEnvelope
		- 通过 agent.stream() 执行，使用目标 Agent 自己的 runtime/LLM
		- 把回复 envelope 发布到 _reply:{correlation_id}
		- 出错时发布 error envelope
		"""
		task = asyncio.create_task(self._consumer_loop(agent))
		self._consumers[agent.name] = task
		return task

	async def stop_consumer(self, agent_name: str) -> None:
		"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
停止指定 Agent 的 consumer task。"""
		task = self._consumers.pop(agent_name, None)
		await cancel_and_wait(task)

	async def stop_all(self) -> None:
		"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
停止所有 consumer task。"""
		for name in list(self._consumers.keys()):
			await self.stop_consumer(name)

	async def request(
		self,
		envelope: AgentEnvelope,
		timeout: float = 60.0,
		event_callback: AgentEventCallback | None = None,
	) -> AgentEnvelope:
		"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
向目标 Agent 发送消息，并等待 correlation_id 匹配的回复。

		消息会发布到 agent.{recipient}.inbox。超时和执行失败都作为 error envelope
		返回，而不是抛出带外异常。
		"""
		correlation_id = uuid.uuid4().hex[:12]
		envelope.correlation_id = correlation_id
		envelope.type = "request"
		reply_channel = f"_reply:{correlation_id}"
		event_channel = f"_events:{correlation_id}"
		loop = asyncio.get_running_loop()
		future: asyncio.Future[AgentEnvelope] = loop.create_future()
		self._pending[correlation_id] = future
		started = time.time()
		if event_callback:
			self._event_callbacks[correlation_id] = event_callback
		listen_task = asyncio.create_task(self._listen_reply(reply_channel, correlation_id))
		event_task = (
			asyncio.create_task(self._listen_events(event_channel, correlation_id))
			if event_callback else None
		)
		try:
			await self._bus.publish(self._agent_channel(envelope.recipient), envelope.to_dict())
			return await asyncio.wait_for(future, timeout=timeout)
		except asyncio.TimeoutError:
			if event_callback:
				timeout_event = _sub_agent_complete_envelope(
					envelope.recipient,
					envelope,
					False,
					started,
					"",
					f"Agent '{envelope.recipient}' 在 {timeout}s 内未响应",
				)
				result = event_callback(timeout_event)
				if hasattr(result, "__await__"):
					await result
			return AgentEnvelope(
				sender=envelope.recipient,
				recipient=envelope.sender,
				type="error",
				content=f"Agent '{envelope.recipient}' 在 {timeout}s 内未响应",
				correlation_id=correlation_id,
				conversation_id=envelope.conversation_id,
				trace_id=envelope.trace_id,
			)
		finally:
			self._pending.pop(correlation_id, None)
			self._event_callbacks.pop(correlation_id, None)
			await cancel_and_wait(listen_task)
			await cancel_and_wait(event_task)

	async def publish(self, envelope: AgentEnvelope) -> None:
		"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
向目标 Agent channel 发布不等待回复的广播消息。"""
		envelope.type = "broadcast"
		await self._bus.publish(self._agent_channel(envelope.recipient), envelope.to_dict())

	async def _consumer_loop(self, agent: "Agent") -> None:
		"""consumer 循环：订阅 agent.{name}.inbox 并处理 envelope。"""
		channel = self._agent_channel(agent.name)
		try:
			async for msg in self._bus.subscribe(channel):
				envelope = AgentEnvelope.from_dict(msg)
				if envelope.type in ("request", "broadcast"):
					await self._handle_envelope(agent, envelope)
		except asyncio.CancelledError:
			pass
		except Exception as e:
			logger.error(f"[dispatcher] Consumer for '{agent.name}' crashed: {e}")

	async def _handle_envelope(self, agent: "Agent", envelope: AgentEnvelope) -> None:
		"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
处理收到的 envelope：通过目标 Agent runtime 执行并回复。"""
		started = time.time()
		try:
			await self._publish_child_event(
				envelope,
				_sub_agent_start_envelope(agent.name, envelope),
			)
			events = await self._run_agent_stream(agent, envelope)
			result_event = _last_event(events, "done")
			error_event = _last_event(events, "error")
			success = result_event is not None and error_event is None
			result = result_event.content if result_event else ""
			error = error_event.content if error_event else ""
			await self._publish_child_event(
				envelope,
				_sub_agent_complete_envelope(agent.name, envelope, success, started, result, error),
			)
			if error_event:
				reply_envelope = AgentEnvelope(
					sender=agent.name,
					recipient=envelope.sender,
					type="error",
					content=error,
					correlation_id=envelope.correlation_id,
					conversation_id=envelope.conversation_id,
					trace_id=envelope.trace_id,
					metadata=envelope.metadata,
				)
			else:
				reply_envelope = AgentEnvelope(
					sender=agent.name,
					recipient=envelope.sender,
					type="reply",
					content=result,
					correlation_id=envelope.correlation_id,
					conversation_id=envelope.conversation_id,
					trace_id=envelope.trace_id,
					metadata=envelope.metadata,
				)
		except Exception as e:
			logger.warning(f"[dispatcher] Agent '{agent.name}' execution failed: {e}")
			await self._publish_child_event(
				envelope,
				_sub_agent_complete_envelope(agent.name, envelope, False, started, "", str(e)),
			)
			reply_envelope = AgentEnvelope(
				sender=agent.name,
				recipient=envelope.sender,
				type="error",
				content=str(e),
				correlation_id=envelope.correlation_id,
				conversation_id=envelope.conversation_id,
				trace_id=envelope.trace_id,
			)
		if envelope.correlation_id:
			reply_channel = f"_reply:{envelope.correlation_id}"
			await self._bus.publish(reply_channel, reply_envelope.to_dict())

	async def _publish_child_event(self, request: AgentEnvelope, event: AgentEnvelope) -> None:
		if not request.correlation_id:
			return
		await self._bus.publish(f"_events:{request.correlation_id}", event.to_dict())

	async def _run_agent_stream(self, agent: "Agent", envelope: AgentEnvelope) -> list["Event"]:
		events: list["Event"] = []
		metadata = deepcopy(envelope.metadata)
		metadata.setdefault("sub_run_id", metadata.get("sub_run_id") or f"{metadata.get('run_id', envelope.trace_id)}:{agent.name}:{envelope.correlation_id}")
		async for event in agent.stream(
			envelope.content,
			session_id=envelope.conversation_id,
			run_options=envelope.run_options,
			metadata=metadata,
		):
			events.append(event)
			if event.type.value != "done":
				await self._publish_child_event(envelope, _sub_agent_step_envelope(agent.name, envelope, event))
		return events

	async def _listen_reply(self, channel: str, correlation_id: str) -> None:
		"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
监听回复 channel，并完成等待中的 future。"""
		try:
			async for msg in self._bus.subscribe(channel):
				if msg.get("correlation_id") == correlation_id:
					future = self._pending.get(correlation_id)
					if future and not future.done():
						future.set_result(AgentEnvelope.from_dict(msg))
					return
		except asyncio.CancelledError:
			pass

	async def _listen_events(self, channel: str, correlation_id: str) -> None:
		try:
			async for msg in self._bus.subscribe(channel):
				if msg.get("correlation_id") != correlation_id:
					continue
				callback = self._event_callbacks.get(correlation_id)
				if callback:
					result = callback(AgentEnvelope.from_dict(msg))
					if hasattr(result, "__await__"):
						await result
		except asyncio.CancelledError:
			pass

	@staticmethod
	def _agent_channel(agent_name: str) -> str:
		return f"agent.{agent_name}.inbox"


def _sub_agent_start_envelope(agent_name: str, envelope: AgentEnvelope) -> AgentEnvelope:
	return _event_envelope(
		envelope,
		"sub_agent_start",
		envelope.content,
		{
			"agent_name": agent_name,
			"agent_id": agent_name,
			"message": envelope.content,
			"sub_run_id": _sub_run_id(agent_name, envelope),
			**_parent_metadata(envelope),
		},
	)


def _sub_agent_step_envelope(agent_name: str, envelope: AgentEnvelope, event: "Event") -> AgentEnvelope:
	return _event_envelope(
		envelope,
		"sub_agent_step",
		event.content,
		{
			"agent_name": agent_name,
			"agent_id": agent_name,
			"sub_run_id": _sub_run_id(agent_name, envelope),
			**_parent_metadata(envelope),
			"step": _event_step(event),
		},
	)


def _sub_agent_complete_envelope(
	agent_name: str,
	envelope: AgentEnvelope,
	success: bool,
	started: float,
	result: str,
	error: str,
) -> AgentEnvelope:
	return _event_envelope(
		envelope,
		"sub_agent_complete",
		result,
		{
			"agent_name": agent_name,
			"agent_id": agent_name,
			"sub_run_id": _sub_run_id(agent_name, envelope),
			"success": success,
			"duration_ms": int((time.time() - started) * 1000),
			"error": error,
			"result_preview": result[:500],
			**_parent_metadata(envelope),
		},
	)


def _event_envelope(source: AgentEnvelope, event_type: str, content: str, metadata: dict[str, Any]) -> AgentEnvelope:
	return AgentEnvelope(
		sender=source.recipient,
		recipient=source.sender,
		type=event_type,
		content=content,
		correlation_id=source.correlation_id,
		conversation_id=source.conversation_id,
		trace_id=source.trace_id,
		metadata=metadata,
	)


def _parent_metadata(envelope: AgentEnvelope) -> dict[str, Any]:
	return {
		"parent_tool_call_id": str(envelope.metadata.get("parent_tool_call_id", "")),
		"run_id": str(envelope.metadata.get("run_id", "")),
		"trace_id": str(envelope.trace_id or envelope.metadata.get("trace_id", "")),
	}


def _event_step(event: "Event") -> dict[str, Any]:
	return {
		"type": event.type.value,
		"tool": event.tool_name,
		"tool_name": event.tool_name,
		"tool_call_id": event.tool_call_id,
		"content": event.content,
		"arguments": event.arguments,
		"artifacts": event.metadata.get("artifacts", []),
		"error": event.metadata.get("error", event.content if event.type.value == "error" else ""),
		"duration_ms": int(event.metadata.get("duration_ms", 0) or 0),
	}


def _sub_run_id(agent_name: str, envelope: AgentEnvelope) -> str:
	raw = envelope.metadata.get("sub_run_id")
	if raw:
		return str(raw)
	parent = str(envelope.metadata.get("run_id") or envelope.trace_id or envelope.conversation_id or "")
	return f"{parent}:{agent_name}:{envelope.correlation_id}" if parent else f"{agent_name}:{envelope.correlation_id}"


def _last_event(events: list["Event"], event_type: str) -> "Event | None":
	return next((event for event in reversed(events) if event.type.value == event_type), None)
