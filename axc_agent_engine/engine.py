"""Engine — Agent template loading and runtime infrastructure.
中文：Agent 模板加载与运行基础设施。

English: Owns shared services and plugin registry; Agent instances receive
their own model objects during template instantiation.
中文：Engine 持有共享服务和插件注册表，Agent 实例在模板实例化时接收自己的模型对象。
"""
from __future__ import annotations

import copy
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from axc_agent_engine.agent import Agent
from axc_agent_engine.runtime.concurrency import ConcurrencyConfig, ExecutionLimiter
from axc_agent_engine.core.context import ExecutionServices
from axc_agent_engine.core.session_manager import SessionManager
from axc_agent_engine.core.errors import ConfigError, SchemaError
from axc_agent_engine.runtime.input import InputProvider, PassthroughInputProvider
from axc_agent_engine.llm.provider import LLMProvider
from axc_agent_engine.plugins import PluginContext, agent_info_from_runtime, model_info_from_models
from axc_agent_engine.plugins.loader import load_plugins
from axc_agent_engine.plugins.registry import PluginRegistry
from axc_agent_engine.runtime.policy import PolicyEvaluator
from axc_agent_engine.runtime.resources import ResourceRegistry, ensure_resource_registry
from axc_agent_engine.runtime.sandbox_models import CommandExecutor
from axc_agent_engine.core.schema import AgentConfig
from axc_agent_engine.storage.protocols import (
	AuditSink,
	CheckpointStore,
	KVStore,
	MessageBus,
	MessagePersistence,
	ResultStore,
	SpanStore,
)
from axc_agent_engine.tools.name_mapping import ToolNameMappingConfig
from axc_agent_engine.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

_RESOURCE_OVERRIDE_PATHS = {
	"plugins.knowledge.index",
	"plugins.knowledge.documents",
	"plugins.knowledge.embedding",
	"plugins.knowledge.vector_store",
	"plugins.knowledge.reranker",
	"plugins.graph.store",
	"plugins.skill.catalog",
	"plugins.tracing.exporter",
}


@dataclass(frozen=True)
class AgentModels:
	"""Model objects bound to one Agent instance.
中文：绑定到单个 Agent 实例的模型对象。"""
	default: LLMProvider
	utility: LLMProvider | None = None
	fallback: LLMProvider | None = None

	def __post_init__(self) -> None:
		if self.default is None:
			raise ConfigError("AgentModels.default is required")

	@property
	def utility_or_default(self) -> LLMProvider:
		return self.utility or self.default


class AgentConfigLoader:
	def load(self, yaml_path: str) -> tuple[Path, AgentConfig, str]:
		path = Path(yaml_path)
		if not path.exists():
			raise ConfigError(f"Agent YAML 不存在: {yaml_path}")
		try:
			raw = yaml.safe_load(path.read_text(encoding="utf-8"))
		except yaml.YAMLError as e:
			raise SchemaError(f"YAML 解析失败: {e}") from e
		try:
			config = AgentConfig(**raw)
		except Exception as e:
			raise SchemaError(f"Agent YAML 校验失败: {e}") from e
		system_prompt = config.system_prompt
		if not system_prompt and config.system_prompt_file:
			prompt_path = path.parent / config.system_prompt_file
			if prompt_path.exists():
				system_prompt = prompt_path.read_text(encoding="utf-8")
			else:
				raise ConfigError(f"system_prompt_file 不存在: {prompt_path}")
		return path, config, system_prompt


