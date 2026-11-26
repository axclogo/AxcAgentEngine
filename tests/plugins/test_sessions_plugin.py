"""Tests for the sessions plugin. 会话操作插件测试。"""

from __future__ import annotations

import pytest

from axc_agent_engine.core.context import ExecutionContext, ExecutionState
from axc_agent_engine.core.errors import CancelledError, ConfigValueError
from axc_agent_engine.plugins.builtin.sessions import SessionsPlugin
from axc_agent_engine.plugins.builtin.sessions.protocol import SessionMessage
from axc_agent_engine.plugins.context import PluginContext
from axc_agent_engine.plugins.registry import PluginRegistry
from axc_agent_engine.runtime.resources import ResourceRegistry
from tests.plugins.sessions_fake import FakeSessionService


class FakeInbox:
	def __init__(self, pending: list[str] | None = None) -> None:
		self.pending = list(pending or [])

	async def peek(self) -> list[str]:
		return list(self.pending)


def make_plugin(service: FakeSessionService | None = None, **config) -> SessionsPlugin:
	plugin = SessionsPlugin()
	resources = {"sessions.service": service} if service is not None else {}
	plugin.initialize({"enabled": True, **config}, PluginContext(resources=ResourceRegistry(resources)))
	return plugin


def make_ctx(metadata: dict | None = None, inbox=None) -> ExecutionContext:
	values = {"session_id": "s1", "agent_name": "agent-a"}
	values.update(metadata or {})
	ctx = ExecutionContext(state=ExecutionState(metadata=values))
	ctx.runtime.inbox = inbox
	return ctx


async def call_tool(plugin: SessionsPlugin, name: str, args: dict, ctx: ExecutionContext):
	tool = next(item for item in plugin.get_tools() if item.name == name)
	assert tool.execute is not None
	return await tool.execute(args, {"exec_ctx": ctx})


def test_tool_surface_defaults() -> None:
	plugin = make_plugin(FakeSessionService())
	assert [tool.name for tool in plugin.get_tools()] == [
		"session_list",
		"session_read",
		"session_watch",
		"session_wait",
		"session_send",
		"session_stop",
		"session_create",
	]


def test_tool_surface_follows_config() -> None:
	read_only = make_plugin(FakeSessionService(), allow_operate=False)
	assert "session_send" not in {tool.name for tool in read_only.get_tools()}
	assert "session_create" in {tool.name for tool in read_only.get_tools()}

	no_create = make_plugin(FakeSessionService(), allow_create=False)
	assert "session_create" not in {tool.name for tool in no_create.get_tools()}
	assert "session_send" in {tool.name for tool in no_create.get_tools()}


@pytest.mark.parametrize(
	"config",
	[
		{"max_depth": 0},
		{"max_active": 0},
		{"wait_timeout": 0},
		{"wait_timeout": 121},
		{"read_limit": 0},
		{"read_max_chars": 999},
		{"cross_agent": "yes"},
	],
)
def test_invalid_config_fails(config: dict) -> None:
	with pytest.raises(ConfigValueError):
		make_plugin(FakeSessionService(), **config)


async def test_missing_service_is_explicit() -> None:
	plugin = make_plugin()
	result = await call_tool(plugin, "session_list", {}, make_ctx())
	assert result.is_error
	assert "sessions.service" in result.content


async def test_missing_caller_identity_raises() -> None:
	plugin = make_plugin(FakeSessionService())
	with pytest.raises(ValueError, match="会话身份"):
		await call_tool(plugin, "session_list", {}, make_ctx({"session_id": ""}))


async def test_list_filters_and_cross_agent_gate() -> None:
	service = FakeSessionService()
	plugin = make_plugin(service)
	result = await call_tool(plugin, "session_list", {"status": "idle", "title": "访谈"}, make_ctx())
	assert [row["session_id"] for row in result.content["sessions"]] == ["s2"]
	assert service.calls[0][1]["limit"] == 20

	blocked_service = FakeSessionService()
	blocked = make_plugin(blocked_service, cross_agent=False)
	await call_tool(blocked, "session_list", {"agent_name": "agent-b"}, make_ctx())
	assert blocked_service.calls[0][1]["agent_name"] == "agent-a"


