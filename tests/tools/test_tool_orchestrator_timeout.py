"""Tests for orchestration-level tool call timeout boundaries."""
from __future__ import annotations

import asyncio

from axc_agent_engine.observability.audit import AuditEventType, InMemoryAuditSink
from axc_agent_engine.core.context import ExecutionConfig, ExecutionContext, ExecutionServices
from axc_agent_engine.plugins.base import BasePlugin
from axc_agent_engine.core.schema import ToolDefinition
from axc_agent_engine.tools.orchestrator import execute_tool_calls
from axc_agent_engine.tools.registry import ToolRegistry
from axc_agent_engine.tools.tool_output import ToolOutput


class HangingPostHookPlugin(BasePlugin):
	name = "hanging_post_hook"

	async def post_tool_call(self, exec_ctx, tool_name, arguments, result, duration_ms):
		await asyncio.sleep(10)
		return result


async def test_step_timeout_bounds_entire_tool_orchestration():
	async def fast_tool(args, ctx):
		return ToolOutput.text("ok")

	registry = ToolRegistry()
	registry.register(ToolDefinition(name="fast", execute=fast_tool, timeout=10))
	audit = InMemoryAuditSink()
	ctx = ExecutionContext(
		config=ExecutionConfig(step_timeout=0.02),
		services=ExecutionServices(audit_sink=audit),
	)

	results = await execute_tool_calls(
		[{"name": "fast", "arguments": {}, "id": "call-1"}],
		registry,
		[HangingPostHookPlugin()],
		ctx,
	)

	assert len(results) == 1
	assert not results[0].success
	assert "timeout" in results[0].error.lower()

	events = await audit.list_events()
	assert [event.type for event in events] == [
		AuditEventType.TOOL_CALL_STARTED,
		AuditEventType.TOOL_CALL_FAILED,
	]
	assert events[-1].error["code"] == "tool.call_timeout"
	assert events[-1].error["category"] == "timeout"
	assert events[-1].error["retryable"] is True


async def test_agent_call_requested_timeout_extends_step_timeout():
	async def agent_call(args, ctx):
		await asyncio.sleep(0.04)
		return ToolOutput.text("ok")

	registry = ToolRegistry()
	registry.register(ToolDefinition(name="agent_call", execute=agent_call, timeout=0, capability="agent_call"))
	ctx = ExecutionContext(config=ExecutionConfig(step_timeout=0.02, allowed_capabilities=frozenset({"agent_call"})))

	results = await execute_tool_calls(
		[{"name": "agent_call", "arguments": {"timeout": 0.1}, "id": "call-1"}],
		registry,
		[],
		ctx,
	)

	assert results[0].success is True
	assert results[0].context_view() == "ok"
