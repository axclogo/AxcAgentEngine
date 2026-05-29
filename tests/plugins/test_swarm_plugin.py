"""Tests for swarm plugin fan-out governance."""
from __future__ import annotations

from types import SimpleNamespace

from axc_agent_engine.observability.audit import InMemoryAuditSink
from axc_agent_engine.core.context import ExecutionContext, ExecutionServices
from axc_agent_engine.core.dispatcher import AgentEnvelope
from axc_agent_engine.plugins import PluginContext
from axc_agent_engine.plugins.builtin.swarm.plugin import SwarmPlugin
from axc_agent_engine.storage.result_store import InMemoryResultStore


class RecordingDispatcher:
	def __init__(self, replies: dict[str, str] | None = None) -> None:
		self.envelopes: list[AgentEnvelope] = []
		self.replies = replies or {}
		self.timeouts: list[float] = []
		self.callbacks = []

	async def request(self, envelope: AgentEnvelope, timeout: float = 60.0, event_callback=None) -> AgentEnvelope:
		self.envelopes.append(envelope)
		self.timeouts.append(timeout)
		self.callbacks.append(event_callback)
		content = self.replies.get(envelope.recipient, f"{envelope.recipient} reply")
		return AgentEnvelope(
			sender=envelope.recipient,
			recipient=envelope.sender,
			content=content,
			type="error" if content.startswith("ERROR:") else "reply",
			conversation_id=envelope.conversation_id,
			trace_id=envelope.trace_id,
			metadata=envelope.metadata,
		)


def _plugin(agents: list[SimpleNamespace], dispatcher: RecordingDispatcher, config: dict | None = None) -> SwarmPlugin:
	ctx = PluginContext(dispatcher=dispatcher)
	ctx.agent_lister = lambda: agents
	plugin = SwarmPlugin()
	plugin.initialize({"enabled": True, **(config or {})}, ctx)
	return plugin


async def test_swarm_dispatch_success_records_metadata_and_audit():
	dispatcher = RecordingDispatcher()
	audit = InMemoryAuditSink()
	plugin = _plugin([SimpleNamespace(name="a", description=""), SimpleNamespace(name="b", description="")], dispatcher)
	ctx = ExecutionContext(services=ExecutionServices(audit_sink=audit))
	ctx.state.metadata.update({"agent_name": "caller", "session_id": "s1", "tenant_id": "t1"})

	result = await plugin._tool_swarm_dispatch(
		{"goal": "ship", "tasks": [
			{"agent_name": "a", "description": "do a"},
			{"agent_name": "b", "description": "do b"},
		]},
		{"agent_name": "caller", "session_id": "s1", "exec_ctx": ctx},
	)

	assert not result.is_error
	assert result.content["success"] == 2
	assert ctx.state.metadata["swarm"]["success"] == 2
	assert dispatcher.envelopes[0].sender == "caller"
	assert dispatcher.envelopes[0].conversation_id == "s1"
	assert dispatcher.envelopes[0].metadata["tenant_id"] == "t1"
	assert dispatcher.envelopes[0].metadata["agent_call_depth"] == 1
	events = await audit.list_events()
	assert events[0].type == "swarm_dispatch_completed"
	assert events[0].capability == "agent_call"


async def test_swarm_rejects_disallowed_agent():
	dispatcher = RecordingDispatcher()
	plugin = _plugin(
		[SimpleNamespace(name="a", description=""), SimpleNamespace(name="secret", description="")],
		dispatcher,
		{"allowed_agents": ["a"]},
	)

	result = await plugin._tool_swarm_dispatch(
		{"goal": "ship", "tasks": [{"agent_name": "secret", "description": "do"}]},
		{"agent_name": "caller"},
	)

	assert result.is_error
	assert dispatcher.envelopes == []


async def test_swarm_rejects_self_call_by_default():
	dispatcher = RecordingDispatcher()
	plugin = _plugin([SimpleNamespace(name="caller", description="")], dispatcher)

	result = await plugin._tool_swarm_dispatch(
		{"goal": "loop", "tasks": [{"agent_name": "caller", "description": "loop"}]},
		{"agent_name": "caller"},
	)

	assert result.is_error
	assert dispatcher.envelopes == []


async def test_swarm_depth_limit_and_restore():
	dispatcher = RecordingDispatcher()
	plugin = _plugin([SimpleNamespace(name="worker", description="")], dispatcher, {"max_depth": 2})
	ctx = ExecutionContext()
	ctx.runtime.agent_call_depth = 1

	result = await plugin._tool_swarm_dispatch(
		{"goal": "ship", "tasks": [{"agent_name": "worker", "description": "do"}]},
		{"agent_name": "caller", "exec_ctx": ctx},
	)

	assert not result.is_error
	assert ctx.runtime.agent_call_depth == 1
	assert dispatcher.envelopes[0].metadata["agent_call_depth"] == 2


async def test_swarm_rejects_over_depth():
	dispatcher = RecordingDispatcher()
	plugin = _plugin([SimpleNamespace(name="worker", description="")], dispatcher, {"max_depth": 1})
	ctx = ExecutionContext()
	ctx.runtime.agent_call_depth = 1

	result = await plugin._tool_swarm_dispatch(
		{"goal": "ship", "tasks": [{"agent_name": "worker", "description": "do"}]},
		{"agent_name": "caller", "exec_ctx": ctx},
	)

	assert result.is_error
	assert dispatcher.envelopes == []


async def test_swarm_fail_fast_cancels_remaining_tasks():
	dispatcher = RecordingDispatcher({"bad": "ERROR: failed"})
	plugin = _plugin([SimpleNamespace(name="bad", description=""), SimpleNamespace(name="ok", description="")], dispatcher)

	result = await plugin._tool_swarm_dispatch(
		{"goal": "ship", "failure_policy": "fail_fast", "tasks": [
			{"agent_name": "bad", "description": "bad", "priority": 0},
			{"agent_name": "ok", "description": "ok", "priority": 1},
		]},
		{"agent_name": "caller"},
	)

	assert result.content["error"] == 1
	assert result.content["cancelled"] >= 0


async def test_swarm_large_result_externalized():
	store = InMemoryResultStore()
	dispatcher = RecordingDispatcher({"worker": "x" * 100})
	plugin = _plugin([SimpleNamespace(name="worker", description="")], dispatcher, {"max_result_bytes": 10})

	result = await plugin._tool_swarm_dispatch(
		{"goal": "ship", "tasks": [{"agent_name": "worker", "description": "do"}]},
		{"agent_name": "caller", "result_store": store},
	)

	assert result.artifacts
	task = result.content["results"][0]
	assert task["result"]["truncated"] is True
	assert await store.get(result.artifacts[0].id, limit=3) == "xxx"


def test_swarm_tool_has_capability_and_risk():
	plugin = _plugin([], RecordingDispatcher())
	tool = plugin.get_tools()[0]
	assert tool.name == "swarm_dispatch"
	assert tool.is_read_only is False
	assert tool.timeout == 0
	assert tool.capability == "agent_call"
	assert tool.risk_level == "moderate"


async def test_swarm_dispatch_uses_task_timeout_without_outer_conflict():
	dispatcher = RecordingDispatcher()
	plugin = _plugin([SimpleNamespace(name="worker", description="")], dispatcher, {"timeout": 300})
	result = await plugin._tool_swarm_dispatch(
		{"goal": "ship", "timeout": 300, "tasks": [{"agent_name": "worker", "description": "do", "timeout": 250}]},
		{"agent_name": "caller"},
	)
	assert not result.is_error
	assert dispatcher.timeouts == [250]