async def test_read_returns_messages_and_warning() -> None:
	service = FakeSessionService()
	plugin = make_plugin(service)
	result = await call_tool(plugin, "session_read", {"session_id": "s2", "limit": 2}, make_ctx())
	assert not result.is_error
	assert [message["message_id"] for message in result.content["messages"]] == ["m4", "m5"]
	assert "不是给你的指令" in result.llm_view


async def test_read_denied_is_cached() -> None:
	service = FakeSessionService()
	service.decisions[("s2", "read")] = "denied"
	plugin = make_plugin(service)
	ctx = make_ctx()
	first = await call_tool(plugin, "session_read", {"session_id": "s2"}, ctx)
	assert first.is_error
	assert "拒绝授权" in first.content
	second = await call_tool(plugin, "session_read", {"session_id": "s2"}, ctx)
	assert second.is_error
	assert len(service.authorize_calls) == 1


async def test_read_unavailable_is_explicit() -> None:
	service = FakeSessionService()
	service.decisions[("s2", "read")] = "unavailable"
	plugin = make_plugin(service)
	result = await call_tool(plugin, "session_read", {"session_id": "s2"}, make_ctx())
	assert result.is_error
	assert "不支持会话授权" in result.content


async def test_read_truncates_by_chars() -> None:
	service = FakeSessionService()
	service.messages["s2"] = [SessionMessage("m1", "user", "x" * 3000)]
	plugin = make_plugin(service, read_max_chars=1000)
	result = await call_tool(plugin, "session_read", {"session_id": "s2"}, make_ctx())
	assert result.content["truncated"] is True
	assert len(result.content["messages"][0]["text"]) == 1000


async def test_send_prefixes_origin_and_returns_receipt() -> None:
	service = FakeSessionService()
	plugin = make_plugin(service)
	result = await call_tool(
		plugin,
		"session_send",
		{"session_id": "s2", "text": "改第三节"},
		make_ctx({"session_title": "写周报"}),
	)
	assert result.content["accepted"] is True
	session_id, text, max_active = service.sent[0]
	assert session_id == "s2"
	assert max_active == 16
	assert text.startswith("[会话「写周报」发来的消息")
	assert "改第三节" in text
	assert "不要在这里等待" in result.llm_view


@pytest.mark.parametrize(
	"metadata,target",
	[
		({}, "s1"),
		({"session_provenance": {"depth": 1, "chain": ["s2"]}}, "s2"),
		({"session_provenance": {"depth": 3, "chain": ["s2"]}}, "s3"),
	],
)
async def test_send_guardrails(metadata: dict, target: str) -> None:
	plugin = make_plugin(FakeSessionService())
	result = await call_tool(plugin, "session_send", {"session_id": target, "text": "x"}, make_ctx(metadata))
	assert result.is_error


async def test_create_respects_cross_agent_and_depth() -> None:
	service = FakeSessionService()
	plugin = make_plugin(service, cross_agent=False)
	blocked = await call_tool(plugin, "session_create", {"agent_name": "agent-b", "text": "x"}, make_ctx())
	assert blocked.is_error
	created = await call_tool(
		plugin,
		"session_create",
		{"agent_name": "agent-a", "text": "x", "title": "T"},
		make_ctx(),
	)
	assert created.content["session_id"] == "s-new"
	assert service.created[0][0] == "agent-a"

	deep = make_plugin(FakeSessionService(), max_depth=1)
	stopped = await call_tool(
		deep,
		"session_create",
		{"agent_name": "agent-a", "text": "x"},
		make_ctx({"session_provenance": {"depth": 1, "chain": ["s0"]}}),
	)
	assert stopped.is_error


async def test_watch_and_stop() -> None:
	service = FakeSessionService()
	plugin = make_plugin(service)
	watched = await call_tool(plugin, "session_watch", {"session_id": "s2", "enabled": True}, make_ctx())
	assert watched.content["watching"] is True
	assert service.watches == [("s2", True)]
	stopped = await call_tool(plugin, "session_stop", {"session_id": "s3"}, make_ctx())
	assert stopped.content["stopped"] is True


async def test_wait_returns_immediately_when_input_pending() -> None:
	plugin = make_plugin(FakeSessionService(), wait_timeout=10)
	result = await call_tool(plugin, "session_wait", {}, make_ctx(inbox=FakeInbox(["hi"])))
	assert result.content["timed_out"] is False


