"""Tests for collaboration plugin tools."""
from __future__ import annotations

from types import SimpleNamespace

from axc_agent_engine.core.dispatcher import AgentEnvelope
from axc_agent_engine.plugins import PluginContext
from axc_agent_engine.plugins.builtin.collaboration.plugin import CollaborationPlugin
from axc_agent_engine.runtime.resources import ResourceRegistry


class RecordingDispatcher:
	def __init__(self) -> None:
		self.envelopes: list[AgentEnvelope] = []
		self.callbacks = []

	async def request(self, envelope: AgentEnvelope, timeout: float = 60.0, event_callback=None) -> AgentEnvelope:
		self.envelopes.append(envelope)
		self.callbacks.append(event_callback)
		return AgentEnvelope(
			sender=envelope.recipient,
			recipient=envelope.sender,
			content=f"{envelope.recipient} reply",
			type="reply",
			conversation_id=envelope.conversation_id,
			trace_id=envelope.trace_id,
		)


def _plugin(
	agents: list[SimpleNamespace],
	dispatcher: RecordingDispatcher,
	config: dict | None = None,
) -> CollaborationPlugin:
	ctx = PluginContext(dispatcher=dispatcher)
	ctx.agent_lister = lambda: agents
	plugin = CollaborationPlugin()
	plugin.initialize({"enabled": True, "max_depth": 3, **(config or {})}, ctx)
	return plugin


class RecordingOrchestrationService:
	def __init__(self) -> None:
		self.calls: list[dict] = []
		self.tasks: dict[str, SimpleNamespace] = {}

	async def create_task(self, **kwargs):
		self.calls.append(kwargs)
		task = SimpleNamespace(
			task_id="task-1",
			status="running",
			mode=kwargs["mode"],
			topic=kwargs["topic"],
			agent_names=kwargs["agent_names"],
			events=[],
			result={},
			error="",
		)
		self.tasks[task.task_id] = task
		return task

	def get_task(self, task_id: str):
		return self.tasks.get(task_id)

	async def cancel_task(self, task_id: str):
		task = self.tasks.get(task_id)
		if not task:
			return False
		task.status = "cancelled"
		return True


async def test_agent_call_tracks_and_restores_depth():
	dispatcher = RecordingDispatcher()
	plugin = _plugin([SimpleNamespace(name="worker", description="")], dispatcher)
	exec_ctx = SimpleNamespace(runtime=SimpleNamespace(agent_call_depth=1))

	result = await plugin._tool_agent_call(
		{"agent_name": "worker", "message": "do it"},
		{"agent_name": "caller", "exec_ctx": exec_ctx},
	)

	assert not result.is_error
	assert exec_ctx.runtime.agent_call_depth == 1
	assert dispatcher.envelopes[0].recipient == "worker"
	assert dispatcher.envelopes[0].metadata["agent_call_depth"] == 2


def test_agent_call_tool_has_capability_and_risk():
	plugin = _plugin([SimpleNamespace(name="worker", description="")], RecordingDispatcher())
	tool = next(item for item in plugin.get_tools() if item.name == "agent_call")

	assert tool.capability == "agent_call"
	assert tool.risk_level == "moderate"
	assert tool.is_read_only is False


async def test_agent_call_rejects_disallowed_agent():
	dispatcher = RecordingDispatcher()
	plugin = _plugin(
		[SimpleNamespace(name="worker", description=""), SimpleNamespace(name="secret", description="")],
		dispatcher,
		{"allowed_agents": ["worker"]},
	)

	result = await plugin._tool_agent_call(
		{"agent_name": "secret", "message": "do it"},
		{"agent_name": "caller"},
	)

	assert result.is_error
	assert dispatcher.envelopes == []


async def test_agent_call_rejects_self_call_by_default():
	dispatcher = RecordingDispatcher()
	plugin = _plugin([SimpleNamespace(name="caller", description="")], dispatcher)

	result = await plugin._tool_agent_call(
		{"agent_name": "caller", "message": "loop"},
		{"agent_name": "caller"},
	)

	assert result.is_error
	assert dispatcher.envelopes == []


async def test_agent_call_uses_timeout_argument():
	class TimeoutDispatcher(RecordingDispatcher):
		async def request(self, envelope: AgentEnvelope, timeout: float = 60.0, event_callback=None) -> AgentEnvelope:
			self.timeout = timeout
			return await super().request(envelope, timeout, event_callback)

	dispatcher = TimeoutDispatcher()
	plugin = _plugin([SimpleNamespace(name="worker", description="")], dispatcher)
	await plugin._tool_agent_call(
		{"agent_name": "worker", "message": "do it", "timeout": 7},
		{"agent_name": "caller"},
	)

	assert dispatcher.timeout == 7


async def test_agent_call_uses_collaboration_timeout_config():
	class TimeoutDispatcher(RecordingDispatcher):
		async def request(self, envelope: AgentEnvelope, timeout: float = 60.0, event_callback=None) -> AgentEnvelope:
			self.timeout = timeout
			return await super().request(envelope, timeout, event_callback)

	dispatcher = TimeoutDispatcher()
	plugin = _plugin([SimpleNamespace(name="worker", description="")], dispatcher, {"timeout": 300})
	await plugin._tool_agent_call(
		{"agent_name": "worker", "message": "do it"},
		{"agent_name": "caller"},
	)

	assert dispatcher.timeout == 300


