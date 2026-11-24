"""Exercise the transaction at the real host boundary. 从真实宿主入口验证加载事务。"""

from typing import ClassVar

import pytest
import yaml

from axc_agent_engine import AgentModels, BasePlugin, Engine, PluginRegistry
from axc_agent_engine.core.errors import PluginInitError
from axc_agent_engine.plugins.config_schema import config_schema


def host(tmp_path, log, *, failure="", circular=False):
	class Dependency(BasePlugin):
		name = "dep"
		config_schema = config_schema(name, "", "", [])
		depends_on: ClassVar[list[str]] = ["child"] if circular else []

		def initialize(self, config, ctx):
			log.append("open dep")

		async def close(self):
			log.append("close dep")

	class Child(BasePlugin):
		name = "child"
		config_schema = config_schema(name, "", "", [])
		depends_on: ClassVar[list[str]] = ["dep"]

		def initialize(self, config, ctx):
			log.append("open child")
			if failure == "init":
				raise ValueError("init failed")

		def get_tools(self):
			if failure == "tools":
				raise RuntimeError("tools failed")
			return []

		async def close(self):
			log.append("close child")
			if failure == "init":
				raise RuntimeError("cleanup failed")

	path = tmp_path / "agent.yaml"
	path.write_text(
		yaml.safe_dump({"name": "lifecycle", "plugins": {"child": {"enabled": True}, "dep": {"enabled": True}}})
	)
	registry = PluginRegistry()
	registry.register_many([Child, Dependency])
	engine = Engine(plugin_registry=registry)
	return engine, engine.load_agent_template(str(path))


async def test_initialization_and_close_respect_dependencies(tmp_path, mock_llm):
	log = []
	engine, template = host(tmp_path, log)
	agent = template.instantiate(models=AgentModels(default=mock_llm))
	assert agent.inspect()["plugins"] == ["dep", "child"]
	await engine.close()
	assert log == ["open dep", "open child", "close child", "close dep"]


@pytest.mark.parametrize("failure,expected", [("init", PluginInitError), ("tools", RuntimeError)])
async def test_rollback_in_same_loop_finishes_before_engine_close(tmp_path, mock_llm, failure, expected):
	log = []
	engine, template = host(tmp_path, log, failure=failure)
	with pytest.raises(expected, match=f"{failure} failed"):
		template.instantiate(models=AgentModels(default=mock_llm))
	assert engine.list_agents() == []
	await engine.close()
	assert log == ["open dep", "open child", "close child", "close dep"]


def test_sync_failure_releases_before_return(tmp_path, mock_llm):
	log = []
	_, template = host(tmp_path, log, failure="init")
	with pytest.raises(PluginInitError, match="init failed"):
		template.instantiate(models=AgentModels(default=mock_llm))
	assert log == ["open dep", "open child", "close child", "close dep"]


@pytest.mark.parametrize("circular", [True, False])
def test_invalid_graph_has_no_init_side_effects(tmp_path, mock_llm, circular):
	log = []
	_, template = host(tmp_path, log, circular=circular)
	overrides = {} if circular else {"plugins.dep.enabled": False}
	with pytest.raises(PluginInitError, match="Circular|depends on"):
		template.instantiate(models=AgentModels(default=mock_llm), overrides=overrides)
	assert log == []


async def test_async_instantiation_waits_for_rollback(tmp_path, mock_llm):
	log = []
	engine, template = host(tmp_path, log, failure="tools")
	with pytest.raises(RuntimeError, match="tools failed"):
		await template.instantiate_async(models=AgentModels(default=mock_llm))
	assert log == ["open dep", "open child", "close child", "close dep"]
	await engine.close()


def test_querying_schema_cannot_mutate_another_hosts_validation():
	from axc_agent_engine.plugins.builtin import PORPlugin

	registry = PluginRegistry()
	registry.register(PORPlugin)
	mode = next(f for f in registry.get_plugin_config_schema("por").fields if f.key == "mode")
	mode.enum.clear()
	other = PluginRegistry()
	other.register(PORPlugin)
	assert next(f for f in other.get_plugin_config_schema("por").fields if f.key == "mode").enum == [
		"por_first",
		"auto",
	]
