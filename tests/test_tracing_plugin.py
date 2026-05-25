from __future__ import annotations

import asyncio

import pytest

from axc_agent_engine.core.context import ExecutionConfig, ExecutionContext, ExecutionRuntimeState
from axc_agent_engine.plugins import AgentInfo, ModelInfo, PluginContext
from axc_agent_engine.core.schema import ToolDefinition
from axc_agent_engine.storage.in_memory import InMemorySpanStore
from axc_agent_engine.tools.orchestrator import execute_tool_calls
from axc_agent_engine.tools.registry import ToolRegistry
from axc_agent_engine.tools.tool_output import ToolOutput


def _plugin(config: dict | None = None, span_store=None):
	from axc_agent_engine.plugins.builtin.tracing.plugin import TracingPlugin

	p = TracingPlugin()
	p.initialize(config or {"enabled": True, "output": "callback"}, PluginContext(span_store=span_store))
	return p


@pytest.mark.asyncio
async def test_tracing_records_runtime_metadata_and_trace_context():
	spans = []
	p = _plugin({"enabled": True, "output": "callback"})
	p.set_callback(spans.append)
	ctx = ExecutionContext(
		config=ExecutionConfig(workspace="/tmp/work"),
		runtime=ExecutionRuntimeState(
			model_info=ModelInfo(default="model-a", active="model-b"),
			agent_info=AgentInfo(name="agent-a", session_id="sess-1", routing_mode="auto"),
		),
	)
	ctx.state.metadata.update({"agent_name": "agent-a", "session_id": "sess-1", "run_id": "run-1"})

	await p.on_execution_start(ctx)
	ctx.add_usage(3, 5)
	await p.on_execution_end(ctx, "done", "")

	root = next(span for span in spans if span["type"] == "execution")
	assert root["agent_name"] == "agent-a"
	assert root["session_id"] == "sess-1"
	assert root["model"] == "model-b"
	assert root["workspace"] == "/tmp/work"
	assert root["traceparent"].startswith("00-")
	assert ctx.state.metadata["tracing"]["trace_id"] == root["trace_id"]


@pytest.mark.asyncio
async def test_tracing_correlates_concurrent_tools_without_parent_mislink():
	spans = []
	p = _plugin({"enabled": True, "output": "callback"})
	p.set_callback(spans.append)
	reg = ToolRegistry()

	async def slow(args, ctx):
		await asyncio.sleep(0.02)
		return ToolOutput.text("slow")

	async def fast(args, ctx):
		return ToolOutput.text("fast")

	reg.register(ToolDefinition(name="slow", execute=slow, is_read_only=True, risk_level="safe"))
	reg.register(ToolDefinition(name="fast", execute=fast, is_read_only=True, risk_level="safe"))
	ctx = ExecutionContext()
	await p.on_execution_start(ctx)

	results = await execute_tool_calls(
		[
			{"name": "slow", "arguments": {}, "id": "call-slow"},
			{"name": "fast", "arguments": {}, "id": "call-fast"},
		],
		reg,
		[p],
		ctx,
	)

	assert all(result.success for result in results)
	tool_spans = {span["tool_call_id"]: span for span in spans if span["type"] == "tool_call"}
	assert sorted(tool_spans) == ["call-fast", "call-slow"]
	parent_ids = {span["parent_span_id"] for span in tool_spans.values()}
	assert len(parent_ids) == 1
	assert next(iter(parent_ids)) == ctx.runtime.plugin_states["tracing"]["root_span"]["span_id"]


@pytest.mark.asyncio
async def test_tracing_correlates_same_name_concurrent_tools_by_call_id():
	spans = []
	p = _plugin({"enabled": True, "output": "callback", "include_arguments": True})
	p.set_callback(spans.append)
	reg = ToolRegistry()

	async def same(args, ctx):
		await asyncio.sleep(args["delay"])
		return ToolOutput.text(args["value"])

	reg.register(ToolDefinition(name="same", execute=same, is_read_only=True))
	ctx = ExecutionContext()
	await p.on_execution_start(ctx)

	await execute_tool_calls(
		[
			{"name": "same", "arguments": {"delay": 0.02, "value": "first"}, "id": "first"},
			{"name": "same", "arguments": {"delay": 0.0, "value": "second"}, "id": "second"},
		],
		reg,
		[p],
		ctx,
	)

	tool_spans = {span["tool_call_id"]: span for span in spans if span["type"] == "tool_call"}
	assert tool_spans["first"]["arguments"]["value"] == "first"
	assert tool_spans["second"]["arguments"]["value"] == "second"


