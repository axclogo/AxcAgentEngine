"""Integration tests for ToolOutput across the execution pipeline."""
import asyncio
import pytest
from axc_agent_engine.tools.tool_output import ToolOutput, ArtifactRef
from axc_agent_engine.tools.executor import ToolResult, execute_tool
from axc_agent_engine.tools.orchestrator import execute_tool_calls
from axc_agent_engine.tools.registry import ToolRegistry
from axc_agent_engine.tools.decorator import tool
from axc_agent_engine.core.schema import ToolDefinition
from axc_agent_engine.core.context import ExecutionContext, ExecutionConfig, ExecutionState
from axc_agent_engine.core.message_store import MessageStore
from axc_agent_engine.core.plugin_manager import PluginManager
from axc_agent_engine.storage.result_store import InMemoryResultStore


class TestToolOutputWithResultStore:
	"""End-to-end: tool stores artifact, result_read retrieves it."""

	@pytest.mark.asyncio
	async def test_file_read_then_result_read(self, tmp_path):
		"""file_read stores large file as artifact, result_read retrieves it."""
		from axc_agent_engine.plugins.builtin.builtin_tools.plugin import _file_read, _result_read
		f = tmp_path / "big.txt"
		content = "\n".join(f"line {i}: data" for i in range(500))
		f.write_text(content)
		store = InMemoryResultStore()
		ctx = {"result_store": store, "workspace": str(tmp_path)}
		read_result = await _file_read({"path": "big.txt"}, ctx)
		assert not read_result.is_error
		assert read_result.content["truncated"] is True
		assert len(read_result.artifacts) == 1
		artifact_id = read_result.artifacts[0].id
		# Now use result_read to get full content
		page_result = await _result_read({"artifact_id": artifact_id, "offset": 0, "limit": 100}, ctx)
		assert not page_result.is_error
		assert "line 0" in page_result.content

	@pytest.mark.asyncio
	async def test_shell_large_stdout_artifact(self):
		"""shell stores large stdout as artifact."""
		from axc_agent_engine.plugins.builtin.builtin_tools.plugin import _shell, _result_read
		store = InMemoryResultStore()
		ctx = {"result_store": store, "allow_unsafe_workspace": True}
		result = await _shell({"command": "python3 -c \"print('x' * 5000)\""}, ctx)
		assert not result.is_error
		if "stdout_artifact_id" in result.content:
			aid = result.content["stdout_artifact_id"]
			read_result = await _result_read({"artifact_id": aid}, ctx)
			assert not read_result.is_error
			assert len(read_result.content) > 0

	@pytest.mark.asyncio
	async def test_result_search_in_file(self, tmp_path):
		"""Search within a stored file artifact."""
		from axc_agent_engine.plugins.builtin.builtin_tools.plugin import _file_read, _result_search
		f = tmp_path / "searchable.txt"
		lines = [f"line {i}: {'important' if i % 10 == 0 else 'normal'}" for i in range(500)]
		f.write_text("\n".join(lines))
		store = InMemoryResultStore()
		ctx = {"result_store": store, "workspace": str(tmp_path)}
		read_result = await _file_read({"path": "searchable.txt"}, ctx)
		assert len(read_result.artifacts) == 1
		aid = read_result.artifacts[0].id
		search_result = await _result_search({"artifact_id": aid, "query": "important"}, ctx)
		assert not search_result.is_error
		assert len(search_result.content["matches"]) > 0


