"""Agent — 对话入口。

English: User-facing chat entry point that wraps execution, sessions, tools, and plugins.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import replace
from contextlib import AsyncExitStack
from typing import Any, AsyncIterator

from axc_agent_engine.core.context import ExecutionConfig, ExecutionContext, ExecutionServices
from axc_agent_engine.core.events import Event, EventType
from axc_agent_engine.core.executor import Executor
from axc_agent_engine.core.llm_caller import LLMCaller
from axc_agent_engine.core.plugin_manager import PluginManager
from axc_agent_engine.core.run_request import RunRequest
from axc_agent_engine.core.session import Session
from axc_agent_engine.core.session_manager import SessionManager
from axc_agent_engine.core.schema import RuntimeConfig
from axc_agent_engine.llm.provider import LLMProvider
from axc_agent_engine.plugins import agent_info_from_runtime, model_info_from_models
from axc_agent_engine.plugins.base import BasePlugin
from axc_agent_engine.runtime.concurrency import ExecutionLimiter, SessionExecutionGate
from axc_agent_engine.runtime.input import InputProvider, InputProviderResult, PassthroughInputProvider
from axc_agent_engine.tools.registry import ToolRegistry
from axc_agent_engine.workflow import (
	WorkflowResumePlan,
	WorkflowResumeRequest,
	WorkflowRuntime,
	create_workflow_runtime,
)

logger = logging.getLogger(__name__)


class ExecutionContextFactory:
	def __init__(self, agent: "Agent") -> None:
		self.agent = agent

	def create(
		self,
		request: RunRequest,
	) -> ExecutionContext:
		agent = self.agent
		config = ExecutionConfig(
			system_prompt=agent._system_prompt,
			max_rounds=agent._runtime.max_rounds,
			stream=request.stream,
			thinking=agent._runtime.thinking,
			parallel_tool_calls=agent._runtime.parallel_tool_calls,
			human_in_the_loop=agent._runtime.human_in_the_loop,
			stream_idle_timeout=request.options.stream_idle_timeout or agent._runtime.stream_idle_timeout,
			workspace=agent._runtime.workspace,
			step_timeout=agent._runtime.step_timeout,
			total_timeout=agent._runtime.total_timeout,
			allowed_capabilities=frozenset(agent._runtime.allowed_capabilities),
		)
		services = replace(agent._services)
		ctx = ExecutionContext(config=config, services=services, utility_model=agent._utility_model)
		model_info = model_info_from_models(agent._default_model, agent._fallback_model, agent._utility_model)
		routing_mode = agent._runtime.routing.mode if agent._runtime.routing else "auto"
		agent_info = agent_info_from_runtime(
			name=agent.name,
			description=agent.description,
			workspace=agent._runtime.workspace,
			session_id=request.session_id,
			routing_mode=routing_mode,
		)
		ctx.runtime.model_info = model_info
		ctx.runtime.agent_info = agent_info
		ctx.state.metadata["model"] = model_info.to_dict()
		ctx.state.metadata["agent"] = agent_info.to_dict()
		if agent._metadata:
			ctx.state.metadata.update(agent._metadata)
		if request.metadata:
			ctx.state.metadata.update(request.metadata)
			try:
				ctx.runtime.agent_call_depth = int(request.metadata.get("agent_call_depth", 0))
			except (TypeError, ValueError):
				ctx.runtime.agent_call_depth = 0
		ctx.state.metadata["agent_name"] = agent.name
		if request.session_id:
			ctx.state.metadata["session_id"] = request.session_id
		if request.llm_options:
			ctx.runtime.llm_options = dict(request.llm_options)
		ctx.runtime.approval_queue = request.options.approval_queue
		ctx.runtime.response_queue = request.options.response_queue
		return ctx


class ExecutorFactory:
	def __init__(self, agent: "Agent") -> None:
		self.agent = agent
		self.context_factory = ExecutionContextFactory(agent)

	def create(
		self,
		request: RunRequest,
	) -> Executor:
		agent = self.agent
		ctx = self.context_factory.create(request)
		pm = PluginManager(agent._plugins)
		llm_caller = LLMCaller(primary=agent._default_model, fallback=agent._fallback_model, plugin_manager=pm)
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
	"""Agent 实例 — 由 AgentTemplate.instantiate() 创建。

	English: Runtime Agent instance created by AgentTemplate.instantiate().
	"""

	def __init__(
		self,
		name: str,
		description: str,
		system_prompt: str,
		runtime: RuntimeConfig,
		plugins: list[BasePlugin],
		default_model: LLMProvider,
		fallback_model: LLMProvider | None,
		utility_model: LLMProvider | None = None,
		session_manager: SessionManager | None = None,
		registry: ToolRegistry | None = None,
		result_store: "object | None" = None,
		services: ExecutionServices | None = None,
		input_provider: InputProvider | None = None,
		engine_limiter: ExecutionLimiter | None = None,
		workflow_runtime: WorkflowRuntime | None = None,
		metadata: dict[str, Any] | None = None,
	) -> None:
		self.name = name
		self.description = description
		self._system_prompt = system_prompt
		self._runtime = runtime
		self._plugins = plugins
		self._default_model = default_model
		self._fallback_model = fallback_model
		self._utility_model = utility_model
		self._session_manager = session_manager or SessionManager()
		self._registry = registry or ToolRegistry()
		self._services = services or ExecutionServices(result_store=result_store)
		self._input_provider = input_provider or PassthroughInputProvider()
		self._workflow_runtime = workflow_runtime or create_workflow_runtime()
		self._metadata = dict(metadata or {})
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
			tools = plugin.get_tools()
			self._registry.register_many(tools)
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
		run_options: dict | None = None,
		metadata: dict | None = None,
	) -> str:
		"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
非流式对话。

		English: Run a non-streaming chat turn and return the final assistant text.
		"""
		return await self._execute(
			user_message=message,
			session_id=session_id,
			stream=False,
			llm_options=llm_options,
			run_options=run_options,
			metadata=metadata,
		)

	async def chat_with_messages(
		self,
		messages: list[dict],
		session_id: str = "",
		llm_options: dict | None = None,
		run_options: dict | None = None,
		metadata: dict | None = None,
	) -> str:
		"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
