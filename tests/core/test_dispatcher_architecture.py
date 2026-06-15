"""Hard architecture tests for AgentMessageDispatcher.

These tests verify the dispatcher/bus/consumer architecture is correct,
not just that keywords are absent.
"""
import asyncio
import pytest
from unittest.mock import MagicMock

from axc_agent_engine.core.events import Event, EventType
from axc_agent_engine.core.dispatcher import AgentMessageDispatcher, AgentEnvelope
from axc_agent_engine.storage.in_memory import InMemoryMessageBus


def _stream_events(events):
	async def stream(*args, **kwargs):
		for event in events:
			yield event
	return stream


def _raise_stream(error):
	async def stream(*args, **kwargs):
		raise error
		yield Event.done("")
	return stream


async def _slow_stream(*args, **kwargs):
	await asyncio.sleep(10)
	yield Event.done("late")


class TestDispatcherConsumer:
	"""Test run_agent_consumer: publish -> consumer -> agent.stream -> reply."""

	@pytest.mark.asyncio
	async def test_consumer_receives_and_replies(self):
		"""Publish to agent.{name}.inbox, consumer calls agent.stream, reply envelope arrives."""
		bus = InMemoryMessageBus()
		dispatcher = AgentMessageDispatcher(message_bus=bus)
		# Mock agent
		agent = MagicMock()
		agent.name = "worker"
		agent.stream = _stream_events([Event.done("I processed your request")])
		# Start consumer
		dispatcher.run_agent_consumer(agent)
		await asyncio.sleep(0.05)  # Let consumer subscribe
		# Send request
		envelope = AgentEnvelope(sender="caller", recipient="worker", content="do something")
		result = await dispatcher.request(envelope, timeout=5.0)
		assert result.type == "reply"
		assert result.content == "I processed your request"
		await dispatcher.stop_all()

	@pytest.mark.asyncio
	async def test_consumer_handles_agent_error(self):
		"""agent.stream raises -> request returns an error envelope."""
		bus = InMemoryMessageBus()
		dispatcher = AgentMessageDispatcher(message_bus=bus)
		agent = MagicMock()
		agent.name = "broken"
		agent.stream = _raise_stream(RuntimeError("agent crashed"))
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
		agent_a.stream = _stream_events([Event.done("reply from A")])
		agent_b = MagicMock()
		agent_b.name = "agent_b"
		agent_b.stream = _stream_events([Event.done("reply from B")])
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
		"""Consumer calls agent.stream which uses the agent's own LLM/plugins."""
		bus = InMemoryMessageBus()
		dispatcher = AgentMessageDispatcher(message_bus=bus)
		call_log: list[str] = []

		async def tracked_stream(msg, **kwargs):
			call_log.append(f"agent_runtime:{msg}")
			yield Event.done(f"processed:{msg}")

		agent = MagicMock()
		agent.name = "tracked"
		agent.stream = tracked_stream
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

		async def tracked_stream(msg, **kwargs):
			seen_metadata.update(kwargs.get("metadata") or {})
			yield Event.done("ok")

		agent = MagicMock()
		agent.name = "tracked"
		agent.stream = tracked_stream
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
	async def test_consumer_metadata_mutation_does_not_pollute_envelope(self):
		bus = InMemoryMessageBus()
		dispatcher = AgentMessageDispatcher(message_bus=bus)

		async def mutating_stream(msg, **kwargs):
			kwargs["metadata"]["nested"]["value"] = "mutated"
			yield Event.done("ok")

		agent = MagicMock()
		agent.name = "tracked"
		agent.stream = mutating_stream
		dispatcher.run_agent_consumer(agent)
		await asyncio.sleep(0.05)
		envelope = AgentEnvelope(
			sender="test",
			recipient="tracked",
			content="task1",
			metadata={"nested": {"value": "original"}},
		)
		result = await dispatcher.request(envelope, timeout=5.0)

		assert envelope.metadata == {"nested": {"value": "original"}}
		assert result.metadata == {"nested": {"value": "original"}}
		await dispatcher.stop_all()

	@pytest.mark.asyncio
	async def test_correlation_id_isolation(self):
		"""Concurrent requests to same agent get correct replies via correlation_id."""
		bus = InMemoryMessageBus()
		dispatcher = AgentMessageDispatcher(message_bus=bus)
		call_count = 0

		async def slow_stream(msg, **kwargs):
			nonlocal call_count
			call_count += 1
			await asyncio.sleep(0.05)
			yield Event.done(f"reply:{msg}")

		agent = MagicMock()
		agent.name = "slow"
		agent.stream = slow_stream
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

		async def capture_stream(msg, **kwargs):
			received.append(msg)
			yield Event.done("ignored")

		agent = MagicMock()
		agent.name = "receiver"
		agent.stream = capture_stream
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
		agent.stream = _stream_events([Event.done("ok")])
		dispatcher.run_agent_consumer(agent)
		await asyncio.sleep(0.05)
		await dispatcher.stop_consumer("stoppable")
		assert "stoppable" not in dispatcher._consumers

	@pytest.mark.asyncio
	async def test_request_forwards_sub_agent_events(self):
		bus = InMemoryMessageBus()
		dispatcher = AgentMessageDispatcher(message_bus=bus)
		agent = MagicMock()
		agent.name = "worker"
		agent.stream = _stream_events([
			Event.tool_call("search", "child-tool", {"q": "x"}),
			Event.tool_result("search", "child-tool", "ok"),
			Event.done("done"),
		])
		seen = []
		dispatcher.run_agent_consumer(agent)
		await asyncio.sleep(0.05)
		envelope = AgentEnvelope(
			sender="caller",
			recipient="worker",
			content="do",
			metadata={"parent_tool_call_id": "parent-call", "run_id": "run-1"},
		)
		result = await dispatcher.request(envelope, timeout=5.0, event_callback=seen.append)
		assert result.type == "reply"
		types = [item.type for item in seen]
		assert "sub_agent_start" in types
		assert "sub_agent_step" in types
		assert "sub_agent_complete" in types
		tool_step = next(item for item in seen if item.metadata.get("step", {}).get("type") == "tool_call")
		assert tool_step.metadata["parent_tool_call_id"] == "parent-call"
		assert tool_step.metadata["sub_run_id"].startswith("run-1:worker:")
		assert tool_step.metadata["step"]["tool"] == "search"
		assert tool_step.metadata["step"]["tool_name"] == "search"
		assert tool_step.metadata["step"]["tool_call_id"] == "child-tool"
		assert tool_step.metadata["step"]["artifacts"] == []
		result_step = next(item for item in seen if item.metadata.get("step", {}).get("type") == "tool_result")
		assert result_step.metadata["step"]["tool"] == "search"
		assert result_step.metadata["step"]["content"] == "ok"
		assert result_step.metadata["step"]["error"] == ""
		complete = next(item for item in seen if item.type == "sub_agent_complete")
		assert complete.metadata["success"] is True
		assert complete.metadata["parent_tool_call_id"] == "parent-call"
		assert complete.metadata["sub_run_id"].startswith("run-1:worker:")
		await dispatcher.stop_all()

	@pytest.mark.asyncio
	async def test_request_forwards_failed_sub_agent_complete(self):
		bus = InMemoryMessageBus()
		dispatcher = AgentMessageDispatcher(message_bus=bus)
		agent = MagicMock()
		agent.name = "worker"
		agent.stream = _stream_events([Event.error("failed")])
		seen = []
		dispatcher.run_agent_consumer(agent)
		await asyncio.sleep(0.05)
		result = await dispatcher.request(
			AgentEnvelope(sender="caller", recipient="worker", content="do"),
			timeout=5.0,
			event_callback=seen.append,
		)
		assert result.type == "error"
		complete = next(item for item in seen if item.type == "sub_agent_complete")
		assert complete.metadata["success"] is False
		assert complete.metadata["error"] == "failed"
		assert complete.metadata["agent_id"] == "worker"
		await dispatcher.stop_all()

	@pytest.mark.asyncio
	async def test_request_forwards_timeout_sub_agent_complete(self):
		bus = InMemoryMessageBus()
		dispatcher = AgentMessageDispatcher(message_bus=bus)
		agent = MagicMock()
		agent.name = "worker"
		agent.stream = _slow_stream
		seen = []
		dispatcher.run_agent_consumer(agent)
		await asyncio.sleep(0.05)
		result = await dispatcher.request(
			AgentEnvelope(sender="caller", recipient="worker", content="do"),
			timeout=0.05,
			event_callback=seen.append,
		)
		await asyncio.sleep(0.05)
		assert result.type == "error"
		assert any(item.type == "sub_agent_start" for item in seen)
		complete = next(item for item in seen if item.type == "sub_agent_complete")
		assert complete.metadata["success"] is False
		assert "未响应" in complete.metadata["error"]
		await dispatcher.stop_all()


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
		assert d["run_options"] == {}

	def test_direct_creation_copies_context_fields(self):
		run_options = {"control": {"stream": False}}
		metadata = {"trace": {"span": "s1"}}
		env = AgentEnvelope(run_options=run_options, metadata=metadata)

		run_options["control"]["stream"] = True
		metadata["trace"]["span"] = "mutated"

		assert env.run_options == {"control": {"stream": False}}
		assert env.metadata == {"trace": {"span": "s1"}}

	def test_runtime_run_options_preserve_identity(self):
		class RuntimeQueue:
			def __deepcopy__(self, memo):
				raise RuntimeError("runtime queues are not copyable")

		approval_queue = RuntimeQueue()
		response_queue = RuntimeQueue()
		run_options = {
			"approval_queue": approval_queue,
			"response_queue": response_queue,
			"control": {"stream": False},
		}

		env = AgentEnvelope(run_options=run_options)
		payload = env.to_dict()

		run_options["control"]["stream"] = True
		env.run_options["control"]["stream"] = True

		assert env.run_options["approval_queue"] is approval_queue
		assert env.run_options["response_queue"] is response_queue
		assert payload["run_options"]["approval_queue"] is approval_queue
		assert payload["run_options"]["response_queue"] is response_queue
		assert payload["run_options"]["control"] == {"stream": False}

	def test_roundtrip(self):
		env = AgentEnvelope(sender="x", recipient="y", content="z", correlation_id="c1", run_options={"run_id": "r1"})
		restored = AgentEnvelope.from_dict(env.to_dict())
		assert restored.sender == "x"
		assert restored.correlation_id == "c1"
		assert restored.run_options == {"run_id": "r1"}

	def test_to_dict_copies_context_fields(self):
		env = AgentEnvelope(
			sender="x",
			recipient="y",
			content="z",
			run_options={"run_id": "r1", "control": {"stream": False}},
			metadata={"trace_id": "t1", "trace": {"span": "s1"}},
		)
		payload = env.to_dict()

		env.run_options["run_id"] = "mutated"
		env.metadata["trace_id"] = "mutated"
		env.run_options["control"]["stream"] = True
		env.metadata["trace"]["span"] = "mutated"

		assert payload["run_options"] == {"run_id": "r1", "control": {"stream": False}}
		assert payload["metadata"] == {"trace_id": "t1", "trace": {"span": "s1"}}

	def test_from_dict_rejects_invalid_context_types(self):
		with pytest.raises(TypeError, match="AgentEnvelope data must be a dict"):
			AgentEnvelope.from_dict([("sender", "x")])
		with pytest.raises(TypeError, match="run_options must be a dict"):
			AgentEnvelope.from_dict({"run_options": [("run_id", "r1")]})
		with pytest.raises(TypeError, match="metadata must be a dict"):
			AgentEnvelope.from_dict({"metadata": [("run_id", "r1")]})


class TestNoDirectAgentChat:
	"""Grep-level tests: verify forbidden patterns are absent."""

	def test_session_no_chat_with_messages(self):
		import inspect
		from axc_agent_engine.sidecar.multi_agent import session
		source = inspect.getsource(session)
		assert "chat_with_messages" not in source
		assert "stream_with_messages" not in source
		# Sessions use dispatcher.request; dispatcher consumers execute target agent.stream.
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