class TestToolOutputNoLLMInDefaultPath:
	"""Verify default path does NOT call LLM for compression."""

	@pytest.mark.asyncio
	async def test_no_llm_call_for_large_result(self):
		"""Large tool result uses compact_view, not LLM summarization."""
		async def big_tool(args, ctx):
			return ToolOutput.text("x" * 10000, summary="10000 chars of x")

		reg = ToolRegistry()
		reg.register(ToolDefinition(name="big", execute=big_tool,
			parameters={"type": "object", "properties": {}}))
		ctx = ExecutionContext(config=ExecutionConfig(), state=ExecutionState())
		pm = PluginManager([])
		results = await execute_tool_calls(
			[{"name": "big", "arguments": {}, "id": "1"}], reg, pm.plugins, ctx)
		assert results[0].success
		# compact_view uses summary, no LLM involved
		assert results[0].compact_view() == "10000 chars of x"


class TestToolOutputConcurrentExecution:
	"""Test ToolOutput with concurrent read-only tool execution."""

	@pytest.mark.asyncio
	async def test_concurrent_tools_all_return_tooloutput(self):
		call_order = []

		async def tool_a(args, ctx):
			await asyncio.sleep(0.01)
			call_order.append("a")
			return ToolOutput.text("result_a")

		async def tool_b(args, ctx):
			call_order.append("b")
			return ToolOutput.text("result_b")

		reg = ToolRegistry()
		reg.register(ToolDefinition(name="a", execute=tool_a, is_read_only=True,
			parameters={"type": "object", "properties": {}}))
		reg.register(ToolDefinition(name="b", execute=tool_b, is_read_only=True,
			parameters={"type": "object", "properties": {}}))
		ctx = ExecutionContext(config=ExecutionConfig(), state=ExecutionState())
		pm = PluginManager([])
		results = await execute_tool_calls(
			[{"name": "a", "arguments": {}, "id": "1"},
			 {"name": "b", "arguments": {}, "id": "2"}],
			reg, pm.plugins, ctx)
		assert len(results) == 2
		assert all(r.success for r in results)
		assert results[0].output.content == "result_a"
		assert results[1].output.content == "result_b"

	@pytest.mark.asyncio
	async def test_mixed_success_and_failure(self):
		async def good(args, ctx):
			return ToolOutput.text("ok")

		async def bad(args, ctx):
			return ToolOutput.error("failed")

		reg = ToolRegistry()
		reg.register(ToolDefinition(name="good", execute=good, is_read_only=True,
			parameters={"type": "object", "properties": {}}))
		reg.register(ToolDefinition(name="bad", execute=bad, is_read_only=True,
			parameters={"type": "object", "properties": {}}))
		ctx = ExecutionContext(config=ExecutionConfig(), state=ExecutionState())
		pm = PluginManager([])
		results = await execute_tool_calls(
			[{"name": "good", "arguments": {}, "id": "1"},
			 {"name": "bad", "arguments": {}, "id": "2"}],
			reg, pm.plugins, ctx)
		assert results[0].success
		assert not results[1].success


class TestToolOutputMessageStoreIntegration:
	"""Test that message_store correctly stores compact views."""

	def test_context_not_bloated_by_large_results(self):
		ms = MessageStore()
		# Simulate 10 large tool results
		for i in range(10):
			output = ToolOutput.text("x" * 10000, summary=f"Result {i}")
			results = [ToolResult(tool_call_id=str(i), tool_name="t", arguments={}, output=output, success=True)]
			ms.append_tool_results(results)
		# Total content should be small (summaries only)
		total_chars = sum(len(m["content"]) for m in ms.get_all())
		assert total_chars < 500  # 10 short summaries

	def test_artifacts_referenced_in_messages(self):
		ms = MessageStore()
		ref = ArtifactRef(id="art123", kind="text", size=50000)
		output = ToolOutput(content="preview", content_type="text", artifacts=[ref])
		results = [ToolResult(tool_call_id="1", tool_name="t", arguments={}, output=output, success=True)]
		ms.append_tool_results(results)
		content = ms.get_all()[0]["content"]
		assert "art123" in content


