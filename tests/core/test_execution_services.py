"""Tests for ExecutionServices and typed service injection."""
import pytest
from axc_agent_engine.core.context import (
	ExecutionConfig, ExecutionState, ExecutionContext, ExecutionServices,
)
from axc_agent_engine.storage.result_store import InMemoryResultStore
from axc_agent_engine.tools.context import ToolContext
from axc_agent_engine.tools.tool_output import ToolOutput
from axc_agent_engine.tools.executor import execute_tool
from axc_agent_engine.tools.orchestrator import execute_tool_calls
from axc_agent_engine.tools.registry import ToolRegistry
from axc_agent_engine.core.schema import ToolDefinition
from axc_agent_engine.core.plugin_manager import PluginManager


class TestExecutionServices:
	def test_default_none(self):
		services = ExecutionServices()
		assert services.result_store is None
		assert services.message_bus is None

	def test_with_result_store(self):
		store = InMemoryResultStore()
		services = ExecutionServices(result_store=store)
		assert services.result_store is store

	def test_context_has_services(self):
		store = InMemoryResultStore()
		services = ExecutionServices(result_store=store)
		ctx = ExecutionContext(services=services)
		assert ctx.services.result_store is store

	def test_context_default_services(self):
		ctx = ExecutionContext()
		assert ctx.services is not None
		assert ctx.services.result_store is None

	def test_fork_for_child_inherits_services_identity_and_cancel(self):
		store = InMemoryResultStore()
		services = ExecutionServices(result_store=store)
		ctx = ExecutionContext(services=services)
		ctx.state.metadata["agent_name"] = "agent-a"
		ctx.state.metadata["session_id"] = "session-a"
		ctx.state.metadata["private"] = object()
		ctx.runtime.llm_options["temperature"] = 0

		child = ctx.fork_for_child({"por_step_id": 2})

		assert child is not ctx
		assert child.services is services
		assert child.state.metadata["agent_name"] == "agent-a"
		assert child.state.metadata["session_id"] == "session-a"
		assert child.state.metadata["por_step_id"] == 2
		assert "private" not in child.state.metadata
		assert child.runtime.llm_options == {"temperature": 0}
		ctx.cancel()
		with pytest.raises(Exception, match="Execution cancelled"):
			child.check_cancelled()


class TestToolContextResultStore:
	def test_result_store_from_services(self):
		store = InMemoryResultStore()
		services = ExecutionServices(result_store=store)
		exec_ctx = ExecutionContext(services=services)
		tool_ctx = ToolContext(exec_ctx=exec_ctx)
		assert tool_ctx.result_store is store

	def test_result_store_none_without_services(self):
		exec_ctx = ExecutionContext()
		tool_ctx = ToolContext(exec_ctx=exec_ctx)
		assert tool_ctx.result_store is None

	def test_result_store_none_without_exec_ctx(self):
		tool_ctx = ToolContext()
		assert tool_ctx.result_store is None

	def test_to_dict_includes_result_store(self):
		store = InMemoryResultStore()
		services = ExecutionServices(result_store=store)
		exec_ctx = ExecutionContext(services=services)
		tool_ctx = ToolContext(exec_ctx=exec_ctx)
		d = tool_ctx.to_dict()
		assert d["result_store"] is store

	def test_to_dict_result_store_none(self):
		tool_ctx = ToolContext()
		d = tool_ctx.to_dict()
		assert d["result_store"] is None


class TestOrchestratorServiceInjection:
	@pytest.mark.asyncio
	async def test_result_store_reaches_tool(self):
		"""Verify result_store from services reaches tool via context dict."""
		received = {}

		async def capture(args, ctx):
			received["result_store"] = ctx.get("result_store")
			return ToolOutput.text("ok")

		reg = ToolRegistry()
		reg.register(ToolDefinition(name="t", execute=capture,
			parameters={"type": "object", "properties": {}}))
		store = InMemoryResultStore()
		services = ExecutionServices(result_store=store)
		ctx = ExecutionContext(config=ExecutionConfig(), state=ExecutionState(), services=services)
		pm = PluginManager([])
		results = await execute_tool_calls(
			[{"name": "t", "arguments": {}, "id": "x"}], reg, pm.plugins, ctx)
		assert results[0].success
		assert received["result_store"] is store

	@pytest.mark.asyncio
	async def test_no_result_store_passes_none(self):
		received = {}

		async def capture(args, ctx):
			received["result_store"] = ctx.get("result_store")
			return ToolOutput.text("ok")

		reg = ToolRegistry()
		reg.register(ToolDefinition(name="t", execute=capture,
			parameters={"type": "object", "properties": {}}))
		ctx = ExecutionContext(config=ExecutionConfig(), state=ExecutionState())
		pm = PluginManager([])
		results = await execute_tool_calls(
			[{"name": "t", "arguments": {}, "id": "x"}], reg, pm.plugins, ctx)
		assert results[0].success
		assert received["result_store"] is None