def test_agent_call_tool_disables_outer_timeout():
	plugin = _plugin([SimpleNamespace(name="worker", description="")], RecordingDispatcher(), {"timeout": 300})
	tool = next(item for item in plugin.get_tools() if item.name == "agent_call")
	assert tool.timeout == 0


async def test_agent_call_forwards_child_events_to_exec_sink():
	dispatcher = RecordingDispatcher()
	plugin = _plugin([SimpleNamespace(name="worker", description="")], dispatcher)
	events = []
	exec_ctx = SimpleNamespace(
		runtime=SimpleNamespace(agent_call_depth=0, event_sink=events.append),
		state=SimpleNamespace(metadata={"run_id": "run-1"}),
	)

	await plugin._tool_agent_call(
		{"agent_name": "worker", "message": "do it"},
		{"agent_name": "caller", "exec_ctx": exec_ctx, "tool_call_id": "parent-call"},
	)
	dispatcher.callbacks[0](AgentEnvelope(
		type="sub_agent_step",
		content="tool",
		metadata={
			"agent_name": "worker",
			"parent_tool_call_id": "parent-call",
			"step": {"type": "tool_call", "tool": "search"},
		},
	))

	assert events[0].type.value == "sub_agent_step"
	assert events[0].metadata["parent_tool_call_id"] == "parent-call"
	assert events[0].metadata["step"]["type"] == "tool_call"


def test_collaboration_plugin_does_not_expose_inline_multi_agent_session_without_sidecar():
	plugin = _plugin([], RecordingDispatcher())

	names = [tool.name for tool in plugin.get_tools()]

	assert "multi_agent_session" not in names
	assert "orchestration_task_create" not in names


def test_agent_list_filters_visible_agents():
	plugin = _plugin(
		[
			SimpleNamespace(name="caller", description="self"),
			SimpleNamespace(name="worker", description="ok"),
			SimpleNamespace(name="secret", description="no"),
		],
		RecordingDispatcher(),
		{"allowed_agents": ["worker", "secret"], "denied_agents": ["secret"]},
	)

	import asyncio
	result = asyncio.run(plugin._tool_agent_list({}, {"agent_name": "caller"}))

	assert result.content["agents"] == [{"name": "worker", "description": "ok"}]


async def test_collaboration_plugin_can_create_sidecar_orchestration_task():
	service = RecordingOrchestrationService()
	ctx = PluginContext(resources=ResourceRegistry({"orchestration": service}))
	plugin = CollaborationPlugin()
	plugin.initialize({"enabled": True}, ctx)

	result = await plugin._tool_orchestration_task_create(
		{"agent_names": ["alpha", "beta"], "mode": "debate", "topic": "ship?", "max_rounds": 1},
		{},
	)

	assert not result.is_error
	assert result.content["task_id"] == "task-1"
	assert service.calls[0]["agent_names"] == ["alpha", "beta"]
	assert service.calls[0]["mode"] == "debate"


async def test_orchestration_create_rejects_disallowed_agents():
	service = RecordingOrchestrationService()
	ctx = PluginContext(resources=ResourceRegistry({"orchestration": service}))
	plugin = CollaborationPlugin()
	plugin.initialize({"enabled": True, "allowed_agents": ["alpha"]}, ctx)

	result = await plugin._tool_orchestration_task_create(
		{"agent_names": ["alpha", "beta"], "mode": "debate", "topic": "ship?"},
		{"agent_name": "caller"},
	)

	assert result.is_error
	assert service.calls == []


async def test_collaboration_plugin_can_get_and_cancel_orchestration_task():
	service = RecordingOrchestrationService()
	ctx = PluginContext(resources=ResourceRegistry({"orchestration": service}))
	plugin = CollaborationPlugin()
	plugin.initialize({"enabled": True}, ctx)
	await plugin._tool_orchestration_task_create(
		{"agent_names": ["alpha"], "mode": "discussion", "topic": "ship?"},
		{},
	)

	status = await plugin._tool_orchestration_task_get({"task_id": "task-1"}, {})
	cancelled = await plugin._tool_orchestration_task_cancel({"task_id": "task-1"}, {})

	assert status.content["task_id"] == "task-1"
	assert cancelled.content["cancelled"] is True
	assert service.tasks["task-1"].status == "cancelled"


async def test_collaboration_plugin_awaits_async_get_task():
	class AsyncGetService(RecordingOrchestrationService):
		async def get_task(self, task_id: str):
			return self.tasks.get(task_id)

	service = AsyncGetService()
	ctx = PluginContext(resources=ResourceRegistry({"orchestration": service}))
	plugin = CollaborationPlugin()
	plugin.initialize({"enabled": True}, ctx)
	await plugin._tool_orchestration_task_create(
		{"agent_names": ["alpha"], "mode": "discussion", "topic": "ship?"},
		{},
	)

	status = await plugin._tool_orchestration_task_get({"task_id": "task-1"}, {})

	assert status.content["task_id"] == "task-1"