接受结构化消息列表的非流式对话。

		English: Run a non-streaming chat turn from a structured message list.
		"""
		user_msg = self._extract_last_user_message(messages)
		return await self._execute(
			user_message=user_msg,
			inject_messages=messages,
			session_id=session_id,
			stream=False,
			llm_options=llm_options,
			run_options=run_options,
			metadata=metadata,
		)

	async def stream(
		self,
		message: str,
		session_id: str = "",
		llm_options: dict | None = None,
		run_options: dict | None = None,
		metadata: dict | None = None,
	) -> AsyncIterator[Event]:
		"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
流式对话。

		English: Stream execution events for one chat turn.
		"""
		async for event in self._execute_stream(
			user_message=message,
			session_id=session_id,
			llm_options=llm_options,
			run_options=run_options,
			metadata=metadata,
		):
			yield event

	async def stream_with_messages(
		self,
		messages: list[dict],
		session_id: str = "",
		llm_options: dict | None = None,
		run_options: dict | None = None,
		metadata: dict | None = None,
	) -> AsyncIterator[Event]:
		"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
接受结构化消息列表的流式对话。

		English: Stream execution events from a structured message list.
		"""
		user_msg = self._extract_last_user_message(messages)
		async for event in self._execute_stream(
			user_message=user_msg,
			inject_messages=messages,
			session_id=session_id,
			llm_options=llm_options,
			run_options=run_options,
			metadata=metadata,
		):
			yield event

	async def resume(
		self,
		run_id: str,
		message: str = "",
		llm_options: dict | None = None,
		run_options: dict | None = None,
		metadata: dict | None = None,
	) -> str:
		"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
非流式恢复执行级 checkpoint。"""
		from axc_agent_engine.core.errors import ProviderError
		result = ""
		async for event in self.resume_stream(
			run_id,
			message=message,
			llm_options=llm_options,
			run_options=run_options,
			metadata=metadata,
		):
			if event.type == EventType.DONE:
				result = event.content
			elif event.type == EventType.ERROR:
				raise ProviderError(event.content)
		return result

	async def resume_stream(
		self,
		run_id: str,
		message: str = "",
		llm_options: dict | None = None,
		run_options: dict | None = None,
		metadata: dict | None = None,
	) -> AsyncIterator[Event]:
		"""English: Bilingual documentation follows.
	中文：以下为双语文档说明。
	通过 WorkflowRuntime 恢复 execution/round 或 POR checkpoint。"""
		self._validate_resume_metadata(run_id, metadata, run_options)
		request = WorkflowResumeRequest(
			run_id=run_id,
			message=message,
			handler=lambda plan: self._resume_from_workflow_plan(plan, message, llm_options, run_options, metadata),
			checkpoint_store=self._services.checkpoint_store,
			metadata=dict(metadata or {}),
		)
		async for event in self._workflow_runtime.resume(request):
			yield event

	async def _resume_from_workflow_plan(
		self,
		plan: WorkflowResumePlan,
		message: str = "",
		llm_options: dict | None = None,
		run_options: dict | None = None,
		metadata: dict | None = None,
	) -> AsyncIterator[Event]:
		"""Resume from a WorkflowRuntime-owned plan.
中文：此文档说明相关引擎组件的行为。"""
		if plan.kind == "missing":
			yield Event.error("CheckpointStore is required for resume")
			return
		run_id = plan.run_id
		session_id = plan.session_id
		stream = bool((run_options or {}).get("stream", True))
		async with await self._run_coordinator.slots(session_id):
			request_metadata = {"run_id": run_id, **dict(metadata or {})}
			request = RunRequest.create(
				user_message=message,
				session_id=session_id,
				stream=stream,
				llm_options=llm_options,
				run_options=run_options,
				metadata=request_metadata,
			)
			executor = self._create_executor(request)
			executor.load_resume_snapshot(run_id, plan.snapshot)
			executor._ctx.state.metadata.update(request.metadata)
			async for event in executor.run_stream(message):
				yield event
			if session_id:
				session = await self._session_manager.get_or_create(session_id)
				session.messages = executor.message_store.get_all()
				await self._session_manager.save(session_id)

	async def get_session(self, session_id: str) -> Session | None:
		return await self._session_manager.get(session_id)

	async def reset_session(self, session_id: str = "") -> None:
		"""English: This documentation describes the related engine component behavior.
中文：重置指定会话，空字符串清除所有"""
		if session_id:
			await self._session_manager.remove(session_id)
		else:
			await self._session_manager.clear()

	async def close(self) -> None:
		"""English: This documentation describes the related engine component behavior.
中文：并行释放所有插件资源，带超时。"""
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
		run_options: dict | None = None,
		metadata: dict | None = None,
	) -> str:
		"""English: This documentation describes the related engine component behavior.
中文：统一非流式执行"""
		from axc_agent_engine.core.errors import ProviderError
		result = ""
		async for event in self._execute_stream(
			user_message=user_message,
			session_id=session_id,
			inject_messages=inject_messages,
			stream=stream,
			llm_options=llm_options,
			run_options=run_options,
			metadata=metadata,
		):
			if event.type == EventType.DONE:
				result = event.content
			elif event.type == EventType.CANCELLED:
				from axc_agent_engine.core.errors import CancelledError
				raise CancelledError(event.content)
			elif event.type == EventType.ERROR:
				raise ProviderError(event.content)
		return result

	async def _execute_stream(
		self, user_message: str, session_id: str = "",
		inject_messages: list[dict] | None = None,
		stream: bool = True,
		llm_options: dict | None = None,
		run_options: dict | None = None,
		metadata: dict | None = None,
	) -> AsyncIterator[Event]:
		"""English: This documentation describes the related engine component behavior.
中文：统一流式执行。"""
		request = RunRequest.create(
			user_message=user_message,
			session_id=session_id,
			stream=stream,
			messages=inject_messages,
			llm_options=llm_options,
			run_options=run_options,
			metadata=metadata,
		)
		async with await self._run_coordinator.slots(request.session_id):
			executor = self._create_executor(request)
			raw_messages = (
				request.messages if request.messages is not None
				else [{"role": "user", "content": request.user_message}]
			)
			processed = await self._process_input(raw_messages, request.session_id)
			processed.messages = _normalize_multimodal_messages(processed.messages)
			effective_user_message = self._extract_last_user_message(processed.messages) or request.user_message
			if processed.artifacts:
				executor._ctx.state.metadata["input_artifacts"] = processed.artifacts
			if processed.metadata:
				executor._ctx.state.metadata["input_metadata"] = processed.metadata
			#English: Source note. 中文：恢复会话上下文
			if request.session_id:
				session = await self._session_manager.get_or_create(request.session_id)
				self._session_manager.restore_context(session, executor.message_store)
			#English: Bilingual note. 中文：所有入口都走 InputProvider，因此统一注入标准 messages。
			if processed.messages:
				executor.message_store.extend(processed.messages)
				executor.skip_user_init = True
				executor._ctx.add_image_tokens(processed.messages)
			async for event in executor.run_stream(effective_user_message):
				yield event
			#English: Source note. 中文：写回会话并持久化
			if request.session_id:
				session = await self._session_manager.get_or_create(request.session_id)
				session.messages = executor.message_store.get_all()
				await self._session_manager.save(request.session_id)

	def _create_executor(
		self,
		request: RunRequest | str = "",
		stream: bool = True,
		llm_options: dict | None = None,
		run_options: dict | None = None,
		metadata: dict | None = None,
	) -> "Executor":
		if not isinstance(request, RunRequest):
			request = RunRequest.create(
				user_message="",
				session_id=str(request or ""),
				stream=stream,
				llm_options=llm_options,
				run_options=run_options,
				metadata=metadata,
			)
		return self._executor_factory.create(request)

	@staticmethod
	def _validate_resume_metadata(run_id: str, metadata: dict | None, run_options: dict | None) -> None:
		metadata_run_id = (metadata or {}).get("run_id")
		option_run_id = (run_options or {}).get("run_id")
		if metadata_run_id and str(metadata_run_id) != run_id:
			raise ValueError("metadata.run_id conflicts with resume run_id")
		if option_run_id and str(option_run_id) != run_id:
			raise ValueError("run_options.run_id conflicts with resume run_id")

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
				return _content_to_text(msg.get("content", ""))
		return ""


