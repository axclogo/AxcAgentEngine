from __future__ import annotations

import asyncio
import logging

import pytest

from axc_agent_engine.core.context import ExecutionConfig, ExecutionContext, ExecutionRuntimeState
from axc_agent_engine.core.errors import ErrorEnvelope
from axc_agent_engine.plugins import AgentInfo, ModelInfo, PluginContext
from axc_agent_engine.core.schema import ToolDefinition
from axc_agent_engine.plugins.builtin.tracing.plugin import (
	RedactionService,
	TraceSampler,
	_bounded_float,
	_bounded_int,
	_current_tool_runtime,
	_error_payload,
	_log_span,
	_resource_name,
	_sampled,
	_span_metadata,
	_trace_summary,
	_truncate,
)
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
async def test_tracing_span_store_receives_execution_metadata_and_parent_id():
	class Store:
		def __init__(self):
			self.spans = []

		async def save_span(self, span):
			self.spans.append(span)

	store = Store()
	p = _plugin({"enabled": True, "output": "store"}, span_store=store)
	reg = ToolRegistry()

	async def tool(args, ctx):
		return ToolOutput.text("ok")

	reg.register(ToolDefinition(name="tool", execute=tool, is_read_only=True, risk_level="safe"))
	ctx = ExecutionContext(
		runtime=ExecutionRuntimeState(
			agent_info=AgentInfo(name="agent-a", session_id="session-1"),
			model_info=ModelInfo(default="model-a"),
		),
	)
	ctx.state.metadata.update({
		"exec_log_id": 1001,
		"conversation_id": 123,
		"agent_config_id": 9,
		"run_id": "run-1",
		"session_id": "session-1",
		"agent_name": "agent-a",
	})

	await p.on_execution_start(ctx)
	await execute_tool_calls([{"name": "tool", "arguments": {}, "id": "call-1"}], reg, [p], ctx)
	await p.on_execution_end(ctx, "done", "")
	await p.close()

	root = next(span for span in store.spans if span["type"] == "execution")
	child = next(span for span in store.spans if span["type"] == "tool_call")
	for span in (root, child):
		assert span["metadata"]["exec_log_id"] == 1001
		assert span["metadata"]["conversation_id"] == 123
		assert span["metadata"]["agent_config_id"] == 9
		assert span["metadata"]["run_id"] == "run-1"
		assert span["metadata"]["session_id"] == "session-1"
		assert span["metadata"]["agent_name"] == "agent-a"
	assert child["parent_span_id"] == root["span_id"]


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
	p = _plugin({
		"enabled": True,
		"output": "callback",
		"include_arguments": True,
		"include_result": True,
		"max_result_length": 4,
	})
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


@pytest.mark.asyncio
async def test_tracing_tool_queries_empty_store_failures_and_session_filter():
	class FailingStore:
		async def query_by_trace(self, trace_id):
			raise RuntimeError("store down")

	spans = []
	p = _plugin({"enabled": True, "output": "callback"}, span_store=FailingStore())
	p.set_callback(spans.append)
	ctx = ExecutionContext()
	ctx.state.metadata["session_id"] = "sess-a"
	await p.on_execution_start(ctx)
	await p.on_execution_end(ctx, "ok", "")

	assert (await p._tool_get_trace({}, {})).is_error
	trace = await p._tool_get_trace({"trace_id": ctx.state.metadata["tracing"]["trace_id"]}, {})
	assert trace.content["count"] == 1
	assert (await p._tool_list_traces({"session_id": "other"}, {})).content["count"] == 0
	assert (await p._tool_list_traces({"session_id": "sess-a", "limit": "bad"}, {})).content["count"] == 1


@pytest.mark.asyncio
async def test_tracing_exporter_callback_schedule_and_audit_branches():
	class Store:
		def __init__(self):
			self.values = []

		async def set(self, key, value):
			self.values.append((key, value))

	async_exported = []

	async def exporter(span):
		async_exported.append(span)

	callback_errors = []
	def bad_callback(span):
		callback_errors.append(span)
		raise RuntimeError("callback failed")

	kv = Store()
	config = {
		"enabled": True,
		"output": "callback",
		"include_arguments": True,
		"include_result": True,
		"audit_mode": True,
	}
	from axc_agent_engine.plugins.builtin.tracing.plugin import TracingPlugin
	p = TracingPlugin()
	p.initialize(config, PluginContext(kv_store=kv, resources={"tracing.exporter": exporter}))
	p.set_callback(bad_callback)
	ctx = ExecutionContext()
	await p.on_execution_start(ctx)
	reg = ToolRegistry()

	async def tool(args, context):
		return ToolOutput.text("result")

	reg.register(ToolDefinition(name="tool", execute=tool, is_read_only=True))
	await execute_tool_calls([{"name": "tool", "arguments": {"password": "secret"}, "id": "audit-1"}], reg, [p], ctx)
	await p.close()

	assert async_exported
	assert callback_errors
	assert kv.values
	assert p._stats["failed"] >= 1


