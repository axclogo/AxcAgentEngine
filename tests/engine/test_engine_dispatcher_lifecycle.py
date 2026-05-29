"""Tests for Engine managing dispatcher lifecycle.

Verifies:
- AgentTemplate.instantiate auto-starts consumer
- agent_call works without manual consumer setup
- Engine.unload_agent stops consumer
- Engine.close stops all consumers
- ARCHITECTURE.md does not contain "create_plan tool"
"""
import asyncio
import os
import tempfile
import pytest
from unittest.mock import AsyncMock

from axc_agent_engine.core.schema import LLMMessage, LLMResponse, LLMStreamChunk, LLMUsage
from axc_agent_engine.engine import AgentModels, Engine
from axc_agent_engine.tools.name_mapping import ToolNameMappingConfig
from axc_agent_engine.storage.in_memory import InMemoryMessageBus
from axc_agent_engine.core.dispatcher import AgentEnvelope
from axc_agent_engine.core.events import Event, EventType
from axc_agent_engine.core.schema import ToolDefinition
from axc_agent_engine.plugins.base import BasePlugin
from axc_agent_engine.plugins.builtin.collaboration.plugin import CollaborationPlugin
from axc_agent_engine.plugins.config_schema import config_schema
from axc_agent_engine.plugins.registry import PluginRegistry
from axc_agent_engine.tools.tool_output import ToolOutput


def _make_mock_llm():
	from axc_agent_engine.core.schema import LLMResponse, LLMMessage, LLMUsage
	class MockLLMProvider:
		model = "test"
		tool_name_mapping = ToolNameMappingConfig()

		def __init__(self):
			self.chat = AsyncMock(return_value=LLMResponse(
				message=LLMMessage(content="ok"),
				usage=LLMUsage(input_tokens=1, output_tokens=1),
			))
			self.ask = AsyncMock(return_value="ok")
			self.close = AsyncMock()

		async def stream(self, messages, tools=None, **kwargs):
			if False:
				yield None

	return MockLLMProvider()


def _write_agent_yaml(tmp_dir, name="test_agent"):
	yaml_content = f"""
name: {name}
description: test
system_prompt: "you are a test agent"
runtime:
  max_rounds: 5
plugins: {{}}
"""
	path = os.path.join(tmp_dir, f"{name}.yaml")
	with open(path, "w") as f:
		f.write(yaml_content)
	return path


def _stream_done(content: str):
	async def stream(*args, **kwargs):
		yield Event.done(content)
	return stream


class SequenceLLMProvider:
	model = "test"
	tool_name_mapping = ToolNameMappingConfig()

	def __init__(self, responses: list[LLMResponse]) -> None:
		self.responses = responses
		self.index = 0
		self.ask = AsyncMock(return_value="ok")
		self.close = AsyncMock()

	async def chat(self, messages, tools=None, **kwargs):
		index = min(self.index, len(self.responses) - 1)
		self.index += 1
		return self.responses[index]

	async def stream(self, messages, tools=None, **kwargs):
		response = await self.chat(messages, tools, **kwargs)
		message = response.message
		if message.tool_calls:
			for index, tool_call in enumerate(message.tool_calls):
				yield LLMStreamChunk(tool_call_delta={**tool_call, "index": index})
			return
		if message.content:
			yield LLMStreamChunk(content_delta=message.content, usage=response.usage)


def _response(content: str = "", tool_calls: list[dict] | None = None) -> LLMResponse:
	return LLMResponse(
		message=LLMMessage(content=content, tool_calls=tool_calls or []),
		usage=LLMUsage(input_tokens=1, output_tokens=1),
	)


class ChildToolPlugin(BasePlugin):
	name = "child_tools"
	config_schema = config_schema("child_tools", "Child Tools", "Test child tools.", [])

	def get_tools(self):
		async def child_tool(args, ctx):
			return ToolOutput.text("child tool result")
		return [ToolDefinition(name="child_tool", description="child tool", execute=child_tool)]


