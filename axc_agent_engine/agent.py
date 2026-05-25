"""Agent — 对话入口。

English: User-facing chat entry point that wraps execution, sessions, tools, and plugins.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import replace
from contextlib import AsyncExitStack
from typing import Any, AsyncIterator

from axc_agent_engine.runtime.concurrency import ExecutionLimiter, SessionExecutionGate
from axc_agent_engine.core.context import ExecutionConfig, ExecutionContext, ExecutionServices
from axc_agent_engine.core.executor import Executor
from axc_agent_engine.core.llm_caller import LLMCaller
from axc_agent_engine.core.plugin_manager import PluginManager
from axc_agent_engine.core.session import Session
from axc_agent_engine.core.session_manager import SessionManager
from axc_agent_engine.core.events import Event, EventType
from axc_agent_engine.runtime.input import InputProvider, InputProviderResult, PassthroughInputProvider
from axc_agent_engine.llm.provider import LLMProvider
from axc_agent_engine.plugins.base import BasePlugin
from axc_agent_engine.plugins import agent_info_from_runtime, model_info_from_providers
from axc_agent_engine.core.schema import RuntimeConfig
from axc_agent_engine.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


class ExecutionContextFactory:
	def __init__(self, agent: "Agent") -> None:
		self.agent = agent

	def create(
		self,
		session_id: str = "",
		stream: bool = True,
		llm_options: dict | None = None,
		metadata: dict | None = None,
	) -> ExecutionContext:
		agent = self.agent
		config = ExecutionConfig(
			system_prompt=agent._system_prompt,
			max_rounds=agent._runtime.max_rounds,
			stream=stream,
			thinking=agent._runtime.thinking,
			parallel_tool_calls=agent._runtime.parallel_tool_calls,
			human_in_the_loop=agent._runtime.human_in_the_loop,
			workspace=agent._runtime.workspace,
			step_timeout=agent._runtime.step_timeout,
			total_timeout=agent._runtime.total_timeout,
			allowed_capabilities=frozenset(agent._runtime.allowed_capabilities),
		)
		services = replace(agent._services)
		ctx = ExecutionContext(config=config, services=services, utility_llm=agent._utility_llm)
		model_info = model_info_from_providers(agent._default_client, agent._fallback_client, agent._utility_llm)
		routing_mode = agent._runtime.routing.mode if agent._runtime.routing else "auto"
		agent_info = agent_info_from_runtime(
			name=agent.name,
			description=agent.description,
			workspace=agent._runtime.workspace,
			session_id=session_id,
			routing_mode=routing_mode,
		)
		ctx.runtime.model_info = model_info
		ctx.runtime.agent_info = agent_info
		ctx.state.metadata["model"] = model_info.to_dict()
		ctx.state.metadata["agent"] = agent_info.to_dict()
		if metadata:
			ctx.state.metadata.update(metadata)
			try:
				ctx.runtime.agent_call_depth = int(metadata.get("agent_call_depth", 0))
			except (TypeError, ValueError):
				ctx.runtime.agent_call_depth = 0
		ctx.state.metadata["agent_name"] = agent.name
		if session_id:
			ctx.state.metadata["session_id"] = session_id
		if llm_options:
			ctx.runtime.llm_options = llm_options
		return ctx


class ExecutorFactory:
	def __init__(self, agent: "Agent") -> None:
		self.agent = agent
		self.context_factory = ExecutionContextFactory(agent)

	def create(
		self,
		session_id: str = "",
		stream: bool = True,
		llm_options: dict | None = None,
		metadata: dict | None = None,
	) -> Executor:
		agent = self.agent
		ctx = self.context_factory.create(session_id, stream, llm_options, metadata)
		pm = PluginManager(agent._plugins)
		llm_caller = LLMCaller(primary=agent._default_client, fallback=agent._fallback_client, plugin_manager=pm)
		routing_mode = agent._runtime.routing.mode if agent._runtime.routing else "auto"
		return Executor(
			llm_caller=llm_caller,
			registry=agent._registry,
			plugin_manager=pm,
			ctx=ctx,
			routing_mode=routing_mode,
		)


class RunCoordinator:
	def __init__(self, agent: "Agent") -> None:
		self.agent = agent

	async def slots(self, session_id: str):
		stack = AsyncExitStack()
		await stack.enter_async_context(self.agent._engine_limiter.slot())
		await stack.enter_async_context(self.agent._agent_limiter.slot())
		await stack.enter_async_context(self.agent._session_gate.slot(session_id))
		return stack


class Agent:
	"""Agent 实例 — 由 Engine.load_agent() 创建。

	English: Runtime Agent instance created by Engine.load_agent().
	"""

	def __init__(
		self,
		name: str,
		description: str,
		system_prompt: str,
		runtime: RuntimeConfig,
		plugins: list[BasePlugin],
		default_client: LLMProvider,
		fallback_client: LLMProvider | None,
		utility_llm: LLMProvider | None = None,
		session_manager: SessionManager | None = None,
		registry: ToolRegistry | None = None,
		result_store: "object | None" = None,
		services: ExecutionServices | None = None,
		input_provider: InputProvider | None = None,
		engine_limiter: ExecutionLimiter | None = None,
	) -> None:
		self.name = name
		self.description = description
		self._system_prompt = system_prompt
		self._runtime = runtime
		self._plugins = plugins
		self._default_client = default_client
		self._fallback_client = fallback_client
		self._utility_llm = utility_llm
		self._session_manager = session_manager or SessionManager()
		self._registry = registry or ToolRegistry()
		self._services = services or ExecutionServices(result_store=result_store)
		self._input_provider = input_provider or PassthroughInputProvider()
		queue_timeout = self._runtime.concurrency.queue_timeout
		self._engine_limiter = engine_limiter or ExecutionLimiter()
		self._agent_limiter = ExecutionLimiter(
			self._runtime.concurrency.max_agent_concurrent_runs,
			queue_timeout,
			name=f"agent:{name}",
		)
		self._session_gate = SessionExecutionGate(
			self._runtime.concurrency.max_session_concurrent_runs,
			queue_timeout,
		)
		self._executor_factory = ExecutorFactory(self)
		self._run_coordinator = RunCoordinator(self)
		for plugin in plugins:
			try:
				tools = plugin.get_tools()
				self._registry.register_many(tools)
			except Exception as e:
				logger.warning(f"Plugin {plugin.name} get_tools error: {e}")
		self._registry.freeze()
		logger.info(f"Agent '{name}' loaded: {len(plugins)} plugins, {self._registry.count} tools")

	@property
	def registry(self) -> ToolRegistry:
		return self._registry

	async def chat(
		self,
		message: str,
		session_id: str = "",
		llm_options: dict | None = None,
		metadata: dict | None = None,
	) -> str:
		"""非流式对话。

		English: Run a non-streaming chat turn and return the final assistant text.
		"""
		return await self._execute(
			user_message=message,
			session_id=session_id,
			stream=False,
			llm_options=llm_options,
			metadata=metadata,
		)

	async def chat_with_messages(self, messages: list[dict], session_id: str = "", llm_options: dict | None = None) -> str:
		"""接受结构化消息列表的非流式对话。

		English: Run a non-streaming chat turn from a structured message list.
		"""
		user_msg = self._extract_last_user_message(messages)
		return await self._execute(user_message=user_msg, inject_messages=messages, session_id=session_id, stream=False, llm_options=llm_options)

	async def stream(self, message: str, session_id: str = "", llm_options: dict | None = None) -> AsyncIterator[Event]:
		"""流式对话。

		English: Stream execution events for one chat turn.
		"""
		async for event in self._execute_stream(user_message=message, session_id=session_id, llm_options=llm_options):
			yield event

	async def stream_with_messages(self, messages: list[dict], session_id: str = "", llm_options: dict | None = None) -> AsyncIterator[Event]:
		"""接受结构化消息列表的流式对话。

		English: Stream execution events from a structured message list.
		"""
		user_msg = self._extract_last_user_message(messages)
		async for event in self._execute_stream(user_message=user_msg, inject_messages=messages, session_id=session_id, llm_options=llm_options):
			yield event

	async def resume(self, run_id: str, message: str = "", llm_options: dict | None = None) -> str:
		"""非流式恢复执行级 checkpoint。"""
		from axc_agent_engine.core.errors import ProviderError
		result = ""
		async for event in self.resume_stream(run_id, message=message, llm_options=llm_options):
			if event.type == EventType.DONE:
				result = event.content
			elif event.type == EventType.ERROR:
				raise ProviderError(event.content)
		return result

	async def resume_stream(self, run_id: str, message: str = "", llm_options: dict | None = None) -> AsyncIterator[Event]:
		"""从 CheckpointStore 中恢复 execution/round 或 POR checkpoint。"""
		if not self._services.checkpoint_store:
			yield Event.error("CheckpointStore is required for resume")
			return
		checkpoint = await self._services.checkpoint_store.latest(run_id)
		if not checkpoint:
			yield Event.error(f"No checkpoint found for run_id={run_id}")
			return
		session_id = _session_id_from_checkpoint(checkpoint)
		stream = bool((llm_options or {}).get("stream", True))
		async with await self._run_coordinator.slots(session_id):
			executor = self._create_executor(session_id=session_id, stream=stream, llm_options=llm_options)
			if checkpoint.kind == "por":
				executor._ctx.state.metadata["run_id"] = run_id
				async for event in executor.resume_por(run_id, message):
					yield event
				return
			executor.restore_checkpoint(checkpoint)
			async for event in executor.run_stream(message):
				yield event
			if session_id:
				session = await self._session_manager.get_or_create(session_id)
				session.messages = executor.message_store.get_all()
				await self._session_manager.save(session_id)

	async def get_session(self, session_id: str) -> Session | None:
		return await self._session_manager.get(session_id)

	async def reset_session(self, session_id: str = "") -> None:
		"""重置指定会话，空字符串清除所有"""
		if session_id:
			await self._session_manager.remove(session_id)
		else:
			await self._session_manager.clear()

	async def close(self) -> None:
		"""并行释放所有插件资源，带超时。"""
		async def _close_plugin(plugin: BasePlugin) -> None:
			try:
				await asyncio.wait_for(plugin.close(), timeout=5.0)
			except asyncio.TimeoutError:
				logger.warning(f"Plugin {plugin.name} close timed out")
			except Exception as e:
				logger.warning(f"Plugin {plugin.name} close error: {e}")
		await asyncio.gather(*[_close_plugin(p) for p in self._plugins])

	async def _execute(
		self, user_message: str, session_id: str = "",
		inject_messages: list[dict] | None = None, stream: bool = False,
		llm_options: dict | None = None,
		metadata: dict | None = None,
	) -> str:
		"""统一非流式执行"""
		from axc_agent_engine.core.errors import ProviderError
		result = ""
		async for event in self._execute_stream(
			user_message=user_message,
			session_id=session_id,
			inject_messages=inject_messages,
			stream=stream,
			llm_options=llm_options,
			metadata=metadata,
		):
			if event.type == EventType.DONE:
				result = event.content
			elif event.type == EventType.ERROR:
				raise ProviderError(event.content)
		return result

	async def _execute_stream(
		self, user_message: str, session_id: str = "",
		inject_messages: list[dict] | None = None,
		stream: bool = True,
		llm_options: dict | None = None,
		metadata: dict | None = None,
	) -> AsyncIterator[Event]:
		"""统一流式执行。"""
		async with await self._run_coordinator.slots(session_id):
			executor = self._create_executor(session_id, stream=stream, llm_options=llm_options, metadata=metadata)
			raw_messages = inject_messages if inject_messages is not None else [{"role": "user", "content": user_message}]
			processed = await self._process_input(raw_messages, session_id)
			effective_user_message = self._extract_last_user_message(processed.messages) or user_message
			if processed.artifacts:
				executor._ctx.state.metadata["input_artifacts"] = processed.artifacts
			if processed.metadata:
				executor._ctx.state.metadata["input_metadata"] = processed.metadata
			# 恢复会话上下文
			if session_id:
				session = await self._session_manager.get_or_create(session_id)
				self._session_manager.restore_context(session, executor.message_store)
			# 所有入口都走 InputProvider，因此统一注入标准 messages。
			if processed.messages:
				executor.message_store.extend(processed.messages)
				executor.skip_user_init = True
				executor._ctx.add_image_tokens(processed.messages)
			async for event in executor.run_stream(effective_user_message):
				yield event
			# 写回会话并持久化
			if session_id:
				session = await self._session_manager.get_or_create(session_id)
				session.messages = executor.message_store.get_all()
				await self._session_manager.save(session_id)

	def _create_executor(
		self,
		session_id: str = "",
		stream: bool = True,
		llm_options: dict | None = None,
		metadata: dict | None = None,
	) -> "Executor":
		return self._executor_factory.create(session_id, stream, llm_options, metadata)

	async def _process_input(self, messages: list[dict[str, Any]], session_id: str) -> InputProviderResult:
		context = {
			"agent_name": self.name,
			"session_id": session_id,
			"workspace": self._runtime.workspace,
		}
		return await self._input_provider.process(messages, context)

	@staticmethod
	def _extract_last_user_message(messages: list[dict]) -> str:
		for msg in reversed(messages):
			if msg.get("role") == "user":
				return msg.get("content", "")
		return ""


def _session_id_from_checkpoint(checkpoint: Any) -> str:
	metadata = checkpoint.state.get("metadata", {}) if isinstance(checkpoint.state, dict) else {}
	if isinstance(metadata, dict) and metadata.get("session_id"):
		return str(metadata["session_id"])
	if checkpoint.metadata.get("session_id"):
		return str(checkpoint.metadata["session_id"])
	return ""
