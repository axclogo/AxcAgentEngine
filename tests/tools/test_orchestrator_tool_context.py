"""Tests for orchestrator building ToolContext correctly."""

import pytest

from axc_agent_engine.core.context import ExecutionConfig, ExecutionContext, ExecutionState
from axc_agent_engine.core.plugin_manager import PluginManager
from axc_agent_engine.core.schema import ToolDefinition
from axc_agent_engine.tools.orchestrator import execute_tool_calls, partition_tool_calls
from axc_agent_engine.tools.registry import ToolRegistry
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
		reg.register(
			ToolDefinition(
				name="test_tool",
				execute=capture_tool,
				is_read_only=True,
				parameters={"type": "object", "properties": {}},
			)
		)
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
		reg.register(ToolDefinition(name="t", execute=capture_tool, parameters={"type": "object", "properties": {}}))
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
		reg.register(ToolDefinition(name="t", execute=noop, parameters={"type": "object", "properties": {}}))
		ctx = ExecutionContext()
		pm = PluginManager([RejectPlugin()])
		results = await execute_tool_calls([{"name": "t", "arguments": {}, "id": "x"}], reg, pm.plugins, ctx)
		assert not results[0].success
		assert "reject" in results[0].error
		assert "未提供具体原因" in results[0].error

	@pytest.mark.asyncio
	async def test_plugin_rejection_preserves_reason(self):
		from axc_agent_engine.core.plugin_manager import PreToolCallDecision
		from axc_agent_engine.plugins.base import BasePlugin

		class RejectPlugin(BasePlugin):
			name = "reject_guard"

			async def pre_tool_call(self, exec_ctx, tool_name, arguments):
				return PreToolCallDecision(
					allowed=False,
					arguments=arguments,
					reason="record_id is required before loading related owner_id",
					code="tool.rejected_by_test",
				)

		async def noop(args, ctx):
			return ToolOutput.text("ok")

		reg = ToolRegistry()
		reg.register(ToolDefinition(name="t", execute=noop, parameters={"type": "object", "properties": {}}))
		ctx = ExecutionContext()
		pm = PluginManager([RejectPlugin()])

		results = await execute_tool_calls([{"name": "t", "arguments": {}, "id": "x"}], reg, pm.plugins, ctx)

		assert not results[0].success
		assert "reject_guard" in results[0].error
		assert "record_id is required" in results[0].error
		assert results[0].error != "Operation rejected by plugin"

	@pytest.mark.asyncio
	async def test_artifact_store_accessible_via_context(self):
		"""Verify artifact_store from services reaches the tool via context dict."""
		from axc_agent_engine.core.context import ExecutionServices
		from axc_agent_engine.storage.artifact_store import InMemoryArtifactStore

		received_context = {}

		async def capture_tool(args, context):
			received_context.update(context)
			return ToolOutput.text("ok")

		reg = ToolRegistry()
		reg.register(ToolDefinition(name="t", execute=capture_tool, parameters={"type": "object", "properties": {}}))
		store = InMemoryArtifactStore()
		services = ExecutionServices(artifact_store=store)
		ctx = ExecutionContext(config=ExecutionConfig(), state=ExecutionState(), services=services)
		pm = PluginManager([])
		results = await execute_tool_calls([{"name": "t", "arguments": {}, "id": "x"}], reg, pm.plugins, ctx)
		assert results[0].success
		assert received_context.get("artifact_store") is store

	@pytest.mark.asyncio
	async def test_passes_tool_call_metadata_to_tool_context(self):
		received_context = {}

		async def capture_tool(args, context):
			received_context.update(context)
			return ToolOutput.text("ok")

		reg = ToolRegistry()
		reg.register(ToolDefinition(name="t", execute=capture_tool, parameters={"type": "object", "properties": {}}))
		ctx = ExecutionContext(config=ExecutionConfig(), state=ExecutionState())
		pm = PluginManager([])
		results = await execute_tool_calls([{"name": "t", "arguments": {}, "id": "call-123"}], reg, pm.plugins, ctx)
		assert results[0].success
		assert received_context["tool_name"] == "t"
		assert received_context["tool_call_id"] == "call-123"