class Engine:
	"""Agent 执行引擎。

	English: Agent execution engine used by hosts to load, run, and unload agents.
	"""

	def __init__(
		self,
		kv_store: KVStore | None = None,
		message_persistence: MessagePersistence | None = None,
		span_store: SpanStore | None = None,
		message_bus: MessageBus | None = None,
		result_store: "ResultStore | None" = None,
		audit_sink: AuditSink | None = None,
		checkpoint_store: CheckpointStore | None = None,
		command_executor: CommandExecutor | None = None,
		policy_evaluator: PolicyEvaluator | None = None,
		input_provider: InputProvider | None = None,
		resources: dict[str, object] | ResourceRegistry | None = None,
		concurrency: ConcurrencyConfig | None = None,
		plugin_registry: PluginRegistry | None = None,
	) -> None:
		from axc_agent_engine.core.dispatcher import AgentMessageDispatcher
		self._config_loader = AgentConfigLoader()
		self._kv_store = kv_store
		self._message_persistence = message_persistence
		self._span_store = span_store
		self._message_bus = message_bus
		self._result_store = result_store
		self._audit_sink = audit_sink
		self._checkpoint_store = checkpoint_store
		self._command_executor = command_executor
		self._policy_evaluator = policy_evaluator
		self._input_provider = input_provider or PassthroughInputProvider()
		self._resources = ensure_resource_registry(resources)
		self._concurrency = concurrency or ConcurrencyConfig()
		self._plugin_registry = plugin_registry or PluginRegistry()
		self._engine_limiter = ExecutionLimiter(
			self._concurrency.max_engine_concurrent_runs,
			self._concurrency.queue_timeout,
			name="engine",
		)
		self._dispatcher: AgentMessageDispatcher | None = None
		if message_bus:
			self._dispatcher = AgentMessageDispatcher(message_bus=message_bus)
		self._execution_services = ExecutionServices(
			result_store=result_store,
			message_bus=message_bus,
			dispatcher=self._dispatcher,
			audit_sink=audit_sink,
			checkpoint_store=checkpoint_store,
			command_executor=command_executor,
			policy_evaluator=policy_evaluator,
		)
		self._agents: dict[str, Agent] = {}
		self._session_manager = SessionManager(persistence=message_persistence)

	@property
	def resources(self) -> ResourceRegistry:
		"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
访问共享资源注册表。

		English: Access the shared resource registry injected into plugins.
		"""
		return self._resources

	@property
	def plugin_registry(self) -> PluginRegistry:
		"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
访问 Engine 级插件注册表。
		Access the engine-scoped plugin registry.
		"""
		return self._plugin_registry

	def load_agent_template(self, yaml_path: str) -> "AgentTemplate":
		"""Load Agent YAML as a reusable template without binding models or plugins.
中文：加载 Agent YAML 为可复用模板，不绑定模型或插件。"""
		path, config, system_prompt = self._config_loader.load(yaml_path)
		return AgentTemplate(self, path, config, system_prompt)

	def _instantiate_agent(
		self,
		template: "AgentTemplate",
		models: AgentModels,
		mounts: dict[str, object] | ResourceRegistry | None = None,
		metadata: dict[str, Any] | None = None,
		overrides: dict[str, Any] | None = None,
	) -> Agent:
		config, system_prompt = template._materialize(overrides)
		resources = self._build_instance_resources(mounts)
		agent_default = models.default
		agent_fallback = models.fallback
		agent_utility = models.utility_or_default
		plugin_ctx = PluginContext(
			default_model=agent_default,
			fallback_model=agent_fallback,
			utility_model=agent_utility,
			kv_store=self._kv_store,
			message_persistence=self._message_persistence,
			span_store=self._span_store,
			message_bus=self._message_bus,
			result_store=self._result_store,
			resources=resources,
			dispatcher=self._dispatcher,
			workspace=config.runtime.workspace,
			agent_getter=self.get_agent,
			agent_lister=self.list_agents,
			model_info=model_info_from_models(agent_default, agent_fallback, agent_utility),
			agent_info=agent_info_from_runtime(
				name=config.name,
				description=config.description,
				workspace=config.runtime.workspace,
				routing_mode=config.runtime.routing.mode if config.runtime.routing else "auto",
			),
		)
		#English: Bilingual note. 中文：提前创建 registry，确保插件 initialize() 阶段可访问。
		#English: English: Create the registry before plugin initialization so plugins can register tools. 中文：源码说明。
		registry = ToolRegistry(name_mapping=self._provider_tool_name_mapping(agent_default))
		plugin_ctx.tool_registry = registry
		plugins_raw = {k: v.model_dump() for k, v in config.plugins.items()}
		active_plugins = load_plugins(plugins_raw, plugin_ctx, self._plugin_registry)
		agent = Agent(
			name=config.name,
			description=config.description,
			system_prompt=system_prompt,
			runtime=config.runtime,
			plugins=active_plugins,
			default_model=agent_default,
			fallback_model=agent_fallback,
			utility_model=agent_utility,
			session_manager=self._session_manager,
			registry=registry,
			services=self._execution_services,
			input_provider=self._input_provider,
			engine_limiter=self._engine_limiter,
			metadata=metadata or {},
		)
		self._agents[config.name] = agent
		if self._dispatcher:
			self._dispatcher.run_agent_consumer(agent)
		return agent

	def get_agent(self, name: str) -> Agent | None:
		return self._agents.get(name)

	def list_agents(self) -> list[Agent]:
		return list(self._agents.values())

	async def unload_agent(self, name: str) -> None:
		agent = self._agents.pop(name, None)
		if agent:
			if self._dispatcher:
				await self._dispatcher.stop_consumer(name)
			await agent.close()

	async def close(self, timeout: float = 30.0) -> None:
		"""English: This documentation describes the related engine component behavior.
中文：优雅关闭。"""
		if self._dispatcher:
			await self._dispatcher.stop_all()
		for agent in self._agents.values():
			await agent.close()
		self._agents.clear()

	@staticmethod
	def _provider_tool_name_mapping(provider: LLMProvider) -> ToolNameMappingConfig | None:
		config = getattr(provider, "tool_name_mapping", None)
		return config if isinstance(config, ToolNameMappingConfig) else None

	def _build_instance_resources(self, mounts: dict[str, object] | ResourceRegistry | None) -> ResourceRegistry:
		resources = self._resources.as_dict()
		if isinstance(mounts, ResourceRegistry):
			resources.update(mounts.as_dict())
		elif mounts:
			resources.update(mounts)
		return ResourceRegistry(resources)