def _content_to_text(content: Any) -> str:
	"""Extract a textual goal from string or OpenAI-compatible multimodal content.
中文：此文档说明相关引擎组件的行为。"""
	if isinstance(content, str):
		return content
	if isinstance(content, list):
		text_parts = []
		for part in content:
			if isinstance(part, dict) and part.get("type") == "text":
				text = part.get("text", "")
				if text:
					text_parts.append(str(text))
		return "\n".join(text_parts)
	return "" if content is None else str(content)


def _normalize_multimodal_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
	return [{**message, "content": _normalize_content_parts(message.get("content"))} for message in messages]


def _normalize_content_parts(content: Any) -> Any:
	if not isinstance(content, list):
		return content
	parts = []
	for part in content:
		if not isinstance(part, dict):
			raise TypeError("message content part must be an object")
		part_type = str(part.get("type") or "")
		if part_type == "text":
			parts.append({"type": "text", "text": str(part.get("text", ""))})
		elif part_type == "image_url":
			image = part.get("image_url")
			if not isinstance(image, dict) or not image.get("url"):
				raise ValueError("image_url content part requires image_url.url")
			parts.append({"type": "image_url", "image_url": dict(image)})
		elif part_type == "image_base64":
			data = str(part.get("data") or part.get("image_base64") or "")
			media_type = str(part.get("media_type") or "image/png")
			if not data:
				raise ValueError("image_base64 content part requires data")
			parts.append({"type": "image_url", "image_url": {"url": f"data:{media_type};base64,{data}"}})
		elif part_type == "file_ref":
			ref = str(part.get("ref") or part.get("file_ref") or "")
			if not ref:
				raise ValueError("file_ref content part requires ref")
			parts.append({"type": "file_ref", "file_ref": {"ref": ref, **dict(part.get("metadata", {}) or {})}})
		else:
			raise ValueError(f"unsupported message content part type: {part_type}")
	return parts