async def test_wait_times_out() -> None:
	plugin = make_plugin(FakeSessionService(), wait_timeout=1)
	result = await call_tool(plugin, "session_wait", {"timeout_ms": 1000}, make_ctx(inbox=FakeInbox()))
	assert result.content["timed_out"] is True


async def test_wait_rejects_cancelled_run() -> None:
	plugin = make_plugin(FakeSessionService())
	ctx = make_ctx(inbox=FakeInbox())
	ctx.state.cancelled = True
	with pytest.raises(CancelledError):
		await call_tool(plugin, "session_wait", {}, ctx)


async def test_wait_without_inbox_is_error() -> None:
	plugin = make_plugin(FakeSessionService())
	result = await call_tool(plugin, "session_wait", {}, make_ctx())
	assert result.is_error


def test_tools_declare_capabilities() -> None:
	plugin = make_plugin(FakeSessionService())
	tools = {tool.name: tool for tool in plugin.get_tools()}
	assert tools["session_read"].capability == "session_read"
	assert tools["session_send"].capability == "session_operate"
	assert tools["session_read"].is_read_only is True
	assert tools["session_send"].is_read_only is False
	assert tools["session_wait"].timeout <= 130


async def test_authorization_granted_once_per_run() -> None:
	service = FakeSessionService()
	plugin = make_plugin(service)
	ctx = make_ctx()
	await call_tool(plugin, "session_read", {"session_id": "s2"}, ctx)
	await call_tool(plugin, "session_read", {"session_id": "s2"}, ctx)
	assert service.authorize_calls == [("s2", "read")]


async def test_malformed_provenance_fails() -> None:
	plugin = make_plugin(FakeSessionService())
	with pytest.raises(TypeError):
		await call_tool(plugin, "session_read", {"session_id": "s2"}, make_ctx({"session_provenance": "bad"}))


def test_config_schema_is_registered() -> None:
	registry = PluginRegistry()
	registry.register(SessionsPlugin)
	schema = registry.get_plugin_config_schema("sessions")
	fields = {field.key: field for field in schema.fields}
	assert set(fields) >= {
		"allow_create",
		"allow_operate",
		"cross_agent",
		"max_depth",
		"max_active",
		"wait_timeout",
		"read_limit",
		"read_max_chars",
		"max_wakeups",
	}
	assert fields["max_depth"].default == 3
	assert fields["max_active"].default == 16
	assert fields["max_wakeups"].default == 100


async def test_missing_service_is_explicit_for_every_tool() -> None:
	cases = [
		("session_read", {"session_id": "s2"}),
		("session_send", {"session_id": "s2", "text": "x"}),
		("session_stop", {"session_id": "s2"}),
		("session_create", {"agent_name": "agent-a", "text": "x"}),
		("session_watch", {"session_id": "s2"}),
	]
	plugin = make_plugin()
	for name, args in cases:
		result = await call_tool(plugin, name, args, make_ctx())
		assert result.is_error
		assert "sessions.service" in result.content


async def test_execution_context_is_required() -> None:
	plugin = make_plugin(FakeSessionService())
	tool = next(item for item in plugin.get_tools() if item.name == "session_list")
	assert tool.execute is not None
	with pytest.raises(TypeError, match="execution context"):
		await tool.execute({}, {})


async def test_caller_metadata_type_is_validated() -> None:
	plugin = make_plugin(FakeSessionService())
	with pytest.raises(TypeError, match="session_title"):
		await call_tool(plugin, "session_list", {}, make_ctx({"session_title": 1}))


def test_provenance_validation() -> None:
	from axc_agent_engine.plugins.builtin.sessions.plugin import _provenance

	with pytest.raises(ValueError, match="depth"):
		_provenance({"session_provenance": {"depth": -1}})
	with pytest.raises(ValueError, match="chain"):
		_provenance({"session_provenance": {"depth": 0, "chain": [1]}})
	assert _provenance({}) == {"depth": 0, "chain": []}


