"""Agent — 对话入口。

English: User-facing chat entry point that wraps execution, sessions, tools, and plugins.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator, AsyncIterator, Callable
from contextlib import aclosing, asynccontextmanager
from dataclasses import replace
from typing import Any
from uuid import uuid4

from axc_agent_engine.core.context import ExecutionConfig, ExecutionContext, ExecutionServices
from axc_agent_engine.core.errors import CancelledError, ProviderError
from axc_agent_engine.core.events import TERMINAL_EVENTS, Event, EventType
from axc_agent_engine.core.executor import Executor
from axc_agent_engine.core.input_messages import extract_last_user_message, normalize_multimodal_messages
from axc_agent_engine.core.llm_caller import LLMCaller
from axc_agent_engine.core.message_store import MessageStore
from axc_agent_engine.core.plugin_manager import PluginManager
from axc_agent_engine.core.run_context import checkpoint_owner, context_run_id, dict_or_empty
from axc_agent_engine.core.run_request import RunRequest
from axc_agent_engine.core.schema import RuntimeConfig
from axc_agent_engine.core.session import Session
from axc_agent_engine.core.session_manager import SessionManager
from axc_agent_engine.llm.provider import LLMProvider
from axc_agent_engine.plugins.base import BasePlugin
from axc_agent_engine.plugins.context import agent_info_from_runtime, model_info_from_models
from axc_agent_engine.plugins.loader import close_plugins
from axc_agent_engine.plugins.strategy_selection import select_strategy
from axc_agent_engine.runtime.checkpoint import ForkedRun
from axc_agent_engine.runtime.concurrency import ExecutionLimiter, SessionExecutionGate
from axc_agent_engine.runtime.inbox import receipt
from axc_agent_engine.runtime.input import InputProvider, InputProviderResult, PassthroughInputProvider
from axc_agent_engine.runtime.policy import CapabilityPolicyEvaluator
from axc_agent_engine.runtime.run_control import RunControlRegistry
from axc_agent_engine.runtime.tasks import managed_stream
from axc_agent_engine.tools.registry import ToolRegistry
from axc_agent_engine.workflow import (
	WorkflowResumePlan,
	WorkflowResumeRequest,
	WorkflowRuntime,
	create_workflow_runtime,
)
from axc_agent_engine.workflow.state import fork_checkpoint

logger = logging.getLogger(__name__)


async def _final_text(events: AsyncIterator[Event]) -> str:
	async with managed_stream(events):
		async for event in events:
			if event.type == EventType.DONE:
				return event.content
			if event.type == EventType.ERROR:
				raise ProviderError(event.content)
			if event.type == EventType.CANCELLED:
				raise CancelledError(event.content)
	raise RuntimeError("Execution ended without a terminal event")


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
		artifact_store: object | None = None,
		services: ExecutionServices | None = None,
		input_provider: InputProvider | None = None,
		engine_limiter: ExecutionLimiter | None = None,
		workflow_runtime: WorkflowRuntime | None = None,
		metadata: dict[str, Any] | None = None,
	) -> None:
		self.name = name
		self._registered_name = name
		self.description = description
		self._system_prompt = system_prompt
		self._runtime = runtime
		self._plugin_manager = PluginManager(plugins)
		self._strategy, self._strategy_reason = select_strategy(plugins, runtime.strategy, runtime.routing.mode)
		self._default_model = default_model
		self._fallback_model = fallback_model
		self._utility_model = utility_model
		self._session_manager = session_manager or SessionManager(name)
		self._registry = registry or ToolRegistry()
		self._services = services or ExecutionServices(artifact_store=artifact_store)
		if self._services.run_control is None:
			self._services = replace(self._services, run_control=RunControlRegistry())
		self._input_provider = input_provider or PassthroughInputProvider()
		self._workflow_runtime = workflow_runtime or create_workflow_runtime()
		self._metadata = dict_or_empty(metadata, "metadata")
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
		for plugin in plugins:
			tools = plugin.get_tools()
			self._registry.register_many(tools)
		self._registry.freeze()
		logger.info(f"Agent '{name}' loaded: {len(plugins)} plugins, {self._registry.count} tools")

	@property
	def registered_name(self) -> str:
		"""Stable name used for routing and stored state. 路由与持久化状态使用的固定注册名。"""
		return self._registered_name

	def inspect(self) -> dict[str, Any]:
		"""Describe the actual agent without executing a turn. 查看 Agent 的有效装配与当前工具。"""
		policy = self._services.policy_evaluator
		if policy is None:
			policy = CapabilityPolicyEvaluator(set(self._runtime.allowed_capabilities))
		return {
			"agent": self.name,
			"strategy": {
				"name": self._strategy.name,
				"reason": self._strategy_reason,
				"state_version": self._strategy.state_version,
				"settings": self._strategy.inspection_settings(),
			},
			"plugins": [plugin.name for plugin in self._plugin_manager.plugins],
			"allowed_capabilities": list(self._runtime.allowed_capabilities),
			"policy": type(policy).__name__,
			"tools": [
				{
					"name": tool.name,
					"capability": tool.capability,
					"capability_allowed": policy.allows_capability(tool.capability)
					if type(policy) is CapabilityPolicyEvaluator
					else None,
					"risk_level": tool.risk_level,
					"deferred": tool.deferred,
				}
				for tool in self._registry.get_all()
			],
		}

	@property
	def registry(self) -> ToolRegistry:
		return self._registry

	async def send_message(self, run_id: str, text: str, *, message_id: str, session_id: str) -> str:
		"""Submit user input to an active run. 将用户输入交给指定会话的活动运行。"""
		control = self._services.run_control
		token = control.token(run_id) if control else None
		if token is not None:
			for ctx in token.contexts:
				if ctx.runtime.agent_owner is self and ctx.state.metadata.get("session_id", "") == session_id:
					return await ctx.runtime.inbox.send(message_id, text)
			return "closed"
		store = self._services.checkpoint_store
		checkpoint = await store.latest(run_id) if store else None
		if checkpoint:
			meta = checkpoint.state.get("metadata", {})
			if meta.get("agent_name") == self.registered_name and meta.get("session_id", "") == session_id:
				inbox = checkpoint.state.get("inbox", {})
				return receipt(dict(inbox.get("entries", [])), inbox.get("consumed", 0), message_id, text, False)
		return "closed"

	async def chat(
		self,
		message: str,
		session_id: str = "",
		llm_options: dict | None = None,
		run_options: dict | None = None,
		metadata: dict | None = None,
	) -> str:
		"""Run a turn and return the final reply. 执行一轮对话并返回最终回复。"""
		return await _final_text(
			self._execute_stream(
				user_message=message,
				session_id=session_id,
				stream=False,
				llm_options=llm_options,
				run_options=run_options,
				metadata=metadata,
			)
		)

	async def chat_with_messages(
		self,
		messages: list[dict],
		session_id: str = "",
		llm_options: dict | None = None,
		run_options: dict | None = None,
		metadata: dict | None = None,
	) -> str:
		"""Run a turn from structured messages. 使用结构化消息执行对话。"""
		user_msg = extract_last_user_message(messages)
		return await _final_text(
			self._execute_stream(
				user_message=user_msg,
				inject_messages=messages,
				session_id=session_id,
				stream=False,
				llm_options=llm_options,
				run_options=run_options,
				metadata=metadata,
			)
		)

	def stream(
		self,
		message: str,
		session_id: str = "",
		llm_options: dict | None = None,
		run_options: dict | None = None,
		metadata: dict | None = None,
	) -> AsyncGenerator[Event, None]:
		"""Stream the events of one turn. 逐个返回一轮对话的事件。"""
		return self._execute_stream(
			user_message=message,
			session_id=session_id,
			llm_options=llm_options,
			run_options=run_options,
			metadata=metadata,
		)

	def stream_with_messages(
		self,
		messages: list[dict],
		session_id: str = "",
		llm_options: dict | None = None,
		run_options: dict | None = None,
		metadata: dict | None = None,
	) -> AsyncGenerator[Event, None]:
		"""Stream a turn from structured messages. 使用结构化消息执行流式对话。"""
		user_msg = extract_last_user_message(messages)
		return self._execute_stream(
			user_message=user_msg,
			inject_messages=messages,
			session_id=session_id,
			llm_options=llm_options,
			run_options=run_options,
			metadata=metadata,
		)

	async def fork_run(self, run_id: str, checkpoint_id: str, *, metadata: dict | None = None) -> ForkedRun:
		"""Create an independent run at an exact stored cut. 从指定的持久化切点创建独立运行。"""
		store = self._services.checkpoint_store
		if store is None:
			raise ValueError("CheckpointStore is required for fork")
		checkpoint = next((cp for cp in await store.list(run_id) if cp.id == checkpoint_id), None)
		if checkpoint is None:
			raise ValueError("Fork checkpoint not found in source run")
		state = checkpoint.state
		meta = state.get("metadata", {})
		caller = {**self._metadata, **dict_or_empty(metadata, "metadata")}
		owner = checkpoint_owner(caller)
		if meta.get("agent_name") != self.registered_name or owner != state.get("owner", {}):
			raise ValueError("Fork checkpoint belongs to another agent, tenant or user")
		if reason := checkpoint.fork_blocker:
			raise ValueError(reason)
		MessageStore.validate_history(state["messages"])
		continuation = self._strategy.restore_state(state)
		branch = ForkedRun(uuid4().hex, uuid4().hex)
		source = {**caller, **meta}
		target = {**source, "session_id": branch.session_id, "run_id": branch.run_id}
		scopes = self._plugin_manager.fork_scopes(state.get("plugin_scopes", {}), source, target, state["fork_state"])
		seed = fork_checkpoint(checkpoint, branch, scopes, continuation, self._strategy.state_version)
		with self._services.run_control.claim(branch.run_id):
			if await store.latest(branch.run_id) is not None:
				raise ValueError("Generated branch run ID already exists")
			await store.save(seed)
		return branch

	async def resume(
		self,
		run_id: str,
		message: str = "",
		llm_options: dict | None = None,
		run_options: dict | None = None,
		metadata: dict | None = None,
	) -> str:
		"""Resume a checkpoint and return the final reply. 从检查点恢复并返回最终回复。"""
		return await _final_text(self.resume_stream(run_id, message, llm_options, run_options, metadata))

	def resume_stream(
		self,
		run_id: str,
		message: str = "",
		llm_options: dict | None = None,
		run_options: dict | None = None,
		metadata: dict | None = None,
	) -> AsyncIterator[Event]:
		"""Stream events from a resumed workflow. 逐个返回恢复执行产生的事件。"""
		self._validate_resume_metadata(run_id, metadata, run_options)
		request = WorkflowResumeRequest(
			run_id=run_id,
			message=message,
			handler=lambda plan: self._resume_from_workflow_plan(plan, message, llm_options, run_options, metadata),
			checkpoint_store=self._services.checkpoint_store,
			metadata=dict_or_empty(metadata, "metadata"),
		)
		return self._claimed_stream(run_id, lambda: self._workflow_runtime.resume(request))

	async def _resume_from_workflow_plan(
		self,
		plan: WorkflowResumePlan,
		message: str = "",
		llm_options: dict | None = None,
		run_options: dict | None = None,
		metadata: dict | None = None,
	) -> AsyncIterator[Event]:
		"""Restore the selected run and its session. 还原工作流选定的运行和会话。"""
		if plan.kind in {"handler", "missing"} and not plan.snapshot:
			yield Event.error("CheckpointStore is required for resume")
			return
		owner = plan.snapshot.get("metadata", {}).get("agent_name")
		if owner and owner != self.registered_name:
			raise ValueError("Checkpoint belongs to another agent")
		run_id = plan.run_id
		session_id = plan.session_id
		async with self._slots(session_id):
			await self._check_resume_cut(plan)
			request_metadata = {"run_id": run_id, **dict_or_empty(metadata, "metadata")}
			request = RunRequest.create(
				user_message=message,
				session_id=session_id,
				llm_options=llm_options,
				run_options=run_options,
				metadata=request_metadata,
			)
			ctx = self._execution_context(request)
			executor = self._create_executor(ctx)
			replay = executor.load_resume_snapshot(run_id, plan.snapshot, message=message, resume_metadata=metadata)
			ctx.state.metadata.update(request.metadata, agent_name=self.registered_name, session_id=session_id)
			if replay is not None:
				event = replay
			else:
				if (seed := plan.snapshot.get("session_seed")) is not None:
					await self._plugin_manager.restore_session(ctx, seed)
				async with aclosing(executor.run_stream(message)) as events:
					async for event in events:
						if event.type in TERMINAL_EVENTS:
							break
						yield event
				await self._save_session(session_id, executor)
		yield event

	async def _check_resume_cut(self, plan: WorkflowResumePlan) -> None:
		store = self._services.checkpoint_store
		if store and (snapshot_id := plan.metadata.get("checkpoint_id")):
			current = await store.latest(plan.run_id)
			if (
				current is None
				or current.id != snapshot_id
				or current.sequence != plan.snapshot.get("sequence", current.sequence)
			):
				raise ValueError("Checkpoint changed while waiting for a slot. Retry resume")

	async def get_session(self, session_id: str) -> Session | None:
		return await self._session_manager.get(session_id)

	async def reset_session(self, session_id: str = "") -> None:
		"""Reset one session, or all sessions for an empty ID. 重置指定会话，空 ID 清除全部会话。"""
		async with self._session_gate.reset(session_id):
			for plugin in self._plugin_manager.plugins:
				await plugin.on_session_reset(self.registered_name, session_id)
			if session_id:
				await self._session_manager.remove(session_id)
			else:
				await self._session_manager.clear()

	@asynccontextmanager
	async def _slots(self, session_id: str) -> AsyncIterator[None]:
		async with self._engine_limiter.slot(), self._agent_limiter.slot(), self._session_gate.slot(session_id):
			yield

	async def close(self) -> None:
		await close_plugins(self._plugin_manager.plugins)

	def _execute_stream(
		self,
		user_message: str,
		session_id: str = "",
		inject_messages: list[dict] | None = None,
		stream: bool = True,
		llm_options: dict | None = None,
		run_options: dict | None = None,
		metadata: dict | None = None,
	) -> AsyncGenerator[Event, None]:
		"""Process input and write back the session before delivering the terminal event. 处理输入，在终止事件前写回会话。"""
		request = RunRequest.create(
			user_message=user_message,
			session_id=session_id,
			stream=stream,
			messages=inject_messages,
			llm_options=llm_options,
			run_options=run_options,
			metadata=metadata,
		)
		return self._claimed_stream(request.metadata["run_id"], lambda: self._execute_request(request))

	async def _claimed_stream(
		self, run_id: str, open_stream: Callable[[], AsyncIterator[Event]]
	) -> AsyncGenerator[Event, None]:
		with self._services.run_control.claim(run_id):
			async with managed_stream(open_stream()) as events:
				async for event in events:
					if event.type in TERMINAL_EVENTS:
						break
					yield event
				else:
					return
		yield event

	async def _execute_request(self, request: RunRequest) -> AsyncGenerator[Event, None]:
		async with self._slots(request.session_id):
			ctx = self._execution_context(request)
			executor = self._create_executor(ctx)
			raw_messages = (
				request.messages
				if request.messages is not None
				else [{"role": "user", "content": request.user_message}]
			)
			processed = await self._process_input(raw_messages, request.session_id)
			processed.messages = normalize_multimodal_messages(processed.messages)
			effective_user_message = extract_last_user_message(processed.messages) or request.user_message
			if processed.artifacts:
				ctx.state.metadata["input_artifacts"] = processed.artifacts
			if processed.metadata:
				ctx.state.metadata["input_metadata"] = processed.metadata
			if request.session_id:
				session = await self._session_manager.get_or_create(request.session_id)
				self._session_manager.restore_context(session, executor.message_store)
			if processed.messages:
				executor.message_store.extend(processed.messages)
				executor.skip_user_init = True
				ctx.add_image_tokens(processed.messages)
			async with aclosing(executor.run_stream(effective_user_message)) as events:
				async for event in events:
					if event.type in TERMINAL_EVENTS:
						break
					yield event
			await self._save_session(request.session_id, executor)
		yield event

	async def _save_session(self, session_id: str, executor: Executor) -> None:
		if session_id:
			session = await self._session_manager.get_or_create(session_id)
			session.messages = executor.message_store.get_all()
			await self._session_manager.save(session_id)

	def _execution_context(self, request: RunRequest) -> ExecutionContext:
		config = ExecutionConfig(
			system_prompt=self._system_prompt,
			max_rounds=self._runtime.max_rounds,
			stream=request.stream,
			thinking=self._runtime.thinking,
			parallel_tool_calls=self._runtime.parallel_tool_calls,
			max_parallel_tool_calls=self._runtime.max_parallel_tool_calls,
			human_in_the_loop=self._runtime.human_in_the_loop,
			stream_idle_timeout=request.options.stream_idle_timeout or self._runtime.stream_idle_timeout,
			workspace=self._runtime.workspace,
			step_timeout=self._runtime.step_timeout,
			total_timeout=self._runtime.total_timeout,
			allowed_capabilities=frozenset(self._runtime.allowed_capabilities),
		)
		services = replace(self._services)
		ctx = ExecutionContext(config=config, services=services, utility_model=self._utility_model)
		model_info = model_info_from_models(self._default_model, self._fallback_model, self._utility_model)
		routing_mode = self._runtime.routing.mode
		agent_info = agent_info_from_runtime(
			name=self.registered_name,
			description=self.description,
			workspace=self._runtime.workspace,
			session_id=request.session_id,
			routing_mode=routing_mode,
		)
		ctx.runtime.agent_owner = self
		ctx.runtime.model_info = model_info
		ctx.runtime.agent_info = agent_info
		ctx.state.metadata["model"] = model_info.to_dict()
		ctx.state.metadata["agent"] = agent_info.to_dict()
		if self._metadata:
			ctx.state.metadata.update(self._metadata)
		if request.metadata:
			ctx.state.metadata.update(request.metadata)
		depth = ctx.state.metadata.get("agent_call_depth", 0)
		if type(depth) is not int or depth < 0:
			raise ValueError("agent_call_depth must be a nonnegative integer")
		ctx.runtime.agent_call_depth = depth
		ctx.state.metadata["agent_name"] = self.registered_name
		ctx.state.metadata["session_id"] = request.session_id
		if request.llm_options:
			ctx.runtime.llm_options = dict(request.llm_options)
		ctx.runtime.approval_queue = request.options.approval_queue
		ctx.runtime.response_queue = request.options.response_queue
		return ctx

	def _create_executor(self, ctx: ExecutionContext) -> Executor:
		pm = self._plugin_manager
		llm_caller = LLMCaller(primary=self._default_model, fallback=self._fallback_model, plugin_manager=pm)
		return Executor(
			llm_caller=llm_caller,
			registry=self._registry,
			plugin_manager=pm,
			ctx=ctx,
			strategy=self._strategy,
		)

	@staticmethod
	def _validate_resume_metadata(run_id: str, metadata: dict | None, run_options: dict | None) -> None:
		metadata_run_id = context_run_id(dict_or_empty(metadata, "metadata"))
		option_run_id = context_run_id(dict_or_empty(run_options, "run_options"))
		if metadata_run_id and metadata_run_id != run_id:
			raise ValueError("metadata.run_id conflicts with resume run_id")
		if option_run_id and option_run_id != run_id:
			raise ValueError("run_options.run_id conflicts with resume run_id")

	async def _process_input(self, messages: list[dict[str, Any]], session_id: str) -> InputProviderResult:
		context = {
			"agent_name": self.registered_name,
			"session_id": session_id,
			"workspace": self._runtime.workspace,
		}
		return await self._input_provider.process(messages, context)
