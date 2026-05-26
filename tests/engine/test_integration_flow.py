"""Integration tests for #31 — full execution flow."""
import asyncio
import json
import pytest
from unittest.mock import MagicMock
from axc_agent_engine.core.executor import Executor
from axc_agent_engine.core.llm_caller import LLMCaller
from axc_agent_engine.core.context import ExecutionContext, ExecutionConfig, ExecutionState
from axc_agent_engine.core.plugin_manager import PluginManager
from axc_agent_engine.tools.registry import ToolRegistry
from axc_agent_engine.core.events import EventType
from axc_agent_engine.core.errors import ProviderError
from axc_agent_engine.core.schema import ToolDefinition
from axc_agent_engine.plugins.base import BasePlugin
from axc_agent_engine.tools.tool_output import ToolOutput


async def _run_executor(executor, message: str) -> str:
	"""Helper: collect DONE event from executor stream."""
	result = ""
	async for event in executor.run_stream(message):
		if event.type == EventType.DONE:
			result = event.content
		elif event.type == EventType.ERROR:
			raise ProviderError(event.content)
	return result


def _mock_llm_provider(responses):
	"""Create a mock LLM provider that returns predefined LLMResponse."""
	from axc_agent_engine.core.schema import LLMResponse, LLMMessage, LLMUsage
	provider = MagicMock()
	call_count = [0]

	async def mock_chat(messages, tools=None, **kwargs):
		idx = min(call_count[0], len(responses) - 1)
		call_count[0] += 1
		resp = responses[idx]
		message = LLMMessage(
			role=resp.get("role", "assistant"),
			content=resp.get("content", "") or "",
			tool_calls=resp.get("tool_calls", []),
		)
		return LLMResponse(message=message, usage=LLMUsage(input_tokens=10, output_tokens=5))

	provider.chat = mock_chat
	provider.stream = None
	return provider


class TestSimpleConversation:
	@pytest.mark.asyncio
	async def test_no_tool_call_returns_content(self):
		provider = _mock_llm_provider([{"role": "assistant", "content": "Hello!"}])
		pm = PluginManager([])
		llm_caller = LLMCaller(primary=provider, fallback=None, plugin_manager=pm)
		reg = ToolRegistry()
		ctx = ExecutionContext(config=ExecutionConfig(stream=False), state=ExecutionState())
		executor = Executor(llm_caller=llm_caller, registry=reg, plugin_manager=pm, ctx=ctx)
		result = await _run_executor(executor, "Hi")
		assert result == "Hello!"

	@pytest.mark.asyncio
	async def test_non_stream_does_not_emit_stream_delta_events(self):
		provider = _mock_llm_provider([{"role": "assistant", "content": "World"}])
		pm = PluginManager([])
		llm_caller = LLMCaller(primary=provider, fallback=None, plugin_manager=pm)
		reg = ToolRegistry()
		ctx = ExecutionContext(config=ExecutionConfig(stream=False), state=ExecutionState())
		executor = Executor(llm_caller=llm_caller, registry=reg, plugin_manager=pm, ctx=ctx)
		events = []
		async for event in executor.run_stream("Hello"):
			events.append(event)
		types = [e.type for e in events]
		assert EventType.STREAM_START not in types
		assert EventType.STREAM_DELTA not in types
		assert EventType.STREAM_END in types
		assert EventType.DONE in types

	@pytest.mark.asyncio
	async def test_done_is_last_event(self):
		provider = _mock_llm_provider([{"role": "assistant", "content": "Done"}])
		pm = PluginManager([])
		llm_caller = LLMCaller(primary=provider, fallback=None, plugin_manager=pm)
		reg = ToolRegistry()
		ctx = ExecutionContext(config=ExecutionConfig(stream=False), state=ExecutionState())
		executor = Executor(llm_caller=llm_caller, registry=reg, plugin_manager=pm, ctx=ctx)
		events = []
		async for event in executor.run_stream("test"):
			events.append(event)
		assert events[-1].type == EventType.DONE