def test_argument_helpers() -> None:
	from axc_agent_engine.plugins.builtin.sessions.plugin import _int_arg, _list_filters, _required_str
	from axc_agent_engine.plugins.builtin.sessions.protocol import SessionCaller

	with pytest.raises(TypeError):
		_int_arg({"limit": "2"}, "limit", 20, 1, 50)
	with pytest.raises(ValueError, match="between"):
		_int_arg({"limit": 0}, "limit", 20, 1, 50)
	with pytest.raises(ValueError, match="nonempty"):
		_required_str({"text": " "}, "text")
	with pytest.raises(TypeError, match="title"):
		_list_filters({"title": 1}, SessionCaller("s1", "agent-a"), True)


def test_empty_views() -> None:
	from axc_agent_engine.plugins.builtin.sessions.plugin import _list_view, _read_view

	assert "没有" in _list_view([])
	assert "没有" in _read_view("s2", [], False)


async def test_host_contract_type_errors(monkeypatch: pytest.MonkeyPatch) -> None:
	service = FakeSessionService()

	async def bad_list(caller, filters):
		return [{"session_id": "x"}]

	monkeypatch.setattr(service, "list_sessions", bad_list)
	with pytest.raises(TypeError, match="SessionInfo"):
		await call_tool(make_plugin(service), "session_list", {}, make_ctx())

	async def bad_read(caller, session_id, since, limit):
		return [{"message_id": "x"}]

	monkeypatch.setattr(service, "read", bad_read)
	with pytest.raises(TypeError, match="SessionMessage"):
		await call_tool(make_plugin(service), "session_read", {"session_id": "s2"}, make_ctx())


async def test_send_and_create_contract_errors(monkeypatch: pytest.MonkeyPatch) -> None:
	service = FakeSessionService()

	async def bad_send(caller, session_id, text, max_active):
		return []

	monkeypatch.setattr(service, "send", bad_send)
	with pytest.raises(TypeError, match="mapping"):
		await call_tool(make_plugin(service), "session_send", {"session_id": "s2", "text": "x"}, make_ctx())

	async def rejected_send(caller, session_id, text, max_active):
		return {"accepted": False, "reason": "max_active"}

	monkeypatch.setattr(service, "send", rejected_send)
	result = await call_tool(make_plugin(service), "session_send", {"session_id": "s2", "text": "x"}, make_ctx())
	assert result.is_error
	assert "max_active" in result.content

	async def bad_create(caller, agent_name, text, title, max_active):
		return []

	monkeypatch.setattr(service, "create", bad_create)
	with pytest.raises(TypeError, match="mapping"):
		await call_tool(make_plugin(service), "session_create", {"agent_name": "agent-a", "text": "x"}, make_ctx())

	async def missing_id(caller, agent_name, text, title, max_active):
		return {"accepted": True}

	monkeypatch.setattr(service, "create", missing_id)
	with pytest.raises(ValueError, match="session_id"):
		await call_tool(make_plugin(service), "session_create", {"agent_name": "agent-a", "text": "x"}, make_ctx())


async def test_stop_and_watch_validation(monkeypatch: pytest.MonkeyPatch) -> None:
	service = FakeSessionService()
	plugin = make_plugin(service)
	stopped = await call_tool(plugin, "session_stop", {"session_id": "s2"}, make_ctx())
	assert stopped.content["stopped"] is False

	async def bad_stop(caller, session_id):
		return "stopped"

	monkeypatch.setattr(service, "stop", bad_stop)
	with pytest.raises(TypeError, match="mapping"):
		await call_tool(plugin, "session_stop", {"session_id": "s3"}, make_ctx())

	with pytest.raises(TypeError, match="boolean"):
		await call_tool(plugin, "session_watch", {"session_id": "s2", "enabled": "yes"}, make_ctx())


async def test_read_rejects_non_string_since() -> None:
	plugin = make_plugin(FakeSessionService())
	with pytest.raises(TypeError, match="since"):
		await call_tool(plugin, "session_read", {"session_id": "s2", "since": 1}, make_ctx())


async def test_denied_read_blocks_operate_without_new_prompt() -> None:
	service = FakeSessionService()
	service.decisions[("s2", "read")] = "denied"
	plugin = make_plugin(service)
	ctx = make_ctx()
	await call_tool(plugin, "session_read", {"session_id": "s2"}, ctx)
	result = await call_tool(plugin, "session_send", {"session_id": "s2", "text": "x"}, ctx)
	assert result.is_error
	assert len(service.authorize_calls) == 1