class TestEngineDispatcherLifecycle:
	@pytest.mark.asyncio
	async def test_instantiate_starts_consumer(self):
		"""AgentTemplate.instantiate automatically starts dispatcher consumer."""
		bus = InMemoryMessageBus()
		llm = _make_mock_llm()
		engine = Engine(message_bus=bus)
		assert engine._dispatcher is not None
		with tempfile.TemporaryDirectory() as tmp:
			path = _write_agent_yaml(tmp)
			agent = engine.load_agent_template(path).instantiate(models=AgentModels(default=llm))
			# Consumer should be running
			assert agent.name in engine._dispatcher._consumers
			task = engine._dispatcher._consumers[agent.name]
			assert not task.done()
		await engine.close()

	@pytest.mark.asyncio
	async def test_agent_call_works_without_manual_consumer(self):
		"""After instantiate, dispatcher.request works immediately."""
		bus = InMemoryMessageBus()
		llm = _make_mock_llm()
		engine = Engine(message_bus=bus)
		with tempfile.TemporaryDirectory() as tmp:
			path = _write_agent_yaml(tmp, "worker")
			agent = engine.load_agent_template(path).instantiate(models=AgentModels(default=llm))
			agent.stream = _stream_done("worker reply")
			await asyncio.sleep(0.05)
			# Use dispatcher to call the agent
			envelope = AgentEnvelope(sender="caller", recipient="worker", content="do task")
			result = await engine._dispatcher.request(envelope, timeout=5.0)
			assert result.type == "reply"
			assert result.content == "worker reply"
		await engine.close()

	@pytest.mark.asyncio
	async def test_agent_call_stream_forwards_child_agent_events(self):
		"""Parent stream receives child agent details from collaboration.agent_call."""
		registry = PluginRegistry()
		registry.register(CollaborationPlugin)
		registry.register(ChildToolPlugin)
		bus = InMemoryMessageBus()
		engine = Engine(message_bus=bus, plugin_registry=registry)
		parent_model = SequenceLLMProvider([
			_response(tool_calls=[{
				"id": "parent-call",
				"function": {
					"name": "agent_call",
					"arguments": '{"agent_name":"worker","message":"search it","timeout":5}',
				},
			}]),
			_response("parent done"),
		])
		child_model = SequenceLLMProvider([
			_response(tool_calls=[{"id": "child-tool", "function": {"name": "child_tool", "arguments": "{}"}}]),
			_response("child done"),
		])
		with tempfile.TemporaryDirectory() as tmp:
			worker_path = os.path.join(tmp, "worker.yaml")
			with open(worker_path, "w", encoding="utf-8") as f:
				f.write("""
name: worker
description: test worker
system_prompt: worker
runtime:
  max_rounds: 5
plugins:
  child_tools:
    enabled: true
""")
			parent_path = os.path.join(tmp, "parent.yaml")
			with open(parent_path, "w", encoding="utf-8") as f:
				f.write("""
name: parent
description: test parent
system_prompt: parent
runtime:
  max_rounds: 5
  allowed_capabilities:
    - agent_call
plugins:
  collaboration:
    enabled: true
    timeout: 5
    allow_self_call: false
""")
			engine.load_agent_template(worker_path).instantiate(models=AgentModels(default=child_model))
			parent = engine.load_agent_template(parent_path).instantiate(models=AgentModels(default=parent_model))
			await asyncio.sleep(0.05)
			events = [
				event async for event in parent.stream_with_messages(
					[{"role": "user", "content": "delegate"}],
					session_id="s1",
				)
			]
		await engine.close()
		sub_events = [event for event in events if event.type in EventType.__members__.values()]
		start = next(event for event in events if event.type == EventType.SUB_AGENT_START)
		tool_call = next(
			event for event in events
			if event.type == EventType.SUB_AGENT_STEP and event.metadata["step"]["type"] == "tool_call"
		)
		tool_result = next(
			event for event in events
			if event.type == EventType.SUB_AGENT_STEP and event.metadata["step"]["type"] == "tool_result"
		)
		complete = next(event for event in events if event.type == EventType.SUB_AGENT_COMPLETE)
		assert sub_events
		assert start.metadata["agent_name"] == "worker"
		assert tool_call.metadata["parent_tool_call_id"] == "parent-call"
		assert tool_call.metadata["step"]["tool"] == "child_tool"
		assert tool_result.metadata["step"]["content"] == "child tool result"
		assert complete.metadata["success"] is True

	@pytest.mark.asyncio
	async def test_unload_agent_stops_consumer(self):
		"""Engine.unload_agent stops the consumer task."""
		bus = InMemoryMessageBus()
		llm = _make_mock_llm()
		engine = Engine(message_bus=bus)
		with tempfile.TemporaryDirectory() as tmp:
			path = _write_agent_yaml(tmp)
			engine.load_agent_template(path).instantiate(models=AgentModels(default=llm))
			assert "test_agent" in engine._dispatcher._consumers
			await engine.unload_agent("test_agent")
			assert "test_agent" not in engine._dispatcher._consumers
		await engine.close()

	@pytest.mark.asyncio
	async def test_close_stops_all_consumers(self):
		"""Engine.close stops all dispatcher consumers."""
		bus = InMemoryMessageBus()
		llm = _make_mock_llm()
		engine = Engine(message_bus=bus)
		with tempfile.TemporaryDirectory() as tmp:
			path1 = _write_agent_yaml(tmp, "agent1")
			path2 = _write_agent_yaml(tmp, "agent2")
			engine.load_agent_template(path1).instantiate(models=AgentModels(default=llm))
			engine.load_agent_template(path2).instantiate(models=AgentModels(default=llm))
			assert len(engine._dispatcher._consumers) == 2
			await engine.close()
			assert len(engine._dispatcher._consumers) == 0

	@pytest.mark.asyncio
	async def test_no_dispatcher_without_bus(self):
		"""Engine without message_bus has no dispatcher."""
		llm = _make_mock_llm()
		engine = Engine()
		assert engine._dispatcher is None
		await engine.close()

	@pytest.mark.asyncio
	async def test_timeout_when_agent_not_loaded(self):
		"""Request to non-existent agent times out."""
		bus = InMemoryMessageBus()
		llm = _make_mock_llm()
		engine = Engine(message_bus=bus)
		envelope = AgentEnvelope(sender="x", recipient="ghost", content="hi")
		result = await engine._dispatcher.request(envelope, timeout=0.2)
		assert result.type == "error"
		assert "未响应" in result.content
		await engine.close()


