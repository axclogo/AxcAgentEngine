"""Tests for plugin per-execution state isolation — ensures concurrent requests don't share state."""
import pytest
from axc_agent_engine.core.context import ExecutionConfig, ExecutionContext, ExecutionState
from axc_agent_engine.plugins.builtin.builtin_tools.plugin import BuiltinToolsPlugin


class TestBuiltinToolsIsolation:
	@pytest.mark.asyncio
	async def test_active_deferred_isolated_between_contexts(self):
		"""Two ExecutionContexts sharing one BuiltinToolsPlugin must not see each other's deferred tools."""
		plugin = BuiltinToolsPlugin()
		plugin.initialize({"load": ["get_time"], "defer": ["shell", "python_exec"]}, None)
		# Simulate two concurrent executions
		ctx_a = ExecutionContext(config=ExecutionConfig(stream=True), state=ExecutionState())
		ctx_b = ExecutionContext(config=ExecutionConfig(stream=True), state=ExecutionState())
		await plugin.on_execution_start(ctx_a)
		await plugin.on_execution_start(ctx_b)
		# Activate deferred tool in context A
		active_a = plugin._active_deferred_for(ctx_a)
		active_a.add("shell")
		# Context B should NOT see "shell"
		active_b = plugin._active_deferred_for(ctx_b)
		assert "shell" not in active_b
		assert "shell" in active_a

	@pytest.mark.asyncio
	async def test_pre_llm_call_uses_per_context_state(self):
		"""pre_llm_call should only inject deferred tools from the current execution's state."""
		plugin = BuiltinToolsPlugin()
		plugin.initialize({"load": ["get_time"], "defer": ["shell"]}, None)
		ctx_a = ExecutionContext(config=ExecutionConfig(stream=True), state=ExecutionState())
		ctx_b = ExecutionContext(config=ExecutionConfig(stream=True), state=ExecutionState())
		await plugin.on_execution_start(ctx_a)
		await plugin.on_execution_start(ctx_b)
		# Activate shell in ctx_a only
		plugin._active_deferred_for(ctx_a).add("shell")
		base_tools = [{"function": {"name": "get_time"}, "type": "function"}]
		# ctx_a should get shell injected
		_, tools_a = plugin.pre_llm_call(ctx_a, [], list(base_tools))
		tool_names_a = [t.get("function", {}).get("name") for t in tools_a]
		assert "shell" in tool_names_a
		# ctx_b should NOT get shell injected
		_, tools_b = plugin.pre_llm_call(ctx_b, [], list(base_tools))
		tool_names_b = [t.get("function", {}).get("name") for t in tools_b]
		assert "shell" not in tool_names_b

	def test_deferred_tools_preserve_capability_metadata(self):
		"""Deferred tool definitions must not bypass capability policy."""
		plugin = BuiltinToolsPlugin()
		plugin.initialize({"load": ["shell"], "defer": ["shell"]}, None)
		tools = plugin.get_tools()
		shell_tool = next(t for t in tools if t.name == "shell")
		assert shell_tool.deferred is True
		assert shell_tool.capability == "shell"

	@pytest.mark.asyncio
	async def test_post_tool_call_removes_from_correct_context(self):
		"""post_tool_call should only remove from the current execution's active set."""
		plugin = BuiltinToolsPlugin()
		plugin.initialize({"load": ["get_time"], "defer": ["shell"]}, None)
		ctx_a = ExecutionContext(config=ExecutionConfig(stream=True), state=ExecutionState())
		ctx_b = ExecutionContext(config=ExecutionConfig(stream=True), state=ExecutionState())
		await plugin.on_execution_start(ctx_a)
		await plugin.on_execution_start(ctx_b)
		# Both activate shell
		plugin._active_deferred_for(ctx_a).add("shell")
		plugin._active_deferred_for(ctx_b).add("shell")
		# Remove from ctx_a
		await plugin.post_tool_call(ctx_a, "shell", {}, "ok", 10)
		# ctx_a should be empty, ctx_b should still have it
		assert "shell" not in plugin._active_deferred_for(ctx_a)
		assert "shell" in plugin._active_deferred_for(ctx_b)