@pytest.mark.asyncio
async def test_tracing_drops_when_queue_full_and_without_running_loop():
	async def never():
		return None

	p = _plugin({"enabled": True, "output": "callback", "queue_limit": 1})
	p._pending_tasks.add(asyncio.create_task(asyncio.sleep(0)))
	p._schedule(never())
	assert p._stats["dropped"] == 1
	await p.close()

	p2 = _plugin({"enabled": True, "output": "callback"})
	p2._schedule(never())
	await p2.close()


def test_tracing_helpers_and_logging(caplog):
	assert _truncate("abcdef", 3) == "abc...[省略 3 个字符]"
	assert _sampled("trace", 1.0)
	assert not _sampled("trace", 0.0)
	sampler = TraceSampler(0.0, sample_errors=False, slow_span_ms=10)
	assert sampler.should_emit({"duration_ms": 11, "sampled": False}, False)
	assert TraceSampler(0.0, sample_errors=True, slow_span_ms=0).should_emit({"sampled": False}, True)
	assert _trace_summary([
		{"type": "tool_call", "duration_ms": 3, "error": {"message": "x"}},
		{"type": "llm_call", "duration_ms": 5},
	]) == {"span_count": 2, "errors": 1, "tool_calls": 1, "llm_calls": 1, "duration_ms": 5}

	envelope = ErrorEnvelope(code="x", message="m")
	assert _error_payload(envelope, 10)["code"] == "x"
	assert _error_payload(RuntimeError("boom"), 10)["details"]["class"] == "RuntimeError"
	assert _error_payload("tool bad", 10, code="tool")["category"] == "tool"

	with caplog.at_level(logging.INFO):
		for span in [
			{"type": "tool_call", "success": True, "name": "t", "duration_ms": 1, "round": 1, "trace_id": "tr"},
			{"type": "round_end", "round": 1, "input_tokens": 2, "output_tokens": 3, "tool_count": 1, "trace_id": "tr"},
			{"type": "llm_call", "duration_ms": 2, "round": 1, "trace_id": "tr"},
			{"type": "execution", "success": False, "duration_ms": 4, "input_tokens": 1, "output_tokens": 1, "trace_id": "tr"},
			{"type": "error", "name": "RuntimeError", "trace_id": "tr"},
			]:
				_log_span(span)
	assert "[trace]" in caplog.text


@pytest.mark.asyncio
async def test_tracing_disabled_plugin_is_noop():
	p = _plugin({"enabled": False})
	ctx = ExecutionContext()

	assert p.get_tools() == []
	await p.on_execution_start(ctx)
	await p.on_execution_end(ctx, "done", "err")
	assert p.pre_llm_call(ctx, [{"role": "user", "content": "x"}], None) == ([{"role": "user", "content": "x"}], None)
	await p.post_llm_call(ctx, [], {}, 1)
	await p.on_error(ctx, RuntimeError("ignored"))
	assert await p.pre_tool_call(ctx, "tool", {"x": 1}) == (True, {"x": 1})
	result = ToolOutput.text("ok")
	assert await p.post_tool_call(ctx, "tool", {}, result, 1) is result
	await p.on_tool_call_failed(ctx, "tool", {}, {}, 1)
	await p.on_round_end(ctx, "u", "a", [])
	assert "tracing" not in ctx.state.metadata


@pytest.mark.asyncio
async def test_tracing_llm_round_and_execution_error_spans():
	spans = []
	p = _plugin({"enabled": True, "output": "callback"})
	p.set_callback(spans.append)
	ctx = ExecutionContext()
	ctx.state.current_round = 3
	ctx.state.total_input_tokens = 11
	ctx.state.total_output_tokens = 7

	await p.on_execution_start(ctx)
	messages, tools = p.pre_llm_call(ctx, [{"role": "user", "content": "x"}], [{"type": "function"}])
	await p.post_llm_call(
		ctx,
		messages,
		{"usage": {"input_tokens": 2, "output_tokens": 4}, "total_usage": {"input_tokens": 9, "output_tokens": 10}},
		12,
	)
	await p.on_round_end(ctx, "u", "a", [{"name": "tool"}])
	await p.on_execution_end(ctx, "result", "fatal")

	llm = next(span for span in spans if span["type"] == "llm_call")
	round_end = next(span for span in spans if span["type"] == "round_end")
	root = next(span for span in spans if span["type"] == "execution")
	assert tools == [{"type": "function"}]
	assert llm["message_count"] == 1
	assert llm["tool_schema_count"] == 1
	assert llm["input_tokens"] == 2
	assert llm["total_output_tokens"] == 10
	assert round_end["tool_count"] == 1
	assert root["success"] is False
	assert root["error"]["message"] == "fatal"


