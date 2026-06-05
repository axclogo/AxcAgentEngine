"""Tests for Engine — agent loading, lifecycle, configuration."""
import pytest

from axc_agent_engine.engine import AgentModels, Engine
from axc_agent_engine.llm.config import LLMConfig
from axc_agent_engine.core.errors import ConfigError, PluginInitError, SchemaError
from axc_agent_engine.core.run_request import RunRequest
from axc_agent_engine.agent import Agent
from axc_agent_engine.plugins import PluginContext
from axc_agent_engine.plugins.base import BasePlugin
from axc_agent_engine.observability.audit import InMemoryAuditSink
from axc_agent_engine.plugins.builtin.compress.plugin import CompressPlugin
from axc_agent_engine.plugins.config_schema import config_field, config_schema
from axc_agent_engine.plugins.registry import PluginRegistry
from axc_agent_engine.runtime.resources import ResourceRegistry
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


class CapturePlugin(BasePlugin):
	name = "capture"
	display_name = "Capture"
	config_schema = config_schema("capture", "Capture", "Test plugin.", [
		config_field("value", "Value", "string", "Captured test value."),
	])
	last_config = None
	last_ctx = None

	def initialize(self, config: dict, plugin_ctx: PluginContext) -> None:
		super().initialize(config, plugin_ctx)
		type(self).last_config = config
		type(self).last_ctx = plugin_ctx


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
		engine = Engine()
		assert engine._agents == {}

	def test_init_with_provider(self, mock_llm):
		engine = Engine()
		agent_models = AgentModels(default=mock_llm)
		assert agent_models.default is mock_llm

	def test_instantiate_agent(self, mock_llm, agent_yaml, compress_registry):
		engine = Engine(plugin_registry=compress_registry)
		agent = engine.load_agent_template(agent_yaml).instantiate(models=AgentModels(default=mock_llm))
		assert isinstance(agent, Agent)
		assert agent.name == "test_agent"

	def test_default_plugin_registry_is_empty(self, mock_llm, agent_yaml):
		engine = Engine()
		with pytest.raises(PluginInitError, match="configured but not registered"):
			engine.load_agent_template(agent_yaml).instantiate(models=AgentModels(default=mock_llm))

	def test_mock_provider_tool_name_mapping_falls_back_to_default(self, mock_llm, agent_yaml, compress_registry):
		engine = Engine(plugin_registry=compress_registry)
		agent = engine.load_agent_template(agent_yaml).instantiate(models=AgentModels(default=mock_llm))
		assert agent.registry.model_name("mcp.github.search") == "mcp_github_search"

	def test_load_agent_template_nonexistent(self, mock_llm):
		engine = Engine()
		with pytest.raises(ConfigError):
			engine.load_agent_template("/nonexistent/path.yaml")

	def test_load_agent_template_invalid_yaml(self, mock_llm, tmp_path):
		path = tmp_path / "bad.yaml"
		path.write_text("{{invalid yaml")
		engine = Engine()
		with pytest.raises(SchemaError):
			engine.load_agent_template(str(path))

	def test_load_agent_template_schema_error(self, mock_llm, tmp_path):
		path = tmp_path / "bad.yaml"
		path.write_text("name: test\nunknown_field: x\n")
		engine = Engine()
		with pytest.raises(SchemaError):
			engine.load_agent_template(str(path))

	def test_instantiate_with_prompt_file(self, mock_llm, agent_yaml_with_prompt_file):
		engine = Engine()
		agent = engine.load_agent_template(agent_yaml_with_prompt_file).instantiate(models=AgentModels(default=mock_llm))
		assert agent._system_prompt == "You are a specialized agent"

	def test_get_agent(self, mock_llm, agent_yaml, compress_registry):
		engine = Engine(plugin_registry=compress_registry)
		engine.load_agent_template(agent_yaml).instantiate(models=AgentModels(default=mock_llm))
		agent = engine.get_agent("test_agent")
		assert agent is not None
		assert agent.name == "test_agent"

	def test_get_agent_nonexistent(self, llm_config):
		engine = Engine()
		assert engine.get_agent("nope") is None

	def test_list_agents(self, mock_llm, agent_yaml, compress_registry):
		engine = Engine(plugin_registry=compress_registry)
		engine.load_agent_template(agent_yaml).instantiate(models=AgentModels(default=mock_llm))
		agents = engine.list_agents()
		assert len(agents) == 1

	def test_execution_services_injected_into_agent(self, mock_llm, agent_yaml, compress_registry):
		audit = InMemoryAuditSink()
		checkpoints = InMemoryCheckpointStore()
		command_executor = LocalSubprocessExecutor()
		engine = Engine(
			audit_sink=audit,
			checkpoint_store=checkpoints,
			command_executor=command_executor,
			plugin_registry=compress_registry,
		)
		agent = engine.load_agent_template(agent_yaml).instantiate(models=AgentModels(default=mock_llm))
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

	def test_instantiate_binds_models_mounts_metadata_and_overrides(self, mock_llm, tmp_path):
		CapturePlugin.last_config = None
		CapturePlugin.last_ctx = None
		path = tmp_path / "agent.yaml"
		path.write_text(
			"""
name: template_agent
system_prompt: base
runtime:
  max_rounds: 3
plugins:
  capture:
    enabled: true
    value: from_yaml
""",
			encoding="utf-8",
		)
		registry = PluginRegistry()
		registry.register(CapturePlugin)
		engine = Engine(resources={"shared": "engine"}, plugin_registry=registry)
		template = engine.load_agent_template(str(path))
		mounts = ResourceRegistry({"shared": "mount", "knowledge.index": object()})

		agent = template.instantiate(
			models=AgentModels(default=mock_llm),
			mounts=mounts,
			metadata={"tenant_id": "t1", "agent_name": "metadata_agent"},
			overrides={
				"runtime.max_rounds": 7,
				"plugins.capture.value": "from_override",
			},
		)

		assert agent._runtime.max_rounds == 7
		assert CapturePlugin.last_config["value"] == "from_override"
		assert CapturePlugin.last_ctx.default_model is mock_llm
		assert CapturePlugin.last_ctx.utility_model is mock_llm
		assert CapturePlugin.last_ctx.resources.require("shared") == "mount"
		assert CapturePlugin.last_ctx.resources.require("knowledge.index") is mounts.require("knowledge.index")
		executor = agent._create_executor("session-1", metadata={"tenant_id": "request"})
		assert executor._ctx.state.metadata["tenant_id"] == "request"
		assert executor._ctx.state.metadata["agent_name"] == "template_agent"

	def test_run_request_merges_run_id_from_run_options(self):
		request = RunRequest.create(
			user_message="hi",
			run_options={"run_id": "run-1"},
			metadata={"external_trace_id": "trace-1001"},
		)
		assert request.metadata["run_id"] == "run-1"
		assert request.metadata["external_trace_id"] == "trace-1001"

	def test_run_request_generates_run_id(self):
		request = RunRequest.create(user_message="hi")
		assert request.metadata["run_id"]

	def test_run_request_rejects_conflicting_run_id_metadata(self):
		with pytest.raises(ValueError, match="run_options.run_id conflicts with metadata.run_id"):
			RunRequest.create(
				user_message="hi",
				run_options={"run_id": "run-1"},
				metadata={"run_id": "other"},
			)

	@pytest.mark.asyncio
	async def test_resume_stream_rejects_conflicting_metadata_run_id(self, mock_llm, tmp_path):
		path = tmp_path / "agent.yaml"
		path.write_text("name: metadata_agent\nsystem_prompt: base\n", encoding="utf-8")
		agent = Engine().load_agent_template(str(path)).instantiate(models=AgentModels(default=mock_llm))
		with pytest.raises(ValueError, match="metadata.run_id conflicts with resume run_id"):
			[event async for event in agent.resume_stream("run-1", metadata={"run_id": "other"})]

	@pytest.mark.asyncio
	async def test_resume_stream_rejects_conflicting_run_options_run_id(self, mock_llm, tmp_path):
		path = tmp_path / "agent.yaml"
		path.write_text("name: metadata_agent\nsystem_prompt: base\n", encoding="utf-8")
		agent = Engine().load_agent_template(str(path)).instantiate(models=AgentModels(default=mock_llm))
		with pytest.raises(ValueError, match="run_options.run_id conflicts with resume run_id"):
			[event async for event in agent.resume_stream("run-1", run_options={"run_id": "other"})]

	@pytest.mark.asyncio
	async def test_stream_with_messages_passes_metadata_to_run_request(self, mock_llm, tmp_path):
		path = tmp_path / "agent.yaml"
		path.write_text("name: metadata_agent\nsystem_prompt: base\n", encoding="utf-8")
		agent = Engine().load_agent_template(str(path)).instantiate(models=AgentModels(default=mock_llm))
		seen = {}

		async def execute_stream(**kwargs):
			seen.update(kwargs)
			if False:
				yield None

		agent._execute_stream = execute_stream
		metadata = {"external_trace_id": "trace-1001", "conversation_id": 123, "agent_profile_id": 9}
		events = [
			event async for event in agent.stream_with_messages(
				[{"role": "user", "content": "hello"}],
				session_id="session-1",
				run_options={"run_id": "run-1"},
				metadata=metadata,
			)
		]
		assert events == []
		assert seen["session_id"] == "session-1"
		assert seen["run_options"] == {"run_id": "run-1"}
		assert seen["metadata"] == metadata

	def test_run_request_metadata_reaches_execution_context(self, mock_llm, tmp_path):
		path = tmp_path / "agent.yaml"
		path.write_text("name: metadata_agent\nsystem_prompt: base\n", encoding="utf-8")
		agent = Engine().load_agent_template(str(path)).instantiate(models=AgentModels(default=mock_llm))
		request = RunRequest.create(
			user_message="hello",
			session_id="session-1",
			stream=True,
			messages=[{"role": "user", "content": "hello"}],
			run_options={"run_id": "run-1"},
			metadata={"external_trace_id": "trace-1001", "conversation_id": 123, "agent_profile_id": 9},
		)
		executor = agent._create_executor(request)
		metadata = executor._ctx.state.metadata
		assert metadata["external_trace_id"] == "trace-1001"
		assert metadata["conversation_id"] == 123
		assert metadata["agent_profile_id"] == 9
		assert metadata["run_id"] == "run-1"
		assert metadata["session_id"] == "session-1"
		assert metadata["agent_name"] == "metadata_agent"

	@pytest.mark.asyncio
	async def test_done_event_contains_aggregated_usage(self, mock_llm, tmp_path):
		path = tmp_path / "agent.yaml"
		path.write_text("name: usage_agent\nsystem_prompt: base\n", encoding="utf-8")
		agent = Engine().load_agent_template(str(path)).instantiate(models=AgentModels(default=mock_llm))
		events = [event async for event in agent.stream("hello", run_options={"run_id": "usage-run", "stream": False})]
		done = next(event for event in events if event.type.value == "done")
		assert done.metadata["usage"]["input_tokens"] == 10
		assert done.metadata["usage"]["output_tokens"] == 5
		assert done.metadata["usage"]["total_tokens"] == 15

	@pytest.mark.asyncio
	async def test_engine_cancel_run_emits_cancelled(self, tmp_path):
		import asyncio
		from axc_agent_engine.core.schema import LLMMessage, LLMResponse

		class SlowLLM:
			model = "slow"
			async def chat(self, messages, tools=None, **kwargs):
				await asyncio.sleep(10)
				return LLMResponse(message=LLMMessage(content="late"))
			async def stream(self, messages, tools=None, **kwargs):
				raise AssertionError("chat expected")
			async def ask(self, prompt, **kwargs):
				return ""
			async def close(self):
				pass

		path = tmp_path / "agent.yaml"
		path.write_text("name: cancel_agent\nsystem_prompt: base\n", encoding="utf-8")
		engine = Engine()
		agent = engine.load_agent_template(str(path)).instantiate(models=AgentModels(default=SlowLLM()))

		async def collect():
			return [event async for event in agent.stream("hello", run_options={"run_id": "cancel-run", "stream": False})]

		task = asyncio.create_task(collect())
		for _ in range(100):
			if engine._run_control.token("cancel-run"):
				break
			await asyncio.sleep(0.01)
		assert engine.cancel_run("cancel-run", "user requested") is True
		events = await task
		assert events[-1].type.value == "cancelled"
		assert events[-1].content == "user requested"

	def test_instantiate_rejects_invalid_overrides(self, mock_llm, agent_yaml):
		engine = Engine()
		template = engine.load_agent_template(agent_yaml)
		with pytest.raises(SchemaError):
			template.instantiate(
				models=AgentModels(default=mock_llm),
				overrides={"runtime.max_rounds": 0},
			)
		with pytest.raises(SchemaError, match="mounts"):
			template.instantiate(
				models=AgentModels(default=mock_llm),
				overrides={"plugins.graph.store": "graph.store"},
			)
		with pytest.raises(SchemaError, match="YAML-serializable"):
			template.instantiate(
				models=AgentModels(default=mock_llm),
				overrides={"plugins.capture.value": object()},
			)

	@pytest.mark.asyncio
	async def test_unload_agent(self, mock_llm, agent_yaml, compress_registry):
		engine = Engine(plugin_registry=compress_registry)
		engine.load_agent_template(agent_yaml).instantiate(models=AgentModels(default=mock_llm))
		await engine.unload_agent("test_agent")
		assert engine.get_agent("test_agent") is None

	@pytest.mark.asyncio
	async def test_close(self, mock_llm, llm_config):
		engine = Engine()
		await engine.close()
		# Should complete without error
