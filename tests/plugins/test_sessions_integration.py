"""End-to-end tests for the sessions plugin. 会话操作插件的端到端测试。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from axc_agent_engine import AgentModels, Engine, EventType, PluginRegistry
from axc_agent_engine.core.errors import ConfigValueError
from axc_agent_engine.core.schema import LLMMessage, LLMResponse, LLMStreamChunk, LLMUsage
from axc_agent_engine.plugins.builtin.sessions import SessionsPlugin
from axc_agent_engine.plugins.strategies.react import ReActPlugin
from axc_agent_engine.tools.name_mapping import ToolNameMappingConfig
from tests.plugins.sessions_fake import FakeSessionService


class ScriptedProvider:
	model = "test"
	tool_name_mapping = ToolNameMappingConfig()

	def __init__(self, responses: list[LLMResponse]) -> None:
		self.responses = responses
		self.index = 0
		self.calls: list[list[dict]] = []

	async def chat(self, messages, tools=None, **kwargs):
		self.calls.append([dict(message) for message in messages])
		response = self.responses[min(self.index, len(self.responses) - 1)]
		self.index += 1
		return response

	async def stream(self, messages, tools=None, **kwargs):
		response = await self.chat(messages, tools, **kwargs)
		message = response.message
		if message.tool_calls:
			for index, tool_call in enumerate(message.tool_calls):
				yield LLMStreamChunk(tool_call_delta={**tool_call, "index": index})
			return
		if message.content:
			yield LLMStreamChunk(content_delta=message.content, usage=response.usage)

	async def ask(self, messages, **kwargs) -> str:
		return "ok"

	async def close(self) -> None:
		pass


def _response(content: str = "", tool_calls: list[dict] | None = None) -> LLMResponse:
	return LLMResponse(
		message=LLMMessage(content=content, tool_calls=tool_calls or []),
		usage=LLMUsage(input_tokens=1, output_tokens=1),
	)


def _tool_call(name: str, arguments: dict) -> dict:
	return {"id": "tc-1", "type": "function", "function": {"name": name, "arguments": json.dumps(arguments)}}


def _agent_yaml(tmp_path: Path, capabilities: list[str]) -> Path:
	path = tmp_path / "sessions-agent.yaml"
	path.write_text(
		yaml.safe_dump(
			{
				"name": "sessions-agent",
				"runtime": {"strategy": "react", "allowed_capabilities": capabilities},
				"plugins": {"react": {"enabled": True}, "sessions": {"enabled": True}},
			}
		),
		encoding="utf-8",
	)
	return path


async def test_agent_calls_session_list_end_to_end(tmp_path: Path) -> None:
	provider = ScriptedProvider([_response(tool_calls=[_tool_call("session_list", {})]), _response("done")])
	registry = PluginRegistry()
	registry.register_many([ReActPlugin, SessionsPlugin])
	engine = Engine(plugin_registry=registry)
	try:
		template = engine.load_agent_template(str(_agent_yaml(tmp_path, ["session_read"])))
		agent = template.instantiate(
			models=AgentModels(default=provider),
			mounts={"sessions.service": FakeSessionService()},
			metadata={"user_id": "u-1"},
		)
		events = [event async for event in agent.stream("列表", session_id="s1", metadata={"session_title": "写周报"})]
		assert events[-1].type == EventType.DONE
		tool_messages = [message for message in provider.calls[-1] if message.get("role") == "tool"]
		assert any("s2" in str(message.get("content")) for message in tool_messages)
	finally:
		await engine.close()


async def test_capability_gate_denies_session_tool(tmp_path: Path) -> None:
	provider = ScriptedProvider([_response(tool_calls=[_tool_call("session_list", {})]), _response("done")])
	registry = PluginRegistry()
	registry.register_many([ReActPlugin, SessionsPlugin])
	engine = Engine(plugin_registry=registry)
	try:
		template = engine.load_agent_template(str(_agent_yaml(tmp_path, [])))
		agent = template.instantiate(
			models=AgentModels(default=provider),
			mounts={"sessions.service": FakeSessionService()},
		)
		events = [event async for event in agent.stream("列表", session_id="s1")]
		assert events[-1].type == EventType.DONE
		tool_messages = [message for message in provider.calls[-1] if message.get("role") == "tool"]
		assert any("not allowed" in str(message.get("content")) for message in tool_messages)
	finally:
		await engine.close()


async def test_session_read_denied_reaches_model(tmp_path: Path) -> None:
	service = FakeSessionService()
	service.decisions[("s2", "read")] = "denied"
	provider = ScriptedProvider(
		[_response(tool_calls=[_tool_call("session_read", {"session_id": "s2"})]), _response("done")]
	)
	registry = PluginRegistry()
	registry.register_many([ReActPlugin, SessionsPlugin])
	engine = Engine(plugin_registry=registry)
	try:
		template = engine.load_agent_template(str(_agent_yaml(tmp_path, ["session_read"])))
		agent = template.instantiate(
			models=AgentModels(default=provider),
			mounts={"sessions.service": service},
		)
		events = [event async for event in agent.stream("读一下", session_id="s1")]
		assert events[-1].type == EventType.DONE
		tool_messages = [message for message in provider.calls[-1] if message.get("role") == "tool"]
		assert any("拒绝授权" in str(message.get("content")) for message in tool_messages)
	finally:
		await engine.close()


@pytest.mark.parametrize("value", [-1, 100001, "5"])
async def test_invalid_max_wakeups_fails_at_agent_load(tmp_path: Path, value) -> None:
	path = tmp_path / "invalid-wakeups.yaml"
	path.write_text(
		yaml.safe_dump(
			{
				"name": "invalid-wakeups",
				"runtime": {"strategy": "react"},
				"plugins": {"react": {"enabled": True}, "sessions": {"enabled": True, "max_wakeups": value}},
			}
		),
		encoding="utf-8",
	)
	registry = PluginRegistry()
	registry.register_many([ReActPlugin, SessionsPlugin])
	engine = Engine(plugin_registry=registry)
	try:
		template = engine.load_agent_template(str(path))
		with pytest.raises(ConfigValueError):
			template.instantiate(models=AgentModels(default=ScriptedProvider([])))
	finally:
		await engine.close()