@pytest.mark.asyncio
async def test_tracing_redacts_arguments_and_results():
	spans = []
	p = _plugin({"enabled": True, "output": "callback", "include_arguments": True, "include_result": True, "max_result_length": 4})
	p.set_callback(spans.append)
	reg = ToolRegistry()

	async def secret_tool(args, ctx):
		return ToolOutput.text("abcdef")

	reg.register(ToolDefinition(name="secret_tool", execute=secret_tool, is_read_only=True))
	ctx = ExecutionContext()
	await p.on_execution_start(ctx)
	await execute_tool_calls(
		[{"name": "secret_tool", "arguments": {"token": "abc", "safe": "value"}, "id": "secret-1"}],
		reg,
		[p],
		ctx,
	)

	span = next(span for span in spans if span["type"] == "tool_call")
	assert span["arguments"]["token"] == "[REDACTED]"
	assert span["arguments"]["safe"] == "value"
	assert span["result"].startswith("abcd")
	assert p._stats["redacted"] >= 1


@pytest.mark.asyncio
async def test_tracing_records_tool_output_errors():
	spans = []
	p = _plugin({"enabled": True, "output": "callback"})
	p.set_callback(spans.append)
	reg = ToolRegistry()

	async def bad(args, ctx):
		return ToolOutput.error("failed hard")

	reg.register(ToolDefinition(name="bad", execute=bad))
	ctx = ExecutionContext()
	await p.on_execution_start(ctx)

	results = await execute_tool_calls([{"name": "bad", "arguments": {}, "id": "bad-1"}], reg, [p], ctx)

	assert results[0].success is False
	span = next(span for span in spans if span["type"] == "tool_call")
	assert span["success"] is False
	assert span["error"]["code"] == "tool.output_error"


@pytest.mark.asyncio
async def test_tracing_closes_rejected_tool_spans():
	spans = []
	p = _plugin({"enabled": True, "output": "callback"})
	p.set_callback(spans.append)
	reg = ToolRegistry()

	async def protected(args, ctx):
		return ToolOutput.text("never")

	reg.register(ToolDefinition(name="protected", execute=protected, capability="shell", risk_level="dangerous"))
	ctx = ExecutionContext()
	await p.on_execution_start(ctx)

	results = await execute_tool_calls([{"name": "protected", "arguments": {}, "id": "deny-1"}], reg, [p], ctx)

	assert results[0].success is False
	span = next(span for span in spans if span["type"] == "tool_call")
	assert span["tool_call_id"] == "deny-1"
	assert span["success"] is False
	assert span["error"]["code"] == "policy.capability_not_allowed"


@pytest.mark.asyncio
async def test_tracing_closes_unknown_tool_spans():
	spans = []
	p = _plugin({"enabled": True, "output": "callback"})
	p.set_callback(spans.append)
	ctx = ExecutionContext()
	await p.on_execution_start(ctx)

	results = await execute_tool_calls([{"name": "missing", "arguments": {}, "id": "missing-1"}], ToolRegistry(), [p], ctx)

	assert results[0].success is False
	span = next(span for span in spans if span["type"] == "tool_call")
	assert span["tool_call_id"] == "missing-1"
	assert span["success"] is False


@pytest.mark.asyncio
async def test_tracing_sample_rate_zero_keeps_errors_only():
	spans = []
	p = _plugin({"enabled": True, "output": "callback", "sample_rate": 0.0, "sample_errors": True})
	p.set_callback(spans.append)
	ctx = ExecutionContext()
	await p.on_execution_start(ctx)
	await p.on_execution_end(ctx, "ignored", "")
	await p.on_error(ctx, RuntimeError("boom"))

	assert [span["type"] for span in spans] == ["error"]


@pytest.mark.asyncio
async def test_tracing_span_store_query_and_status_tools():
	store = InMemorySpanStore()
	p = _plugin({"enabled": True, "output": "callback"}, span_store=store)
	ctx = ExecutionContext()
	await p.on_execution_start(ctx)
	await p.on_execution_end(ctx, "ok", "")
	await p.close()

	trace_id = ctx.state.metadata["tracing"]["trace_id"]
	trace = await p._tool_get_trace({"trace_id": trace_id}, {})
	status = await p._tool_trace_status({}, {})
	listed = await p._tool_list_traces({"limit": 5}, {})

	assert trace.content["count"] == 1
	assert trace.content["summary"]["span_count"] == 1
	assert status.content["stats"]["stored"] == 1
	assert listed.content["count"] == 1
