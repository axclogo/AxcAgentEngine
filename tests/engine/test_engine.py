"""Tests for Engine — agent loading, lifecycle, configuration."""
import pytest

from axc_agent_engine.engine import Engine
from axc_agent_engine.llm.config import LLMConfig
from axc_agent_engine.core.errors import ConfigError, SchemaError
from axc_agent_engine.agent import Agent
from axc_agent_engine.observability.audit import InMemoryAuditSink
from axc_agent_engine.plugins.builtin.compress.plugin import CompressPlugin
from axc_agent_engine.plugins.registry import PluginRegistry
from axc_agent_engine.runtime.checkpoint import InMemoryCheckpointStore
from axc_agent_engine.runtime.sandbox_local import LocalSubprocessExecutor


@pytest.fixture
def llm_config():
	return LLMConfig(base_url="http://localhost:8080", api_key="test", model="test-model")


@pytest.fixture
def agent_yaml(tmp_path):
	yaml_content = """
name: test_agent
description: A test agent
system_prompt: You are helpful
runtime:
  max_rounds: 10
  thinking: auto
plugins:
  compress:
    enabled: true
"""
	path = tmp_path / "agent.yaml"
	path.write_text(yaml_content)
	return str(path)


@pytest.fixture
def compress_registry():
	registry = PluginRegistry()
	registry.register(CompressPlugin)
	return registry


@pytest.fixture
def agent_yaml_with_prompt_file(tmp_path):
	prompt_content = "You are a specialized agent"
	(tmp_path / "prompt.txt").write_text(prompt_content)
	yaml_content = """
name: prompt_file_agent
system_prompt_file: prompt.txt
"""
	path = tmp_path / "agent.yaml"
	path.write_text(yaml_content)
	return str(path)


class TestEngine:
	def test_init(self, llm_config):
		engine = Engine(default_llm=llm_config)
		assert engine._default_client is not None

	def test_init_with_provider(self, mock_llm):
		engine = Engine(default_llm=mock_llm)
		assert engine._default_client is mock_llm

	def test_load_agent(self, llm_config, agent_yaml, compress_registry):
		engine = Engine(default_llm=llm_config, plugin_registry=compress_registry)
		agent = engine.load_agent(agent_yaml)
		assert isinstance(agent, Agent)
		assert agent.name == "test_agent"

	def test_default_plugin_registry_is_empty(self, llm_config, agent_yaml):
		engine = Engine(default_llm=llm_config)
		agent = engine.load_agent(agent_yaml)
		assert agent._plugins == []

	def test_mock_provider_tool_name_mapping_falls_back_to_default(self, mock_llm, agent_yaml, compress_registry):
		engine = Engine(default_llm=mock_llm, plugin_registry=compress_registry)
		agent = engine.load_agent(agent_yaml)
		assert agent.registry.model_name("mcp.github.search") == "mcp_github_search"

	def test_load_agent_nonexistent(self, llm_config):
		engine = Engine(default_llm=llm_config)
		with pytest.raises(ConfigError):
			engine.load_agent("/nonexistent/path.yaml")

	def test_load_agent_invalid_yaml(self, llm_config, tmp_path):
		path = tmp_path / "bad.yaml"
		path.write_text("{{invalid yaml")
		engine = Engine(default_llm=llm_config)
		with pytest.raises(SchemaError):
			engine.load_agent(str(path))

	def test_load_agent_schema_error(self, llm_config, tmp_path):
		path = tmp_path / "bad.yaml"
		path.write_text("name: test\nunknown_field: x\n")
		engine = Engine(default_llm=llm_config)
		with pytest.raises(SchemaError):
			engine.load_agent(str(path))

	def test_load_agent_with_prompt_file(self, llm_config, agent_yaml_with_prompt_file):
		engine = Engine(default_llm=llm_config)
		agent = engine.load_agent(agent_yaml_with_prompt_file)
		assert agent._system_prompt == "You are a specialized agent"

	def test_get_agent(self, llm_config, agent_yaml, compress_registry):
		engine = Engine(default_llm=llm_config, plugin_registry=compress_registry)
		engine.load_agent(agent_yaml)
		agent = engine.get_agent("test_agent")
		assert agent is not None
		assert agent.name == "test_agent"

	def test_get_agent_nonexistent(self, llm_config):
		engine = Engine(default_llm=llm_config)
		assert engine.get_agent("nope") is None

	def test_list_agents(self, llm_config, agent_yaml, compress_registry):
		engine = Engine(default_llm=llm_config, plugin_registry=compress_registry)
		engine.load_agent(agent_yaml)
		agents = engine.list_agents()
		assert len(agents) == 1

	def test_execution_services_injected_into_agent(self, llm_config, agent_yaml, compress_registry):
		audit = InMemoryAuditSink()
		checkpoints = InMemoryCheckpointStore()
		command_executor = LocalSubprocessExecutor()
		engine = Engine(
			default_llm=llm_config,
			audit_sink=audit,
			checkpoint_store=checkpoints,
			command_executor=command_executor,
			plugin_registry=compress_registry,
		)
		agent = engine.load_agent(agent_yaml)
		executor = agent._create_executor("session-1")
		assert executor._ctx.services.audit_sink is audit
		assert executor._ctx.services.checkpoint_store is checkpoints
		assert executor._ctx.services.command_executor is command_executor
		assert executor._ctx.state.metadata["agent_name"] == "test_agent"
		assert executor._ctx.state.metadata["session_id"] == "session-1"
		assert executor._ctx.runtime.agent_info.name == "test_agent"
		assert executor._ctx.runtime.agent_info.session_id == "session-1"
		assert executor._ctx.state.metadata["agent"]["name"] == "test_agent"
		assert executor._ctx.state.metadata["agent"]["session_id"] == "session-1"

	@pytest.mark.asyncio
	async def test_unload_agent(self, llm_config, agent_yaml, compress_registry):
		engine = Engine(default_llm=llm_config, plugin_registry=compress_registry)
		engine.load_agent(agent_yaml)
		await engine.unload_agent("test_agent")
		assert engine.get_agent("test_agent") is None

	@pytest.mark.asyncio
	async def test_close(self, mock_llm, llm_config):
		engine = Engine(default_llm=llm_config)
		await engine.close()
		# Should complete without error
