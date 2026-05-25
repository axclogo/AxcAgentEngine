"""Tests for structured audit events."""
from __future__ import annotations

from axc_agent_engine.observability.audit import AuditEventType, InMemoryAuditSink
from axc_agent_engine.core.context import ExecutionConfig, ExecutionContext, ExecutionServices
from axc_agent_engine.core.schema import Capability, ToolDefinition
from axc_agent_engine.tools.orchestrator import execute_tool_calls
from axc_agent_engine.tools.registry import ToolRegistry
from axc_agent_engine.tools.tool_output import ToolOutput


async def test_tool_success_records_started_and_completed_audit_events():
	async def echo(args, ctx):
		return ToolOutput.text("ok")

	registry = ToolRegistry()
	registry.register(ToolDefinition(name="echo", execute=echo, capability=Capability.FILE_READ, risk_level="safe"))
	audit = InMemoryAuditSink()
	ctx = ExecutionContext(
		config=ExecutionConfig(allowed_capabilities=frozenset({Capability.FILE_READ})),
		services=ExecutionServices(audit_sink=audit),
	)
	ctx.state.metadata["agent_name"] = "agent-a"
	ctx.state.metadata["session_id"] = "session-a"

	results = await execute_tool_calls(
		[{"name": "echo", "arguments": {"x": 1}, "id": "call-1"}],
		registry,
		[],
		ctx,
	)

	assert results[0].success
	events = await audit.list_events()
	assert [event.type for event in events] == [
		AuditEventType.TOOL_CALL_STARTED,
		AuditEventType.TOOL_CALL_COMPLETED,
	]
	assert events[0].actor == "agent-a"
	assert events[0].session_id == "session-a"
	assert events[0].metadata["arguments_keys"] == ["x"]
	assert events[1].duration_ms >= 0


async def test_capability_rejection_records_policy_error():
	async def shell(args, ctx):
		return ToolOutput.text("should not run")

	registry = ToolRegistry()
	registry.register(ToolDefinition(name="shell", execute=shell, capability=Capability.SHELL, risk_level="dangerous"))
	audit = InMemoryAuditSink()
	ctx = ExecutionContext(
		config=ExecutionConfig(allowed_capabilities=frozenset({Capability.FILE_READ})),
		services=ExecutionServices(audit_sink=audit),
	)

	results = await execute_tool_calls(
		[{"name": "shell", "arguments": {"command": "id"}, "id": "call-2"}],
		registry,
		[],
		ctx,
	)

	assert not results[0].success
	events = await audit.list_events()
	assert len(events) == 1
	assert events[0].type == AuditEventType.TOOL_CALL_REJECTED
	assert events[0].allowed is False
	assert events[0].capability == Capability.SHELL
	assert events[0].error["code"] == "policy.capability_not_allowed"
	assert events[0].error["category"] == "policy"


async def test_tool_failure_records_failed_audit_event():
	async def fail(args, ctx):
		return ToolOutput.error("boom")

	registry = ToolRegistry()
	registry.register(ToolDefinition(name="fail", execute=fail))
	audit = InMemoryAuditSink()
	ctx = ExecutionContext(services=ExecutionServices(audit_sink=audit))

	results = await execute_tool_calls(
		[{"name": "fail", "arguments": {}, "id": "call-3"}],
		registry,
		[],
		ctx,
	)

	assert not results[0].success
	events = await audit.list_events()
	assert [event.type for event in events] == [
		AuditEventType.TOOL_CALL_STARTED,
		AuditEventType.TOOL_CALL_FAILED,
	]
	assert events[-1].error["code"] == "tool.execution_failed"
	assert events[-1].error["message"] == "boom"


async def test_unknown_tool_records_rejected_audit_event():
	audit = InMemoryAuditSink()
	ctx = ExecutionContext(services=ExecutionServices(audit_sink=audit))

	results = await execute_tool_calls(
		[{"name": "missing", "arguments": {}, "id": "call-4"}],
		ToolRegistry(),
		[],
		ctx,
	)

	assert not results[0].success
	events = await audit.list_events()
	assert len(events) == 1
	assert events[0].type == AuditEventType.TOOL_CALL_REJECTED
	assert events[0].error["code"] == "tool.unknown"