class TestToolCallFlow:
	@pytest.mark.asyncio
	async def test_single_tool_call_and_response(self):
		"""LLM calls a tool, gets result, then responds."""
		tool_call_msg = {
			"role": "assistant", "content": "",
			"tool_calls": [{"id": "tc-1", "function": {"name": "echo", "arguments": '{"msg":"hi"}'}}],
		}
		final_msg = {"role": "assistant", "content": "Tool said: hi"}
		provider = _mock_llm_provider([tool_call_msg, final_msg])
		pm = PluginManager([])
		llm_caller = LLMCaller(primary=provider, fallback=None, plugin_manager=pm)
		reg = ToolRegistry()

		async def echo_tool(args, ctx):
			return json.dumps({"echo": args.get("msg", "")})

		reg.register(ToolDefinition(
			name="echo", execute=echo_tool,
			parameters={"type": "object", "properties": {"msg": {"type": "string"}}, "required": ["msg"]},
		))
		ctx = ExecutionContext(config=ExecutionConfig(stream=False), state=ExecutionState())
		executor = Executor(llm_caller=llm_caller, registry=reg, plugin_manager=pm, ctx=ctx)
		events = []
		async for event in executor.run_stream("call echo"):
			events.append(event)
		types = [e.type for e in events]
		assert EventType.TOOL_CALL in types
		assert EventType.TOOL_RESULT in types
		assert EventType.DONE in types

	@pytest.mark.asyncio
	async def test_tool_validation_failure_in_flow(self):
		"""LLM sends invalid args, tool returns validation error."""
		tool_call_msg = {
			"role": "assistant", "content": "",
			"tool_calls": [{"id": "tc-1", "function": {"name": "strict", "arguments": '{}'}}],
		}
		final_msg = {"role": "assistant", "content": "Missing param"}
		provider = _mock_llm_provider([tool_call_msg, final_msg])
		pm = PluginManager([])
		llm_caller = LLMCaller(primary=provider, fallback=None, plugin_manager=pm)
		reg = ToolRegistry()

		async def strict_tool(args, ctx):
			return "ok"

		reg.register(ToolDefinition(
			name="strict", execute=strict_tool,
			parameters={"type": "object", "properties": {"required_field": {"type": "string"}}, "required": ["required_field"]},
		))
		ctx = ExecutionContext(config=ExecutionConfig(stream=False), state=ExecutionState())
		executor = Executor(llm_caller=llm_caller, registry=reg, plugin_manager=pm, ctx=ctx)
		events = []
		async for event in executor.run_stream("call strict"):
			events.append(event)
		tool_results = [e for e in events if e.type == EventType.TOOL_RESULT]
		assert len(tool_results) == 1
		assert "Missing required parameter" in tool_results[0].content

	@pytest.mark.asyncio
	async def test_model_safe_tool_alias_resolves_to_real_name(self):
		tool_call_msg = {
			"role": "assistant", "content": "",
			"tool_calls": [{"id": "tc-1", "function": {"name": "mcp_github_search", "arguments": '{"q":"x"}'}}],
		}
		final_msg = {"role": "assistant", "content": "done"}
		provider = _mock_llm_provider([tool_call_msg, final_msg])
		pm = PluginManager([])
		llm_caller = LLMCaller(primary=provider, fallback=None, plugin_manager=pm)
		reg = ToolRegistry()
		called = []

		async def search_tool(args, ctx):
			called.append(args["q"])
			return ToolOutput.text("ok")

		reg.register(ToolDefinition(name="mcp.github.search", execute=search_tool))
		ctx = ExecutionContext(config=ExecutionConfig(stream=False), state=ExecutionState())
		executor = Executor(llm_caller=llm_caller, registry=reg, plugin_manager=pm, ctx=ctx)
		events = []
		async for event in executor.run_stream("search"):
			events.append(event)
		assert called == ["x"]
		tool_events = [e for e in events if e.type == EventType.TOOL_CALL]
		assert tool_events[0].tool_name == "mcp.github.search"


