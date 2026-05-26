"""Engine — Agent 生命周期管理。

English: Owns Agent lifecycle, shared services, provider resolution, and plugin initialization.
"""
from __future__ import annotations

import logging
from pathlib import Path

import yaml

from axc_agent_engine.agent import Agent
from axc_agent_engine.runtime.concurrency import ConcurrencyConfig, ExecutionLimiter
from axc_agent_engine.llm.config import LLMConfig
from axc_agent_engine.core.context import ExecutionServices
from axc_agent_engine.core.session_manager import SessionManager
from axc_agent_engine.core.errors import ConfigError, SchemaError
from axc_agent_engine.runtime.input import InputProvider, PassthroughInputProvider
from axc_agent_engine.llm.client import OpenAIClient
from axc_agent_engine.llm.provider import LLMProvider
from axc_agent_engine.llm.rate_limited import RateLimitedProvider
from axc_agent_engine.llm.registry import ProviderRegistry
from axc_agent_engine.plugins import PluginContext, agent_info_from_runtime, model_info_from_providers
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


class ProviderResolver:
	def __init__(self, registry: ProviderRegistry) -> None:
		self.registry = registry

	def make_client(self, llm: LLMConfig | LLMProvider | None) -> LLMProvider | None:
		if llm is None:
			return None
		if isinstance(llm, LLMProvider):
			return llm
		if not isinstance(llm, LLMConfig):
			raise ConfigError(f"Unsupported LLM provider type: {type(llm).__name__}")
		client: LLMProvider = OpenAIClient(llm)
		if llm.max_concurrent_requests > 0 or llm.requests_per_minute > 0:
			client = RateLimitedProvider(
				client,
				max_concurrent=llm.max_concurrent_requests,
				requests_per_minute=llm.requests_per_minute,
				queue_timeout=llm.rate_limit_queue_timeout,
			)
		return client

	def resolve(self, ref) -> LLMProvider | None:
		if ref is None:
			return None
		if isinstance(ref, str):
			provider = self.registry.get(ref)
			if provider is None:
				raise ConfigError(f"Provider '{ref}' not found in registry")
			return provider
		return self.make_client(ref)


class Engine:
	"""Agent 执行引擎。

	English: Agent execution engine used by hosts to load, run, and unload agents.
	"""

	def __init__(
		self,
		default_llm: LLMConfig | LLMProvider,
		fallback_llm: LLMConfig | LLMProvider | None = None,
		utility_llm: LLMConfig | LLMProvider | None = None,
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
		self._provider_registry = ProviderRegistry()
		self._provider_resolver = ProviderResolver(self._provider_registry)
		self._config_loader = AgentConfigLoader()
		self._default_client = self._make_client(default_llm)
		self._fallback_client = self._make_client(fallback_llm) if fallback_llm else None
		self._utility_client = self._make_client(utility_llm) if utility_llm else None
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
	def provider_registry(self) -> ProviderRegistry:
		"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
访问命名 provider 注册表，用于 per-agent LLM 配置。

		English: Access the named provider registry used by per-agent LLM configuration.
		"""
		return self._provider_registry

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

	def load_agent(self, yaml_path: str,
				 default_llm: "LLMConfig | LLMProvider | str | None" = None,
				 fallback_llm: "LLMConfig | LLMProvider | str | None" = None,
				 utility_llm: "LLMConfig | LLMProvider | str | None" = None) -> Agent:
		"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
加载 Agent YAML 并初始化插件。

		LLM 参数可以是 LLMConfig、LLMProvider 实例，或 ProviderRegistry 中的
		命名 provider 字符串。

		English: Load an Agent YAML file, resolve its LLM providers, initialize plugins,
		and register the resulting Agent with the engine.
		"""
		path, config, system_prompt = self._config_loader.load(yaml_path)
		# per-agent LLM 解析优先级：显式参数 > engine 默认 provider。
		#English: English: Per-agent LLM resolution prefers explicit arguments over engine defaults. 中文：源码说明。
		#English: Bilingual note. 中文：支持通过 ProviderRegistry 解析字符串名称。
		#English: English: String references are resolved through ProviderRegistry. 中文：源码说明。
		agent_default = self._resolve_provider(default_llm) or self._default_client
		agent_fallback = self._resolve_provider(fallback_llm) or self._fallback_client
		agent_utility = self._resolve_provider(utility_llm) or self._utility_client or agent_default
		# PluginContext 使用 Agent 级 provider，不使用 engine 全局 provider。
		#English: English: PluginContext receives agent-level providers, not global engine providers. 中文：源码说明。
		plugin_ctx = PluginContext(
			default_llm=agent_default,
			fallback_llm=agent_fallback,
			utility_llm=agent_utility,
			kv_store=self._kv_store,
			message_persistence=self._message_persistence,
			span_store=self._span_store,
			message_bus=self._message_bus,
			result_store=self._result_store,
			resources=self._resources,
			dispatcher=self._dispatcher,
			workspace=config.runtime.workspace,
			agent_getter=self.get_agent,
			agent_lister=self.list_agents,
			model_info=model_info_from_providers(agent_default, agent_fallback, agent_utility),
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
			default_client=agent_default,
			fallback_client=agent_fallback,
			utility_llm=agent_utility,
			session_manager=self._session_manager,
			registry=registry,
			services=self._execution_services,
			input_provider=self._input_provider,
			engine_limiter=self._engine_limiter,
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
		await self._default_client.close()
		if self._fallback_client:
			await self._fallback_client.close()
		if self._utility_client:
			await self._utility_client.close()
		await self._provider_registry.close_all()

	@staticmethod
	def _make_client(llm: LLMConfig | LLMProvider | None) -> LLMProvider | None:
		return ProviderResolver(ProviderRegistry()).make_client(llm)

	def _resolve_provider(self, ref) -> LLMProvider | None:
		"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
从字符串名称、LLMConfig、LLMProvider 或 None 解析 provider。"""
		return self._provider_resolver.resolve(ref)

	@staticmethod
	def _provider_tool_name_mapping(provider: LLMProvider) -> ToolNameMappingConfig | None:
		config = getattr(provider, "tool_name_mapping", None)
		return config if isinstance(config, ToolNameMappingConfig) else None
