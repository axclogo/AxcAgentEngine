"""Shared test fixtures."""
import pytest
from unittest.mock import AsyncMock
from axc_agent_engine.core.context import ExecutionConfig, ExecutionState, ExecutionContext, ExecutionServices
from axc_agent_engine.core.schema import ToolDefinition
from axc_agent_engine.plugins import PluginContext
from axc_agent_engine.tools.registry import ToolRegistry
from axc_agent_engine.tools.tool_output import ToolOutput
from axc_agent_engine.storage.in_memory import InMemoryKVStore, InMemoryMessagePersistence, InMemorySpanStore
from axc_agent_engine.storage.result_store import InMemoryResultStore


@pytest.fixture
def exec_config():
	return ExecutionConfig(system_prompt="test", max_rounds=10, stream=True)


@pytest.fixture
def exec_state():
	return ExecutionState()


@pytest.fixture
def exec_ctx(exec_config, exec_state):
	return ExecutionContext(config=exec_config, state=exec_state)


@pytest.fixture
def result_store():
	return InMemoryResultStore()


@pytest.fixture
def exec_ctx_with_services(exec_config, exec_state, result_store):
	services = ExecutionServices(result_store=result_store)
	return ExecutionContext(config=exec_config, state=exec_state, services=services)


@pytest.fixture
def mock_llm():
	from axc_agent_engine.core.schema import LLMResponse, LLMMessage, LLMUsage
	class MockLLMProvider:
		model = "test-model"
		tool_name_mapping = None

		def __init__(self):
			self.chat = AsyncMock(return_value=LLMResponse(
				message=LLMMessage(role="assistant", content="hello"),
				usage=LLMUsage(input_tokens=10, output_tokens=5),
			))
			self.ask = AsyncMock(return_value="test response")
			self.close = AsyncMock()

		async def stream(self, messages, tools=None, **kwargs):
			if False:
				yield None

	return MockLLMProvider()


@pytest.fixture
def plugin_ctx(mock_llm):
	return PluginContext(
		default_llm=mock_llm,
		utility_llm=mock_llm,
		kv_store=InMemoryKVStore(),
		message_persistence=InMemoryMessagePersistence(),
		span_store=InMemorySpanStore(),
		result_store=InMemoryResultStore(),
	)


@pytest.fixture
def tool_registry():
	registry = ToolRegistry()
	async def echo(args, ctx) -> ToolOutput:
		return ToolOutput.text(args.get("text", ""))
	registry.register(ToolDefinition(
		name="echo", description="Echo text", is_read_only=True,
		parameters={"type": "object", "properties": {"text": {"type": "string"}}},
		execute=echo,
	))
	async def write_file(args, ctx) -> ToolOutput:
		return ToolOutput.json_output({"written": True})
	registry.register(ToolDefinition(
		name="write_file", description="Write file", is_read_only=False,
		parameters={"type": "object", "properties": {"path": {"type": "string"}}},
		execute=write_file,
	))
	return registry