class TestArchitectureDoc:
	def test_no_create_plan_tool_in_architecture(self):
		"""docs/ARCHITECTURE.md must not describe create_plan as a tool."""
		doc_path = os.path.join(os.path.dirname(__file__), "..", "docs", "ARCHITECTURE.md")
		if not os.path.exists(doc_path):
			pytest.skip("ARCHITECTURE.md not found")
		content = open(doc_path).read()
		assert "create_plan tool" not in content
		assert "Triggered by LLM calling create_plan" not in content

	def test_architecture_describes_transaction_router(self):
		"""docs/ARCHITECTURE.md must describe TransactionRouter."""
		doc_path = os.path.join(os.path.dirname(__file__), "..", "docs", "ARCHITECTURE.md")
		if not os.path.exists(doc_path):
			pytest.skip("ARCHITECTURE.md not found")
		content = open(doc_path).read()
		assert "TransactionRouter" in content
		assert "routing policy" in content

	def test_architecture_describes_dispatcher_consumer(self):
		"""docs/ARCHITECTURE.md must describe dispatcher consumer pattern."""
		doc_path = os.path.join(os.path.dirname(__file__), "..", "docs", "ARCHITECTURE.md")
		if not os.path.exists(doc_path):
			pytest.skip("ARCHITECTURE.md not found")
		content = open(doc_path).read()
		assert "run_agent_consumer" in content or "consumer" in content
		assert "MessageBus" in content
		assert "correlation_id" in content
