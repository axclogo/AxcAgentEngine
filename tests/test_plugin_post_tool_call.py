"""Tests for plugin post_tool_call with ToolOutput."""
import pytest
from axc_agent_engine.plugins.base import BasePlugin
from axc_agent_engine.core.context import ExecutionContext, ExecutionConfig, ExecutionState
from axc_agent_engine.tools.tool_output import ToolOutput
from axc_agent_engine.tools.orchestrator import execute_tool_calls
from axc_agent_engine.tools.registry import ToolRegistry
from axc_agent_engine.core.schema import ToolDefinition
from axc_agent_engine.core.plugin_manager import PluginManager


class TestPluginPostToolCallToolOutput:
	@pytest.mark.asyncio
	async def test_plugin_receives_tooloutput(self):
		"""Plugin post_tool_call receives ToolOutput, not str."""
		received = {}

		class CapturePlugin(BasePlugin):
			name = "capture"
			async def post_tool_call(self, exec_ctx, tool_name, arguments, result, duration_ms):
				received["result_type"] = type(result).__name__
				received["content"] = result.content if hasattr(result, "content") else None
				return result

		async def my_tool(args, ctx):
			return ToolOutput.text("hello from tool")

		reg = ToolRegistry()
		reg.register(ToolDefinition(name="t", execute=my_tool,
			parameters={"type": "object", "properties": {}}))
		ctx = ExecutionContext(config=ExecutionConfig(), state=ExecutionState())
		pm = PluginManager([CapturePlugin()])
		results = await execute_tool_calls(
			[{"name": "t", "arguments": {}, "id": "x"}], reg, pm.plugins, ctx)
		assert results[0].success
		assert received["result_type"] == "ToolOutput"
		assert received["content"] == "hello from tool"

	@pytest.mark.asyncio
	async def test_plugin_can_modify_tooloutput(self):
		"""Plugin can modify ToolOutput content."""
		class ModifyPlugin(BasePlugin):
			name = "modify"
			async def post_tool_call(self, exec_ctx, tool_name, arguments, result, duration_ms):
				result.summary = "modified by plugin"
				return result

		async def my_tool(args, ctx):
			return ToolOutput.text("original")

		reg = ToolRegistry()
		reg.register(ToolDefinition(name="t", execute=my_tool,
			parameters={"type": "object", "properties": {}}))
		ctx = ExecutionContext(config=ExecutionConfig(), state=ExecutionState())
		pm = PluginManager([ModifyPlugin()])
		results = await execute_tool_calls(
			[{"name": "t", "arguments": {}, "id": "x"}], reg, pm.plugins, ctx)
		assert results[0].success
		assert results[0].output.summary == "modified by plugin"

	@pytest.mark.asyncio
	async def test_multiple_plugins_chain(self):
		"""Multiple plugins chain post_tool_call correctly."""
		class Plugin1(BasePlugin):
			name = "p1"
			priority = 10
			async def post_tool_call(self, exec_ctx, tool_name, arguments, result, duration_ms):
				result.metadata["p1"] = True
				return result

		class Plugin2(BasePlugin):
			name = "p2"
			priority = 20
			async def post_tool_call(self, exec_ctx, tool_name, arguments, result, duration_ms):
				result.metadata["p2"] = True
				return result

		async def my_tool(args, ctx):
			return ToolOutput.text("data")

		reg = ToolRegistry()
		reg.register(ToolDefinition(name="t", execute=my_tool,
			parameters={"type": "object", "properties": {}}))
		ctx = ExecutionContext(config=ExecutionConfig(), state=ExecutionState())
		pm = PluginManager([Plugin1(), Plugin2()])
		results = await execute_tool_calls(
			[{"name": "t", "arguments": {}, "id": "x"}], reg, pm.plugins, ctx)
		assert results[0].output.metadata.get("p1") is True
		assert results[0].output.metadata.get("p2") is True

	@pytest.mark.asyncio
	async def test_plugin_error_does_not_crash(self):
		"""Plugin post_tool_call error is swallowed (fail_closed=False)."""
		class BadPlugin(BasePlugin):
			name = "bad"
			async def post_tool_call(self, exec_ctx, tool_name, arguments, result, duration_ms):
				raise RuntimeError("plugin crash")

		async def my_tool(args, ctx):
			return ToolOutput.text("ok")

		reg = ToolRegistry()
		reg.register(ToolDefinition(name="t", execute=my_tool,
			parameters={"type": "object", "properties": {}}))
		ctx = ExecutionContext(config=ExecutionConfig(), state=ExecutionState())
		pm = PluginManager([BadPlugin()])
		results = await execute_tool_calls(
			[{"name": "t", "arguments": {}, "id": "x"}], reg, pm.plugins, ctx)
		assert results[0].success

	@pytest.mark.asyncio
	async def test_fail_closed_plugin_raises(self):
		"""Plugin with fail_closed=True propagates error."""
		class StrictPlugin(BasePlugin):
			name = "strict"
			fail_closed = True
			async def post_tool_call(self, exec_ctx, tool_name, arguments, result, duration_ms):
				raise RuntimeError("strict failure")

		async def my_tool(args, ctx):
			return ToolOutput.text("ok")

		reg = ToolRegistry()
		reg.register(ToolDefinition(name="t", execute=my_tool,
			parameters={"type": "object", "properties": {}}))
		ctx = ExecutionContext(config=ExecutionConfig(), state=ExecutionState())
		pm = PluginManager([StrictPlugin()])
		with pytest.raises(RuntimeError, match="strict failure"):
			await execute_tool_calls(
				[{"name": "t", "arguments": {}, "id": "x"}], reg, pm.plugins, ctx)

	@pytest.mark.asyncio
	async def test_post_hook_not_called_on_failure(self):
		"""post_tool_call is NOT called when tool execution fails."""
		called = {"count": 0}

		class CountPlugin(BasePlugin):
			name = "count"
			async def post_tool_call(self, exec_ctx, tool_name, arguments, result, duration_ms):
				called["count"] += 1
				return result

		async def failing_tool(args, ctx):
			raise ValueError("tool error")

		reg = ToolRegistry()
		reg.register(ToolDefinition(name="t", execute=failing_tool,
			parameters={"type": "object", "properties": {}}))
		ctx = ExecutionContext(config=ExecutionConfig(), state=ExecutionState())
		pm = PluginManager([CountPlugin()])
		results = await execute_tool_calls(
			[{"name": "t", "arguments": {}, "id": "x"}], reg, pm.plugins, ctx)
		assert not results[0].success
		assert called["count"] == 0