@pytest.mark.asyncio
async def test_tracing_post_llm_without_span_and_execution_end_without_root_are_noops():
	p = _plugin({"enabled": True, "output": "callback"})
	ctx = ExecutionContext()

	await p.post_llm_call(ctx, [], "not-dict", 1)
	await p.on_execution_end(ctx, "done", "")

	assert p._stats["emitted"] == 0


@pytest.mark.asyncio
async def test_tracing_manual_tool_hooks_cover_generated_id_missing_runtime_and_failure_default_error():
	spans = []
	p = _plugin({"enabled": True, "output": "callback", "include_arguments": True, "include_result": True})
	p.set_callback(spans.append)
	ctx = ExecutionContext()
	await p.on_execution_start(ctx)

	await p.pre_tool_call(ctx, "manual", {"long": "abcdef", "items": list(range(105))})
	state = ctx.runtime.plugin_states["tracing"]
	tool_call_id = next(key for key in state["active_spans"])
	await p.post_tool_call(ctx, "manual", {}, ToolOutput.text("ok"), 5)
	state["active_spans"].pop(tool_call_id)
	ctx.runtime.plugin_states["_tool_runtime_contexts"] = {
		id(asyncio.current_task()): {"tool_call_id": "failed-id"}
	}
	await p.pre_tool_call(ctx, "failed", {})
	await p.on_tool_call_failed(ctx, "failed", {}, {}, 9)

	failed = next(span for span in spans if span["name"] == "failed")
	assert len(tool_call_id) == 12
	assert not any(span["name"] == "manual" for span in spans)
	assert failed["tool_call_id"] == "failed-id"
	assert failed["error"]["code"] == "tool.execution_failed"


@pytest.mark.asyncio
async def test_tracing_save_span_failure_exporter_sync_failure_and_background_failure():
	class FailingSpanStore:
		async def save_span(self, span):
			raise RuntimeError("store down")

	def bad_exporter(span):
		raise RuntimeError("export down")

	async def bad_task():
		raise RuntimeError("task down")

	from axc_agent_engine.plugins.builtin.tracing.plugin import TracingPlugin
	p = TracingPlugin()
	p.initialize({"enabled": True, "output": "callback"}, PluginContext(
		span_store=FailingSpanStore(),
		resources={"tracing.exporter": bad_exporter},
	))
	ctx = ExecutionContext()

	await p.on_execution_start(ctx)
	await p.on_execution_end(ctx, "done", "")
	p._schedule(bad_task())
	await p.close()

	assert p._stats["failed"] >= 2


def test_tracing_schedule_without_running_loop_closes_coroutine_and_counts_drop():
	async def never():
		return None

	p = _plugin({"enabled": True, "output": "callback"})
	p._schedule(never())
	assert p._stats["dropped"] == 1


@pytest.mark.asyncio
async def test_tracing_redaction_metadata_bounds_and_resource_helpers():
	stats = {"redacted": 0}
	redaction = RedactionService({"secret"}, 3, stats)
	value = redaction.redact({"secret": "x", "nested": [{"text": "abcdef"}], "plain": object()})

	assert value["secret"] == "[REDACTED]"
	assert value["nested"][0]["text"] == "abc...[省略 3 个字符]"
	assert stats["redacted"] == 1
	assert isinstance(value["plain"], object)
	assert _span_metadata({"a": object(), "b": (1, {"x": object()})})["b"][1]["x"].startswith("<object")
	assert _bounded_int("bad", 2, 5) == 2
	assert _bounded_int(99, 2, 5) == 5
	assert _bounded_float("bad", 0.0, 1.0) == 1.0
	assert _bounded_float(-1, 0.0, 1.0) == 0.0
	assert _resource_name(None, "default") == "default"
	assert _resource_name(True, "default") == "default"
	assert _resource_name(False, "default") == ""
	assert _resource_name(123, "default") == "123"
	assert _current_tool_runtime(ExecutionContext()) == {}