@pytest.mark.parametrize("parallel,limit,expected", [(False, 4, 1), (True, 1, 1), (True, 2, 2)])
async def test_tool_concurrency_enforced_across_por_children(parallel, limit, expected):
	import asyncio

	ctx = ExecutionContext(config=ExecutionConfig(parallel_tool_calls=parallel, max_parallel_tool_calls=limit))
	active = peak = 0
	entered, release = asyncio.Event(), asyncio.Event()

	async def read(args, context):
		nonlocal active, peak
		active += 1
		peak = max(active, peak)
		if active == expected:
			entered.set()
		try:
			await release.wait()
			await asyncio.sleep(0)
			return ToolOutput.text(context["tool_call_id"])
		finally:
			active -= 1

	registry = ToolRegistry()
	registry.register(ToolDefinition(name="read", is_read_only=True, execute=read))
	calls = [{"name": "read", "id": str(i), "arguments": {}} for i in range(8)]
	async with asyncio.timeout(2):
		tasks = [
			asyncio.create_task(execute_tool_calls(batch, registry, [], ctx.fork_for_child()))
			for batch in [calls[:4], calls[4:]]
		]
		try:
			await entered.wait()
		finally:
			release.set()
			batches = await asyncio.gather(*tasks)
	assert peak == expected and active == 0
	assert [r.tool_call_id for batch in batches for r in batch if r.success] == [c["id"] for c in calls]


async def test_tool_chunk_preserves_write_barriers():
	import asyncio

	ctx = ExecutionContext(config=ExecutionConfig(max_parallel_tool_calls=2))
	order = []

	async def record(args, context):
		order.append(("start", context["tool_call_id"]))
		await asyncio.sleep(0)
		order.append(("end", context["tool_call_id"]))
		return ToolOutput.text("ok")

	registry = ToolRegistry()
	for name in ("read", "write"):
		registry.register(ToolDefinition(name=name, is_read_only=name == "read", execute=record))
	calls = [{"name": name, "id": str(i), "arguments": {}} for i, name in enumerate(["read"] * 3 + ["write", "read"])]
	results = await execute_tool_calls(calls, registry, [], ctx)
	assert all(result.success for result in results)
	assert max(order.index(("end", str(i))) for i in range(3)) < order.index(("start", "3"))
	assert order.index(("end", "3")) < order.index(("start", "4"))


@pytest.mark.parametrize("limit", [0, -1, False, "2", 1.5])
def test_runtime_rejects_invalid_tool_concurrency(limit):
	from pydantic import ValidationError

	from axc_agent_engine.core.schema import RuntimeConfig

	with pytest.raises(ValidationError, match="max_parallel_tool_calls"):
		RuntimeConfig(max_parallel_tool_calls=limit)


async def test_failed_tool_batch_joins_sibling_and_releases_run_slots(monkeypatch):
	import asyncio

	from axc_agent_engine.tools.orchestrator import ToolExecutionPipeline

	ctx = ExecutionContext(config=ExecutionConfig(max_parallel_tool_calls=2))
	pipeline = ToolExecutionPipeline(ToolRegistry(), PluginManager([]), ctx)
	pipeline.registry.register(ToolDefinition(name="read", is_read_only=True))
	blocked, closed = asyncio.Event(), asyncio.Event()
	seen = []

	async def execute(call, registry, plugins, ctx):
		seen.append(call["id"])
		if call["id"] == "0":
			await blocked.wait()
			raise RuntimeError("fatal hook failure")
		blocked.set()
		try:
			await asyncio.Event().wait()
		finally:
			closed.set()

	monkeypatch.setattr("axc_agent_engine.tools.orchestrator._execute_single_with_timeout", execute)
	calls = [{"name": "read", "id": str(i), "arguments": {}} for i in range(3)]
	async with asyncio.timeout(2):
		with pytest.raises(RuntimeError, match="fatal hook failure"):
			await pipeline.execute(calls)
		assert closed.is_set() and set(seen) == {"0", "1"}
		async with ctx.tool_slots, ctx.tool_slots:
			assert ctx.tool_slots.locked()
