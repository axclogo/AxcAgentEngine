"""Tests for Engine managing dispatcher lifecycle.

Verifies:
- Engine.load_agent auto-starts consumer
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

from axc_agent_engine.engine import Engine
from axc_agent_engine.tools.name_mapping import ToolNameMappingConfig
from axc_agent_engine.storage.in_memory import InMemoryMessageBus
from axc_agent_engine.core.dispatcher import AgentEnvelope


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


class TestEngineDispatcherLifecycle:
	@pytest.mark.asyncio
	async def test_load_agent_starts_consumer(self):
		"""Engine.load_agent automatically starts dispatcher consumer."""
		bus = InMemoryMessageBus()
		llm = _make_mock_llm()
		engine = Engine(default_llm=llm, message_bus=bus)
		assert engine._dispatcher is not None
		with tempfile.TemporaryDirectory() as tmp:
			path = _write_agent_yaml(tmp)
			agent = engine.load_agent(path)
			# Consumer should be running
			assert agent.name in engine._dispatcher._consumers
			task = engine._dispatcher._consumers[agent.name]
			assert not task.done()
		await engine.close()

	@pytest.mark.asyncio
	async def test_agent_call_works_without_manual_consumer(self):
		"""After load_agent, dispatcher.request works immediately."""
		bus = InMemoryMessageBus()
		llm = _make_mock_llm()
		engine = Engine(default_llm=llm, message_bus=bus)
		with tempfile.TemporaryDirectory() as tmp:
			path = _write_agent_yaml(tmp, "worker")
			agent = engine.load_agent(path)
			# Patch agent.chat to return a known value
			agent.chat = AsyncMock(return_value="worker reply")
			await asyncio.sleep(0.05)
			# Use dispatcher to call the agent
			envelope = AgentEnvelope(sender="caller", recipient="worker", content="do task")
			result = await engine._dispatcher.request(envelope, timeout=5.0)
			assert result.type == "reply"
			assert result.content == "worker reply"
		await engine.close()

	@pytest.mark.asyncio
	async def test_unload_agent_stops_consumer(self):
		"""Engine.unload_agent stops the consumer task."""
		bus = InMemoryMessageBus()
		llm = _make_mock_llm()
		engine = Engine(default_llm=llm, message_bus=bus)
		with tempfile.TemporaryDirectory() as tmp:
			path = _write_agent_yaml(tmp)
			engine.load_agent(path)
			assert "test_agent" in engine._dispatcher._consumers
			await engine.unload_agent("test_agent")
			assert "test_agent" not in engine._dispatcher._consumers
		await engine.close()

	@pytest.mark.asyncio
	async def test_close_stops_all_consumers(self):
		"""Engine.close stops all dispatcher consumers."""
		bus = InMemoryMessageBus()
		llm = _make_mock_llm()
		engine = Engine(default_llm=llm, message_bus=bus)
		with tempfile.TemporaryDirectory() as tmp:
			path1 = _write_agent_yaml(tmp, "agent1")
			path2 = _write_agent_yaml(tmp, "agent2")
			engine.load_agent(path1)
			engine.load_agent(path2)
			assert len(engine._dispatcher._consumers) == 2
			await engine.close()
			assert len(engine._dispatcher._consumers) == 0

	@pytest.mark.asyncio
	async def test_no_dispatcher_without_bus(self):
		"""Engine without message_bus has no dispatcher."""
		llm = _make_mock_llm()
		engine = Engine(default_llm=llm)
		assert engine._dispatcher is None
		await engine.close()

	@pytest.mark.asyncio
	async def test_timeout_when_agent_not_loaded(self):
		"""Request to non-existent agent times out."""
		bus = InMemoryMessageBus()
		llm = _make_mock_llm()
		engine = Engine(default_llm=llm, message_bus=bus)
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