async def test_operate_grant_covers_read() -> None:
	service = FakeSessionService()
	plugin = make_plugin(service)
	ctx = make_ctx()
	await call_tool(plugin, "session_send", {"session_id": "s2", "text": "x"}, ctx)
	await call_tool(plugin, "session_read", {"session_id": "s2"}, ctx)
	assert service.authorize_calls == [("s2", "operate")]


async def test_authorize_rejects_unknown_result() -> None:
	service = FakeSessionService()
	service.decisions[("s2", "read")] = "maybe"
	plugin = make_plugin(service)
	with pytest.raises(TypeError, match="granted"):
		await call_tool(plugin, "session_read", {"session_id": "s2"}, make_ctx())


async def test_ancestor_targets_blocked_before_authorization() -> None:
	service = FakeSessionService()
	plugin = make_plugin(service)
	ctx = make_ctx({"session_provenance": {"depth": 2, "chain": ["s2", "s3"]}})
	cases = [
		("session_read", {"session_id": "s2"}),
		("session_watch", {"session_id": "s3"}),
		("session_send", {"session_id": "s2", "text": "x"}),
		("session_stop", {"session_id": "s3"}),
	]
	for name, args in cases:
		result = await call_tool(plugin, name, args, ctx)
		assert result.is_error
		assert "上级会话" in result.content
	assert service.authorize_calls == []


async def test_list_hides_self_and_ancestors() -> None:
	service = FakeSessionService()
	plugin = make_plugin(service)
	ctx = make_ctx({"session_provenance": {"depth": 1, "chain": ["s2"]}})
	result = await call_tool(plugin, "session_list", {}, ctx)
	ids = [row["session_id"] for row in result.content["sessions"]]
	assert "s1" not in ids
	assert "s2" not in ids
	assert "s3" in ids


def test_session_defaults_and_validation() -> None:
	plugin = make_plugin(FakeSessionService())
	assert plugin._max_wakeups == 100
	assert plugin._max_active == 16
	assert plugin._max_depth == 3
	assert make_plugin(FakeSessionService(), max_wakeups=0)._max_wakeups == 0
	bounds = make_plugin(FakeSessionService(), max_wakeups=100000, max_active=256, max_depth=20)
	assert bounds._max_wakeups == 100000
	assert bounds._max_active == 256
	assert bounds._max_depth == 20
	for config in (
		{"max_wakeups": -1},
		{"max_wakeups": 100001},
		{"max_wakeups": "5"},
		{"max_active": 257},
		{"max_depth": 21},
	):
		with pytest.raises(ConfigValueError):
			make_plugin(FakeSessionService(), **config)


def test_caller_turn_origin() -> None:
	plugin = make_plugin(FakeSessionService())
	notice = plugin._caller({"exec_ctx": make_ctx({"turn_origin": "notice"})})
	assert notice.turn_origin == "notice"
	assert notice.to_dict()["turn_origin"] == "notice"
	assert plugin._caller({"exec_ctx": make_ctx()}).turn_origin == "user"
	for bad in ("other", "", "   ", None):
		with pytest.raises(ValueError, match="turn_origin"):
			plugin._caller({"exec_ctx": make_ctx({"turn_origin": bad})})


def test_session_info_scope_is_transport_only() -> None:
	from axc_agent_engine.plugins.builtin.sessions.protocol import SessionInfo

	assert SessionInfo("s9", "agent-a", "T").scope == ""
	assert SessionInfo("s9", "agent-a", "T", scope="topic:1").scope == "topic:1"


async def test_list_view_includes_scope() -> None:
	from axc_agent_engine.plugins.builtin.sessions.protocol import SessionInfo

	service = FakeSessionService()
	service.sessions["s2"] = SessionInfo("s2", "agent-b", "客户访谈", "idle", scope="topic:1")
	plugin = make_plugin(service)
	result = await call_tool(plugin, "session_list", {"agent_name": "agent-b"}, make_ctx())
	assert result.content["sessions"][0]["scope"] == "topic:1"
	assert "scope=topic:1" in result.llm_view
