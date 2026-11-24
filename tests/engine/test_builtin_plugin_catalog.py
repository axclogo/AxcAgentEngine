"""Exercise the official catalog through a host. 通过宿主入口验证官方插件目录。"""

import sys

import pytest
import yaml

from axc_agent_engine import AgentModels, Engine, EventType, PluginRegistry
from axc_agent_engine.plugins.builtin import (
	AVAILABLE_BUILTIN_PLUGINS,
	OutputFormatPlugin,
	PostProcessPlugin,
	ReActPlugin,
)
from axc_agent_engine.plugins.builtin.graph.support import InMemoryGraphStore
from axc_agent_engine.plugins.builtin.output_format.plugin import OutputContractError
from axc_agent_engine.storage.in_memory import InMemoryKVStore, InMemoryMessageBus, InMemorySpanStore
from tests.engine.test_engine_dispatcher_lifecycle import SequenceLLMProvider, _response
from tests.planning.test_routing_integration import PLAN_CONTENT
from tests.plugins.test_mcp_plugin import SERVER_CODE


@pytest.fixture
async def builtin_host(tmp_path):
	registry = PluginRegistry()
	registry.register_many(AVAILABLE_BUILTIN_PLUGINS.values())
	engine = Engine(
		plugin_registry=registry,
		kv_store=InMemoryKVStore(),
		message_bus=InMemoryMessageBus(),
		span_store=InMemorySpanStore(),
	)
	plugins = {name: {"enabled": True} for name in AVAILABLE_BUILTIN_PLUGINS}
	plugins["mcp"]["servers"] = [{"name": "local", "command": sys.executable, "args": ["-c", SERVER_CODE]}]
	plugins["builtin_tools"]["load"] = ["get_time"]
	plugins["post_process"]["append_stats"] = True
	path = tmp_path / "catalog.yaml"
	path.write_text(
		yaml.safe_dump({"name": "catalog", "runtime": {"strategy": "react"}, "plugins": plugins}), encoding="utf-8"
	)
	try:
		yield engine.load_agent_template(str(path))
	finally:
		await engine.close()


@pytest.mark.parametrize("strategy", ["react", "por"])
async def test_official_plugins_run_together(builtin_host, strategy):
	echo = {"type": "function", "id": "echo", "function": {"name": "mcp_local_echo", "arguments": '{"text":"ok"}'}}
	responses = [_response(tool_calls=[echo])] if strategy == "react" else [_response(PLAN_CONTENT), _response("step")]
	provider = SequenceLLMProvider([*responses, _response("complete")])
	provider.ask.return_value = '{"action":"done","step_ok":true,"goal_achieved":true}'
	agent = await builtin_host.instantiate_async(
		models=AgentModels(default=provider),
		mounts={
			"graph.store": InMemoryGraphStore(),
			"skill.catalog": [{"name": "example", "content": "Run a task"}],
			"knowledge.documents": [{"id": "guide", "text": "Project guide"}],
		},
		overrides={"runtime.strategy": strategy},
	)
	events = [event async for event in agent.stream("complete the task")]
	assert events[-1].type == EventType.DONE, events[-1]
	assert events[-1].content.startswith("complete\n\n---\n执行统计:")
	if strategy == "react":
		assert any(event.type == EventType.TOOL_RESULT and event.content == "echo:ok" for event in events)
	else:
		assert any(event.type == EventType.PLAN_CREATED for event in events)


@pytest.mark.parametrize("append_stats", [False, True])
async def test_output_contract_checks_delivered_text(tmp_path, mock_llm, append_stats):
	registry = PluginRegistry()
	registry.register_many([OutputFormatPlugin, PostProcessPlugin, ReActPlugin])
	engine = Engine(plugin_registry=registry)
	plugins = {
		"react": {"enabled": True},
		"output_format": {"enabled": True, "type": "text", "strict": True, "auto_repair": False, "max_output_chars": 5},
		"post_process": {"enabled": True},
	}
	if append_stats:
		plugins["post_process"]["append_stats"] = True
	path = tmp_path / "contract.yaml"
	path.write_text(yaml.safe_dump({"name": "contract", "plugins": plugins}), encoding="utf-8")
	try:
		agent = engine.load_agent_template(str(path)).instantiate(models=AgentModels(default=mock_llm))
		if append_stats:
			with pytest.raises(OutputContractError, match="max_output_chars"):
				await agent.chat("hello")
		else:
			assert await agent.chat("hello") == "hello"
	finally:
		await engine.close()