class TestExecutorToolOutputEnforcement:
	@pytest.mark.asyncio
	async def test_str_return_rejected(self):
		async def bad(args, ctx):
			return "plain string"
		td = ToolDefinition(name="bad", execute=bad)
		with pytest.raises(TypeError, match="必须返回 ToolOutput"):
			await execute_tool(td, {}, "id1")

	@pytest.mark.asyncio
	async def test_dict_return_rejected(self):
		async def bad(args, ctx):
			return {"key": "value"}
		td = ToolDefinition(name="bad", execute=bad)
		with pytest.raises(TypeError, match="必须返回 ToolOutput"):
			await execute_tool(td, {}, "id1")

	@pytest.mark.asyncio
	async def test_list_return_rejected(self):
		async def bad(args, ctx):
			return [1, 2, 3]
		td = ToolDefinition(name="bad", execute=bad)
		with pytest.raises(TypeError, match="必须返回 ToolOutput"):
			await execute_tool(td, {}, "id1")

	@pytest.mark.asyncio
	async def test_none_return_rejected(self):
		async def bad(args, ctx):
			return None
		td = ToolDefinition(name="bad", execute=bad)
		with pytest.raises(TypeError, match="必须返回 ToolOutput"):
			await execute_tool(td, {}, "id1")

	@pytest.mark.asyncio
	async def test_int_return_rejected(self):
		async def bad(args, ctx):
			return 42
		td = ToolDefinition(name="bad", execute=bad)
		with pytest.raises(TypeError, match="必须返回 ToolOutput"):
			await execute_tool(td, {}, "id1")

	@pytest.mark.asyncio
	async def test_tooloutput_accepted(self):
		async def good(args, ctx):
			return ToolOutput.text("ok")
		td = ToolDefinition(name="good", execute=good)
		result = await execute_tool(td, {}, "id1")
		assert result.success
		assert result.output.content == "ok"

	@pytest.mark.asyncio
	async def test_tooloutput_error_treated_as_failure(self):
		async def err(args, ctx):
			return ToolOutput.error("something broke")
		td = ToolDefinition(name="err", execute=err)
		result = await execute_tool(td, {}, "id1")
		assert not result.success
		assert "something broke" in result.error
		assert result.output is not None
		assert result.output.is_error is True

	@pytest.mark.asyncio
	async def test_tooloutput_json(self):
		async def json_tool(args, ctx):
			return ToolOutput.json_output({"status": "ok", "count": 5})
		td = ToolDefinition(name="json_tool", execute=json_tool)
		result = await execute_tool(td, {}, "id1")
		assert result.success
		assert result.output.content_type == "json"
		assert result.output.content["count"] == 5


class TestToolResultContextView:
	def test_result_property_returns_context_view(self):
		from axc_agent_engine.tools.executor import ToolResult
		output = ToolOutput.text("hello world")
		tr = ToolResult(tool_call_id="1", tool_name="t", arguments={}, output=output, success=True)
		assert tr.context_view() == "hello world"

	def test_result_property_none_output(self):
		from axc_agent_engine.tools.executor import ToolResult
		tr = ToolResult(tool_call_id="1", tool_name="t", arguments={}, success=False, error="err")
		assert tr.context_view() == ""

	def test_context_view_long_content(self):
		from axc_agent_engine.tools.executor import ToolResult
		output = ToolOutput.text("x" * 5000)
		tr = ToolResult(tool_call_id="1", tool_name="t", arguments={}, output=output, success=True)
		view = tr.context_view()
		assert len(view) < 5000
		assert "省略" in view
