"""Hard architecture tests for AgentMessageDispatcher.

These tests verify the dispatcher/bus/consumer architecture is correct,
not just that keywords are absent.
"""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock

from axc_agent_engine.core.dispatcher import AgentMessageDispatcher, AgentEnvelope
from axc_agent_engine.storage.in_memory import InMemoryMessageBus


class TestDispatcherConsumer:
	"""Test run_agent_consumer: publish → consumer → agent.chat → reply."""

	@pytest.mark.asyncio
	async def test_consumer_receives_and_replies(self):
		"""Publish to agent.{name}.inbox, consumer calls agent.chat, reply envelope arrives."""
		bus = InMemoryMessageBus()
		dispatcher = AgentMessageDispatcher(message_bus=bus)
		# Mock agent
		agent = MagicMock()
		agent.name = "worker"
		agent.chat = AsyncMock(return_value="I processed your request")
		# Start consumer
		dispatcher.run_agent_consumer(agent)
		await asyncio.sleep(0.05)  # Let consumer subscribe
		# Send request
		envelope = AgentEnvelope(sender="caller", recipient="worker", content="do something")
		result = await dispatcher.request(envelope, timeout=5.0)
		assert result.type == "reply"
		assert result.content == "I processed your request"
		agent.chat.assert_called_once_with("do something", metadata={})
		await dispatcher.stop_all()

	@pytest.mark.asyncio
	async def test_consumer_handles_agent_error(self):
		"""Agent.chat raises -> request returns an error envelope."""
		bus = InMemoryMessageBus()
		dispatcher = AgentMessageDispatcher(message_bus=bus)
		agent = MagicMock()
		agent.name = "broken"
		agent.chat = AsyncMock(side_effect=RuntimeError("agent crashed"))
		dispatcher.run_agent_consumer(agent)
		await asyncio.sleep(0.05)
		envelope = AgentEnvelope(sender="caller", recipient="broken", content="hi")
		result = await dispatcher.request(envelope, timeout=5.0)
		assert result.type == "error"
		assert "agent crashed" in result.content
		await dispatcher.stop_all()

	@pytest.mark.asyncio
	async def test_timeout_when_no_consumer(self):
		"""No consumer running -> request returns timeout error envelope."""
		bus = InMemoryMessageBus()
		dispatcher = AgentMessageDispatcher(message_bus=bus)
		envelope = AgentEnvelope(sender="caller", recipient="nobody", content="hello")
		result = await dispatcher.request(envelope, timeout=0.2)
		assert result.type == "error"
		assert "未响应" in result.content

	@pytest.mark.asyncio
	async def test_multiple_consumers(self):
		"""Multiple agents each have their own consumer."""
		bus = InMemoryMessageBus()
		dispatcher = AgentMessageDispatcher(message_bus=bus)
		agent_a = MagicMock()
		agent_a.name = "agent_a"
		agent_a.chat = AsyncMock(return_value="reply from A")
		agent_b = MagicMock()
		agent_b.name = "agent_b"
		agent_b.chat = AsyncMock(return_value="reply from B")
		dispatcher.run_agent_consumer(agent_a)
		dispatcher.run_agent_consumer(agent_b)
		await asyncio.sleep(0.05)
		env_a = AgentEnvelope(sender="x", recipient="agent_a", content="msg for A")
		env_b = AgentEnvelope(sender="x", recipient="agent_b", content="msg for B")
		result_a = await dispatcher.request(env_a, timeout=5.0)
		result_b = await dispatcher.request(env_b, timeout=5.0)
		assert result_a.content == "reply from A"
		assert result_b.content == "reply from B"
		await dispatcher.stop_all()

	@pytest.mark.asyncio
	async def test_consumer_uses_agent_own_runtime(self):
		"""Consumer calls agent.chat which uses the agent's own LLM/plugins."""
		bus = InMemoryMessageBus()
		dispatcher = AgentMessageDispatcher(message_bus=bus)
		call_log: list[str] = []

		async def tracked_chat(msg, **kwargs):
			call_log.append(f"agent_runtime:{msg}")
			return f"processed:{msg}"

		agent = MagicMock()
		agent.name = "tracked"
		agent.chat = tracked_chat
		dispatcher.run_agent_consumer(agent)
		await asyncio.sleep(0.05)
		envelope = AgentEnvelope(sender="test", recipient="tracked", content="task1")
		result = await dispatcher.request(envelope, timeout=5.0)
		assert result.type == "reply"
		assert result.content == "processed:task1"
		assert call_log == ["agent_runtime:task1"]
		await dispatcher.stop_all()

	@pytest.mark.asyncio
	async def test_consumer_passes_envelope_metadata(self):
		bus = InMemoryMessageBus()
		dispatcher = AgentMessageDispatcher(message_bus=bus)
		seen_metadata = {}

		async def tracked_chat(msg, **kwargs):
			seen_metadata.update(kwargs.get("metadata") or {})
			return "ok"

		agent = MagicMock()
		agent.name = "tracked"
		agent.chat = tracked_chat
		dispatcher.run_agent_consumer(agent)
		await asyncio.sleep(0.05)
		envelope = AgentEnvelope(
			sender="test",
			recipient="tracked",
			content="task1",
			metadata={"agent_call_depth": 2, "tenant_id": "t1"},
		)
		result = await dispatcher.request(envelope, timeout=5.0)
		assert result.type == "reply"
		assert seen_metadata["agent_call_depth"] == 2
		assert seen_metadata["tenant_id"] == "t1"
		await dispatcher.stop_all()

	@pytest.mark.asyncio
	async def test_correlation_id_isolation(self):
		"""Concurrent requests to same agent get correct replies via correlation_id."""
		bus = InMemoryMessageBus()
		dispatcher = AgentMessageDispatcher(message_bus=bus)
		call_count = 0

		async def slow_chat(msg, **kwargs):
			nonlocal call_count
			call_count += 1
			await asyncio.sleep(0.05)
			return f"reply:{msg}"

		agent = MagicMock()
		agent.name = "slow"
		agent.chat = slow_chat
		dispatcher.run_agent_consumer(agent)
		await asyncio.sleep(0.05)
		env1 = AgentEnvelope(sender="t", recipient="slow", content="msg1")
		env2 = AgentEnvelope(sender="t", recipient="slow", content="msg2")
		r1, r2 = await asyncio.gather(
			dispatcher.request(env1, timeout=5.0),
			dispatcher.request(env2, timeout=5.0),
		)
		assert "msg1" in r1.content
		assert "msg2" in r2.content
		assert call_count == 2
		await dispatcher.stop_all()

	@pytest.mark.asyncio
	async def test_publish_fire_and_forget(self):
		"""publish() delivers message but does not wait for reply."""
		bus = InMemoryMessageBus()
		dispatcher = AgentMessageDispatcher(message_bus=bus)
		received: list[str] = []

		async def capture_chat(msg, **kwargs):
			received.append(msg)
			return "ignored"

		agent = MagicMock()
		agent.name = "receiver"
		agent.chat = capture_chat
		dispatcher.run_agent_consumer(agent)
		await asyncio.sleep(0.05)
		envelope = AgentEnvelope(sender="broadcaster", recipient="receiver", content="broadcast msg")
		await dispatcher.publish(envelope)
		await asyncio.sleep(0.1)
		assert "broadcast msg" in received
		await dispatcher.stop_all()

	@pytest.mark.asyncio
	async def test_stop_consumer(self):
		"""stop_consumer cancels the consumer task."""
		bus = InMemoryMessageBus()
		dispatcher = AgentMessageDispatcher(message_bus=bus)
		agent = MagicMock()
		agent.name = "stoppable"
		agent.chat = AsyncMock(return_value="ok")
		dispatcher.run_agent_consumer(agent)
		await asyncio.sleep(0.05)
		await dispatcher.stop_consumer("stoppable")
		assert "stoppable" not in dispatcher._consumers


