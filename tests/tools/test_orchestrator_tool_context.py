"""Tests for orchestrator building ToolContext correctly."""
import pytest
from axc_agent_engine.tools.orchestrator import execute_tool_calls, partition_tool_calls
from axc_agent_engine.tools.registry import ToolRegistry
from axc_agent_engine.core.context import ExecutionContext, ExecutionConfig, ExecutionState
from axc_agent_engine.core.plugin_manager import PluginManager
from axc_agent_engine.core.schema import ToolDefinition
from axc_agent_engine.tools.tool_output import ToolOutput


class TestPartitionToolCalls:
	def test_all_read_only_single_batch(self):
		reg = ToolRegistry()
		reg.register(ToolDefinition(name="a", is_read_only=True))
		reg.register(ToolDefinition(name="b", is_read_only=True))
		calls = [{"name": "a"}, {"name": "b"}]
		batches = partition_tool_calls(calls, reg)
		assert len(batches) == 1
		assert batches[0]["concurrent"] is True
		assert len(batches[0]["calls"]) == 2

	def test_write_tool_separate_batch(self):
		reg = ToolRegistry()
		reg.register(ToolDefinition(name="read", is_read_only=True))
		reg.register(ToolDefinition(name="write", is_read_only=False))
		calls = [{"name": "read"}, {"name": "write"}, {"name": "read"}]
		batches = partition_tool_calls(calls, reg)
		assert len(batches) == 3
		assert batches[0]["concurrent"] is True
		assert batches[1]["concurrent"] is False
		assert batches[2]["concurrent"] is True

	def test_unknown_tool_treated_as_write(self):
		reg = ToolRegistry()
		calls = [{"name": "unknown"}]
		batches = partition_tool_calls(calls, reg)
		assert batches[0]["concurrent"] is False

	def test_empty_calls(self):
		reg = ToolRegistry()
		batches = partition_tool_calls([], reg)
		assert batches == []


class TestExecuteToolCalls:
	@pytest.mark.asyncio
	async def test_passes_workspace_to_tool(self):
		"""Verify that workspace from ExecutionConfig reaches the tool."""
		received_context = {}

		async def capture_tool(args, context):
			received_context.update(context)
			return ToolOutput.json_output({"ok": True})

		reg = ToolRegistry()
		reg.register(ToolDefinition(name="test_tool", execute=capture_tool, is_read_only=True,
			parameters={"type": "object", "properties": {}}))
		ctx = ExecutionContext(
			config=ExecutionConfig(workspace="/my/workspace"),
			state=ExecutionState(),
		)
		pm = PluginManager([])
		calls = [{"name": "test_tool", "arguments": {}, "id": "tc-1"}]
		results = await execute_tool_calls(calls, reg, pm.plugins, ctx)
		assert len(results) == 1
		assert results[0].success
		assert received_context.get("workspace") == "/my/workspace"

	@pytest.mark.asyncio
	async def test_passes_exec_ctx(self):
		received_context = {}

		async def capture_tool(args, context):
			received_context.update(context)
			return ToolOutput.text("ok")

		reg = ToolRegistry()
		reg.register(ToolDefinition(name="t", execute=capture_tool,
			parameters={"type": "object", "properties": {}}))
		ctx = ExecutionContext(config=ExecutionConfig(), state=ExecutionState())
		pm = PluginManager([])
		results = await execute_tool_calls([{"name": "t", "arguments": {}, "id": "x"}], reg, pm.plugins, ctx)
		assert results[0].success
		assert received_context.get("exec_ctx") is ctx

	@pytest.mark.asyncio
	async def test_unknown_tool_returns_error(self):
		reg = ToolRegistry()
		ctx = ExecutionContext()
		pm = PluginManager([])
		results = await execute_tool_calls([{"name": "nonexistent", "arguments": {}, "id": "x"}], reg, pm.plugins, ctx)
		assert not results[0].success
		assert "Unknown tool" in results[0].error

	@pytest.mark.asyncio
	async def test_plugin_rejection(self):
		"""Plugin pre_tool_call can reject execution."""
		from axc_agent_engine.plugins.base import BasePlugin

		class RejectPlugin(BasePlugin):
			name = "reject"
			async def pre_tool_call(self, exec_ctx, tool_name, arguments):
				return False, arguments

		async def noop(args, ctx):
			return ToolOutput.text("ok")

		reg = ToolRegistry()
		reg.register(ToolDefinition(name="t", execute=noop,
			parameters={"type": "object", "properties": {}}))
		ctx = ExecutionContext()
		pm = PluginManager([RejectPlugin()])
		results = await execute_tool_calls([{"name": "t", "arguments": {}, "id": "x"}], reg, pm.plugins, ctx)
		assert not results[0].success
		assert "rejected" in results[0].error.lower()

	@pytest.mark.asyncio
	async def test_result_store_accessible_via_context(self):
		"""Verify result_store from services reaches the tool via context dict."""
		from axc_agent_engine.core.context import ExecutionServices
		from axc_agent_engine.storage.result_store import InMemoryResultStore

		received_context = {}

		async def capture_tool(args, context):
			received_context.update(context)
			return ToolOutput.text("ok")

		reg = ToolRegistry()
		reg.register(ToolDefinition(name="t", execute=capture_tool,
			parameters={"type": "object", "properties": {}}))
		store = InMemoryResultStore()
		services = ExecutionServices(result_store=store)
		ctx = ExecutionContext(config=ExecutionConfig(), state=ExecutionState(), services=services)
		pm = PluginManager([])
		results = await execute_tool_calls([{"name": "t", "arguments": {}, "id": "x"}], reg, pm.plugins, ctx)
		assert results[0].success
		assert received_context.get("result_store") is store

	@pytest.mark.asyncio
	async def test_passes_tool_call_metadata_to_tool_context(self):
		received_context = {}

		async def capture_tool(args, context):
			received_context.update(context)
			return ToolOutput.text("ok")

		reg = ToolRegistry()
		reg.register(ToolDefinition(name="t", execute=capture_tool,
			parameters={"type": "object", "properties": {}}))
		ctx = ExecutionContext(config=ExecutionConfig(), state=ExecutionState())
		pm = PluginManager([])
		results = await execute_tool_calls([{"name": "t", "arguments": {}, "id": "call-123"}], reg, pm.plugins, ctx)
		assert results[0].success
		assert received_context["tool_name"] == "t"
		assert received_context["tool_call_id"] == "call-123"
