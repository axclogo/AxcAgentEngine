"""Tests for collaboration plugin tools."""
from __future__ import annotations

from types import SimpleNamespace

from axc_agent_engine.core.dispatcher import AgentEnvelope
from axc_agent_engine.plugins import PluginContext
from axc_agent_engine.plugins.builtin.collaboration.plugin import CollaborationPlugin
from axc_agent_engine.plugins.builtin.collaboration.plugin import (
	_agent_call_durable_summary,
	_bounded_timeout,
	_collaboration_metadata,
	_task_status,
	_task_to_dict,
)
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


async def test_agent_call_tool_output_contains_durable_result_summary():
	dispatcher = RecordingDispatcher()
	plugin = _plugin([SimpleNamespace(name="worker", description="")], dispatcher)
	result = await plugin._tool_agent_call(
		{"agent_name": "worker", "message": "do it"},
		{"agent_name": "caller"},
	)

	assert not result.is_error
	assert result.metadata["durable"] is True
	assert "worker reply" in result.summary
	assert "worker reply" in result.context_view()


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
	assert result.context_view() == "Available agents (1):\n1. worker\n   Description: ok"
	assert result.display_view() == '{"agents": [{"name": "worker", "description": "ok"}]}'


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


def test_collaboration_disabled_plugin_exposes_no_tools():
	plugin = _plugin([], RecordingDispatcher(), {"enabled": False})

	assert plugin.get_tools() == []


async def test_agent_call_rejects_missing_fields_depth_and_missing_dispatcher():
	plugin = _plugin([SimpleNamespace(name="worker", description="")], RecordingDispatcher(), {"max_depth": 1})
	deep_ctx = SimpleNamespace(runtime=SimpleNamespace(agent_call_depth=1), state=SimpleNamespace(metadata={}))
	no_dispatcher = _plugin([SimpleNamespace(name="worker", description="")], None)

	missing = await plugin._tool_agent_call({"agent_name": "", "message": ""}, {})
	depth = await plugin._tool_agent_call(
		{"agent_name": "worker", "message": "x"},
		{"agent_name": "caller", "exec_ctx": deep_ctx},
	)
	no_bus = await no_dispatcher._tool_agent_call({"agent_name": "worker", "message": "x"}, {"agent_name": "caller"})

	assert missing.is_error
	assert depth.is_error
	assert no_bus.is_error


async def test_agent_call_reply_error_value_error_and_exception_restore_depth():
	class ErrorDispatcher(RecordingDispatcher):
		async def request(self, envelope, timeout=60.0, event_callback=None):
			return AgentEnvelope(type="error", content="child failed")

	class ValueErrorDispatcher(RecordingDispatcher):
		async def request(self, envelope, timeout=60.0, event_callback=None):
			raise ValueError("bad dispatch")

	class CrashDispatcher(RecordingDispatcher):
		async def request(self, envelope, timeout=60.0, event_callback=None):
			raise RuntimeError("boom")

	for dispatcher, expected in [
		(ErrorDispatcher(), "child failed"),
		(ValueErrorDispatcher(), "bad dispatch"),
		(CrashDispatcher(), "Agent 调用失败: boom"),
	]:
		plugin = _plugin([SimpleNamespace(name="worker", description="")], dispatcher)
		exec_ctx = SimpleNamespace(runtime=SimpleNamespace(agent_call_depth=0), state=SimpleNamespace(metadata={}))
		result = await plugin._tool_agent_call(
			{"agent_name": "worker", "message": "x", "timeout": "bad"},
			{"agent_name": "caller", "exec_ctx": exec_ctx},
		)
		assert result.is_error
		assert expected in result.content
		assert exec_ctx.runtime.agent_call_depth == 0


async def test_orchestration_create_validates_service_args_and_exceptions():
	class FailingCreate:
		async def create_task(self, **kwargs):
			raise RuntimeError("create down")

	plugin = CollaborationPlugin()
	plugin.initialize({"enabled": True}, PluginContext(resources=ResourceRegistry({"orchestration": FailingCreate()})))
	no_service = _plugin([], RecordingDispatcher())

	assert (await no_service._tool_orchestration_task_create({}, {})).is_error
	assert (await plugin._tool_orchestration_task_create({"agent_names": "bad", "topic": "x"}, {})).is_error
	result = await plugin._tool_orchestration_task_create({"agent_names": ["a"], "topic": "x", "persona": "bad"}, {})

	assert result.is_error
	assert "create down" in result.content


async def test_orchestration_get_and_cancel_error_boundaries():
	class NoMethods:
		pass

	class SyncCancelService:
		def __init__(self):
			self.tasks = {"task": SimpleNamespace(task_id="task", status="running", agent_names=[], events=list(range(30)))}

		def get_task(self, task_id):
			return self.tasks.get(task_id)

		def cancel_task(self, task_id):
			return task_id == "task"

	plugin = CollaborationPlugin()
	plugin.initialize({"enabled": True}, PluginContext(resources=ResourceRegistry({"orchestration": NoMethods()})))
	sync = CollaborationPlugin()
	sync.initialize({"enabled": True}, PluginContext(resources=ResourceRegistry({"orchestration": SyncCancelService()})))

	assert (await plugin._tool_orchestration_task_get({"task_id": ""}, {})).is_error
	assert (await plugin._tool_orchestration_task_get({"task_id": "x"}, {})).is_error
	assert (await plugin._tool_orchestration_task_cancel({"task_id": ""}, {})).is_error
	assert (await plugin._tool_orchestration_task_cancel({"task_id": "x"}, {})).is_error
	assert (await sync._tool_orchestration_task_get({"task_id": "missing"}, {})).is_error
	status = await sync._tool_orchestration_task_get({"task_id": "task"}, {})
	cancelled = await sync._tool_orchestration_task_cancel({"task_id": "task"}, {})
	assert len(status.content["events"]) == 20
	assert cancelled.content["cancelled"] is True


def test_collaboration_helper_boundaries():
	exec_ctx = SimpleNamespace(state=SimpleNamespace(metadata={"trace_id": "t", "tenant": "x"}))
	task = SimpleNamespace(
		task_id="id",
		status="done",
		mode="m",
		topic="t",
		agent_names=("a", "b"),
		events=list(range(25)),
		result={"ok": True},
		error="",
	)

	assert _bounded_timeout("bad", 7) == 7
	assert _bounded_timeout(0, 7) == 1.0
	assert _bounded_timeout(9999, 7) == 3600.0
	assert _collaboration_metadata({"agent_name": "caller", "session_id": "s"}, exec_ctx, 2) == {
		"trace_id": "t",
		"tenant": "x",
		"agent_call_depth": 2,
		"caller_agent": "caller",
		"caller_session_id": "s",
	}
	assert _agent_call_durable_summary("a", {"x": 1}).startswith("Agent 'a' result")
	assert _task_status(task) == "done"
	assert _task_to_dict(task)["events"] == list(range(5, 25))