class TestPluginHooksInFlow:
	@pytest.mark.asyncio
	async def test_plugin_stop_signal(self):
		"""Plugin can stop execution via should_stop."""
		class StopPlugin(BasePlugin):
			name = "stopper"
			def should_stop(self, exec_ctx):
				return True, "Stopped by plugin"

		provider = _mock_llm_provider([{"role": "assistant", "content": "never reached"}])
		pm = PluginManager([StopPlugin()])
		llm_caller = LLMCaller(primary=provider, fallback=None, plugin_manager=pm)
		reg = ToolRegistry()
		ctx = ExecutionContext(config=ExecutionConfig(stream=False), state=ExecutionState())
		executor = Executor(llm_caller=llm_caller, registry=reg, plugin_manager=pm, ctx=ctx)
		events = []
		async for event in executor.run_stream("test"):
			events.append(event)
		assert events[-1].type == EventType.DONE
		assert events[-1].content == "Stopped by plugin"

	@pytest.mark.asyncio
	async def test_plugin_context_injection(self):
		"""Plugin inject_context adds to system prompt."""
		class ContextPlugin(BasePlugin):
			name = "ctx_injector"
			def inject_context(self, exec_ctx, topic=""):
				return "Extra context here"

		provider = _mock_llm_provider([{"role": "assistant", "content": "ok"}])
		pm = PluginManager([ContextPlugin()])
		llm_caller = LLMCaller(primary=provider, fallback=None, plugin_manager=pm)
		reg = ToolRegistry()
		ctx = ExecutionContext(
			config=ExecutionConfig(system_prompt="System", stream=False),
			state=ExecutionState(),
		)
		executor = Executor(llm_caller=llm_caller, registry=reg, plugin_manager=pm, ctx=ctx)
		await _run_executor(executor, "test")
		msgs = executor.message_store.get_all()
		# Should have system + plugin context + user + assistant
		assert any("Extra context here" in m.get("content", "") for m in msgs)

	@pytest.mark.asyncio
	async def test_max_rounds_exceeded(self):
		"""Executor stops after max_rounds."""
		# Always return tool calls to keep looping
		tool_call_msg = {
			"role": "assistant", "content": "",
			"tool_calls": [{"id": "tc-1", "function": {"name": "noop", "arguments": '{}'}}],
		}
		provider = _mock_llm_provider([tool_call_msg] * 10)
		pm = PluginManager([])
		llm_caller = LLMCaller(primary=provider, fallback=None, plugin_manager=pm)
		reg = ToolRegistry()

		async def noop(args, ctx):
			return "ok"

		reg.register(ToolDefinition(name="noop", execute=noop,
			parameters={"type": "object", "properties": {}}))
		ctx = ExecutionContext(
			config=ExecutionConfig(max_rounds=3, stream=False),
			state=ExecutionState(),
		)
		executor = Executor(llm_caller=llm_caller, registry=reg, plugin_manager=pm, ctx=ctx)
		events = []
		async for event in executor.run_stream("loop"):
			events.append(event)
		assert events[-1].type == EventType.ERROR
		assert "max rounds" in events[-1].content.lower()

	@pytest.mark.asyncio
	async def test_total_timeout(self):
		"""Executor stops on total timeout."""
		async def slow_chat(messages, tools=None, **kwargs):
			from axc_agent_engine.core.schema import LLMResponse, LLMMessage, LLMUsage
			await asyncio.sleep(0.1)
			return LLMResponse(
				message=LLMMessage(content="", tool_calls=[{"id": "tc", "function": {"name": "noop", "arguments": "{}"}}]),
				usage=LLMUsage(input_tokens=1, output_tokens=1),
			)

		provider = MagicMock()
		provider.chat = slow_chat
		pm = PluginManager([])
		llm_caller = LLMCaller(primary=provider, fallback=None, plugin_manager=pm)
		reg = ToolRegistry()

		async def noop(args, ctx):
			return "ok"

		reg.register(ToolDefinition(name="noop", execute=noop,
			parameters={"type": "object", "properties": {}}))
		ctx = ExecutionContext(
			config=ExecutionConfig(total_timeout=0.2, max_rounds=100, stream=False),
			state=ExecutionState(),
		)
		executor = Executor(llm_caller=llm_caller, registry=reg, plugin_manager=pm, ctx=ctx)
		events = []
		async for event in executor.run_stream("test"):
			events.append(event)
		assert events[-1].type == EventType.ERROR
		assert "timeout" in events[-1].content.lower()
