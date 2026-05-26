"""AgentMessageDispatcher — 所有跨 Agent 通信都走 MessageBus。

架构：
- 每个 Agent 都有一个 consumer task（run_agent_consumer），订阅 agent.{name}.inbox
- 调用方通过 dispatcher.request() 发送消息并等待回复
- consumer 接收 envelope 后使用目标 Agent 自己的 runtime 执行，再发到 _reply:{correlation_id}
- consumer 外部不允许直接跨 Agent 调 agent.chat()
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
	from axc_agent_engine.agent import Agent
	from axc_agent_engine.storage.protocols import MessageBus

logger = logging.getLogger(__name__)


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
	metadata: dict[str, Any] = field(default_factory=dict)

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
			"metadata": self.metadata,
		}

	@classmethod
	def from_dict(cls, d: dict[str, Any]) -> "AgentEnvelope":
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
			metadata=d.get("metadata", {}),
		)


class AgentMessageDispatcher:
	"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
通过 MessageBus pub/sub 在 Agent 之间路由消息。

	生命周期：
	1. Engine 使用 MessageBus 创建 dispatcher
	2. 每个已加载 Agent 调用 run_agent_consumer(agent) 启动 consumer
	3. 调用方使用 request(envelope) 发送并等待回复
	4. consumer 收到消息后通过目标 agent.chat() 执行，并用 AgentEnvelope 回复
	"""

	def __init__(self, message_bus: "MessageBus") -> None:
		self._bus = message_bus
		self._consumers: dict[str, asyncio.Task] = {}
		self._pending: dict[str, asyncio.Future[AgentEnvelope]] = {}

	def run_agent_consumer(self, agent: "Agent") -> asyncio.Task:
		"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
为 Agent 启动 consumer task，订阅 agent.{agent.name}.inbox。

		consumer 会：
		- 从总线接收 AgentEnvelope
		- 通过 agent.chat() 执行，使用目标 Agent 自己的 runtime/LLM
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
		if task and not task.done():
			task.cancel()
			try:
				await task
			except (asyncio.CancelledError, Exception):
				pass

	async def stop_all(self) -> None:
		"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
停止所有 consumer task。"""
		for name in list(self._consumers.keys()):
			await self.stop_consumer(name)

	async def request(self, envelope: AgentEnvelope, timeout: float = 60.0) -> AgentEnvelope:
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
		loop = asyncio.get_running_loop()
		future: asyncio.Future[AgentEnvelope] = loop.create_future()
		self._pending[correlation_id] = future
		listen_task = asyncio.create_task(self._listen_reply(reply_channel, correlation_id))
		try:
			await self._bus.publish(self._agent_channel(envelope.recipient), envelope.to_dict())
			return await asyncio.wait_for(future, timeout=timeout)
		except asyncio.TimeoutError:
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
			listen_task.cancel()
			try:
				await listen_task
			except (asyncio.CancelledError, Exception):
				pass

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
		try:
			if envelope.conversation_id:
				result = await agent.chat(
					envelope.content,
					session_id=envelope.conversation_id,
					metadata=envelope.metadata,
				)
			else:
				result = await agent.chat(envelope.content, metadata=envelope.metadata)
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

	@staticmethod
	def _agent_channel(agent_name: str) -> str:
		return f"agent.{agent_name}.inbox"