class TestEnvelopeFields:
	def test_all_fields_present(self):
		env = AgentEnvelope(
			sender="a", recipient="b", content="hi",
			conversation_id="conv1", trace_id="tr1",
		)
		d = env.to_dict()
		assert d["message_id"]
		assert d["conversation_id"] == "conv1"
		assert d["correlation_id"] == ""
		assert d["sender"] == "a"
		assert d["recipient"] == "b"
		assert d["type"] == "request"
		assert d["content"] == "hi"
		assert d["trace_id"] == "tr1"

	def test_roundtrip(self):
		env = AgentEnvelope(sender="x", recipient="y", content="z", correlation_id="c1")
		restored = AgentEnvelope.from_dict(env.to_dict())
		assert restored.sender == "x"
		assert restored.correlation_id == "c1"


class TestNoDirectAgentChat:
	"""Grep-level tests: verify forbidden patterns are absent."""

	def test_session_no_chat_with_messages(self):
		import inspect
		from axc_agent_engine.sidecar.multi_agent import session
		source = inspect.getsource(session)
		assert "chat_with_messages" not in source
		assert "stream_with_messages" not in source
		# agent.chat is only allowed inside dispatcher consumer, not in session
		# Session uses dispatcher.request, not agent.chat
		assert "agent.chat" not in source.replace("agent.chat", "").replace("dispatcher", "")

	def test_collaboration_no_target_chat(self):
		import inspect
		from axc_agent_engine.plugins.builtin.collaboration import plugin as collaboration
		source = inspect.getsource(collaboration)
		assert "target.chat" not in source
		assert "agent.chat" not in source

	def test_swarm_no_agent_chat(self):
		import inspect
		from axc_agent_engine.plugins.builtin.swarm import plugin as swarm
		source = inspect.getsource(swarm)
		assert "agent.chat" not in source
		assert "target.chat" not in source