class TestRepetitionGuardWithToolOutput:
	@pytest.mark.asyncio
	async def test_tracks_compact_view(self):
		from axc_agent_engine.plugins.builtin.repetition_guard.plugin import RepetitionGuardPlugin
		p = RepetitionGuardPlugin()
		p.initialize({"rules": [{"type": "same_call", "limit": 3}]}, None)
		ctx = ExecutionContext()
		output = ToolOutput.text("same result")
		await p.post_tool_call(ctx, "tool", {}, output, 10)
		assert len(p._result_history) == 1
		assert "same result" in p._result_history[0]


class TestTracingWithToolOutput:
	@pytest.mark.asyncio
	async def test_tracing_records_success(self):
		from axc_agent_engine.plugins.builtin.tracing.plugin import TracingPlugin
		from axc_agent_engine.plugins import PluginContext
		spans_captured = []
		plugin_ctx = PluginContext()
		p = TracingPlugin()
		p.initialize({"output": "callback", "include_result": True}, plugin_ctx)
		p.set_callback(lambda span: spans_captured.append(span))
		reg = ToolRegistry()

		async def test_tool(args, ctx):
			return ToolOutput.text("tool result")

		reg.register(ToolDefinition(name="test_tool", execute=test_tool, is_read_only=True))
		ctx = ExecutionContext()
		await p.on_execution_start(ctx)
		await execute_tool_calls(
			[{"name": "test_tool", "arguments": {"arg": "val"}, "id": "test-call"}],
			reg,
			[p],
			ctx,
		)
		tool_spans = [s for s in spans_captured if s.get("type") == "tool_call"]
		assert len(tool_spans) == 1
		assert tool_spans[0]["success"] is True


class TestHooksWithToolOutput:
	@pytest.mark.asyncio
	async def test_hooks_log_action(self):
		from axc_agent_engine.plugins.builtin.hooks.plugin import HooksPlugin
		p = HooksPlugin()
		p.initialize({"rules": [
			{"event": "post_tool_call", "action": "log"}
		]}, None)
		ctx = ExecutionContext()
		output = ToolOutput.text("result data")
		result = await p.post_tool_call(ctx, "my_tool", {}, output, 100)
		# Should return output unchanged
		assert result is output