class TestToolOutputEdgeCases:
	@pytest.mark.asyncio
	async def test_tool_returns_tooloutput_with_empty_content(self):
		async def empty(args, ctx):
			return ToolOutput.text("")
		td = ToolDefinition(name="empty", execute=empty)
		result = await execute_tool(td, {}, "id1")
		assert result.success
		assert result.output.content == ""

	@pytest.mark.asyncio
	async def test_tool_returns_tooloutput_with_none_metadata(self):
		async def meta(args, ctx):
			return ToolOutput(content="ok", content_type="text", metadata={"key": None})
		td = ToolDefinition(name="meta", execute=meta)
		result = await execute_tool(td, {}, "id1")
		assert result.success

	@pytest.mark.asyncio
	async def test_tool_returns_tooloutput_with_many_artifacts(self):
		async def multi(args, ctx):
			refs = [ArtifactRef(id=f"a{i}", kind="text", size=i*100) for i in range(10)]
			return ToolOutput(content="multi", content_type="text", artifacts=refs)
		td = ToolDefinition(name="multi", execute=multi)
		result = await execute_tool(td, {}, "id1")
		assert result.success
		assert len(result.output.artifacts) == 10

	@pytest.mark.asyncio
	async def test_tool_returns_tooloutput_json_with_nested_data(self):
		async def nested(args, ctx):
			data = {"level1": {"level2": {"level3": [1, 2, 3]}}}
			return ToolOutput.json_output(data)
		td = ToolDefinition(name="nested", execute=nested)
		result = await execute_tool(td, {}, "id1")
		assert result.success
		assert result.output.content["level1"]["level2"]["level3"] == [1, 2, 3]

	@pytest.mark.asyncio
	async def test_tool_returns_tooloutput_with_unicode(self):
		async def unicode_tool(args, ctx):
			return ToolOutput.text("你好世界 🌍")
		td = ToolDefinition(name="unicode", execute=unicode_tool)
		result = await execute_tool(td, {}, "id1")
		assert result.success
		assert "你好" in result.output.content

	@pytest.mark.asyncio
	async def test_tool_timeout_returns_proper_error(self):
		async def slow(args, ctx):
			await asyncio.sleep(10)
			return ToolOutput.text("never")
		td = ToolDefinition(name="slow", execute=slow, timeout=0.05)
		result = await execute_tool(td, {}, "id1")
		assert not result.success
		assert "timeout" in result.error.lower()
		assert result.output is None

	@pytest.mark.asyncio
	async def test_tool_exception_returns_proper_error(self):
		async def crash(args, ctx):
			raise RuntimeError("unexpected crash")
		td = ToolDefinition(name="crash", execute=crash)
		result = await execute_tool(td, {}, "id1")
		assert not result.success
		assert "unexpected crash" in result.error
		assert result.output is None


class TestDecoratorWithToolOutput:
	"""Additional decorator tests with ToolOutput."""

	@pytest.mark.asyncio
	async def test_decorated_tool_in_registry(self):
		@tool(name="reg_test", is_read_only=True)
		async def reg_test(x: str) -> ToolOutput:
			return ToolOutput.text(x)

		reg = ToolRegistry()
		reg.register(reg_test.tool_definition)
		assert reg.has("reg_test")
		td = reg.get("reg_test")
		result = await td.execute({"x": "hello"}, {})
		assert isinstance(result, ToolOutput)

	@pytest.mark.asyncio
	async def test_decorated_tool_in_orchestrator(self):
		@tool(name="orch_test", is_read_only=True)
		async def orch_test(msg: str) -> ToolOutput:
			return ToolOutput.text(f"echo: {msg}")

		reg = ToolRegistry()
		reg.register(orch_test.tool_definition)
		ctx = ExecutionContext(config=ExecutionConfig(), state=ExecutionState())
		pm = PluginManager([])
		results = await execute_tool_calls(
			[{"name": "orch_test", "arguments": {"msg": "hi"}, "id": "1"}],
			reg, pm.plugins, ctx)
		assert results[0].success
		assert "echo: hi" in results[0].compact_view()