class AgentTemplate:
	"""Reusable Agent YAML template.
中文：可复用的 Agent YAML 模板。

	Plugins and model objects are bound only when instantiate() is called.
中文：插件和模型对象只在 instantiate() 调用时绑定。
	"""

	def __init__(self, engine: Engine, path: Path, config: AgentConfig, system_prompt: str) -> None:
		self._engine = engine
		self.path = path
		self.config = config
		self.system_prompt = system_prompt

	def instantiate(
		self,
		*,
		models: AgentModels,
		mounts: dict[str, object] | ResourceRegistry | None = None,
		metadata: dict[str, Any] | None = None,
		overrides: dict[str, Any] | None = None,
	) -> Agent:
		return self._engine._instantiate_agent(
			self,
			models=models,
			mounts=mounts,
			metadata=metadata,
			overrides=overrides,
		)

	def _materialize(self, overrides: dict[str, Any] | None = None) -> tuple[AgentConfig, str]:
		raw = copy.deepcopy(self.config.model_dump())
		if overrides:
			_apply_overrides(raw, overrides)
		try:
			config = AgentConfig(**raw)
		except Exception as e:
			raise SchemaError(f"Agent overrides 校验失败: {e}") from e
		system_prompt = config.system_prompt
		if not system_prompt and config.system_prompt_file:
			prompt_path = self.path.parent / config.system_prompt_file
			if prompt_path.exists():
				system_prompt = prompt_path.read_text(encoding="utf-8")
			else:
				raise ConfigError(f"system_prompt_file 不存在: {prompt_path}")
		return config, system_prompt


def _apply_overrides(raw: dict[str, Any], overrides: dict[str, Any]) -> None:
	for path, value in overrides.items():
		path = str(path)
		if path in _RESOURCE_OVERRIDE_PATHS:
			raise SchemaError(f"{path} is a runtime resource; bind it with mounts, not overrides")
		if not _is_yaml_value(value):
			raise SchemaError("Agent overrides only accept YAML-serializable values; bind runtime objects with mounts")
		parts = str(path).split(".")
		if not parts or any(not part for part in parts):
			raise SchemaError(f"Invalid override path: {path}")
		if parts[0] not in raw:
			raise SchemaError(f"Unknown override root: {parts[0]}")
		target: dict[str, Any] = raw
		for index, part in enumerate(parts[:-1]):
			next_target = target.get(part)
			if next_target is None:
				raise SchemaError(f"Override path does not exist: {'.'.join(parts[:index + 1])}")
			if not isinstance(next_target, dict):
				raise SchemaError(f"Override path is not an object: {path}")
			target = next_target
		target[parts[-1]] = value


def _is_yaml_value(value: Any) -> bool:
	if value is None or isinstance(value, (str, int, float, bool)):
		return True
	if isinstance(value, list):
		return all(_is_yaml_value(item) for item in value)
	if isinstance(value, dict):
		return all(isinstance(key, str) and _is_yaml_value(item) for key, item in value.items())
	return False
