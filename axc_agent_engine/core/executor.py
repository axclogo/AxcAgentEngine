"""核心执行器 — 带 TransactionRouter 的 ReAct 主循环，支持 POR。

每轮流程：transform_messages → check_stop → LLM 调用 → 路由 → 工具执行 → post hooks。
对调用方输出实时 Event 流。
"""
from __future__ import annotations

import logging
import time
import uuid
from typing import AsyncIterator

from axc_agent_engine.runtime.checkpoint import Checkpoint, CheckpointStatus
from axc_agent_engine.core.context import ExecutionContext
from axc_agent_engine.core.llm_caller import LLMCaller
from axc_agent_engine.core.message_store import MessageStore
from axc_agent_engine.core.plugin_manager import PluginManager
from axc_agent_engine.core.errors import ProviderError
from axc_agent_engine.core.events import Event, EventType
from axc_agent_engine.core.react_loop import ReActTurnResult, ReActTurnRunner
from axc_agent_engine.planning.planning_service import PlanningService
from axc_agent_engine.planning.router import TransactionRouter
from axc_agent_engine.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


class CheckpointRecorder:
	"""Best-effort execution checkpoint persistence."""

	def __init__(self, ctx: ExecutionContext, messages: MessageStore) -> None:
		self._ctx = ctx
		self._messages = messages

	async def save(
		self,
		run_id: str,
		kind: str,
		status: CheckpointStatus,
		extra_state: dict | None = None,
	) -> None:
		store = self._ctx.services.checkpoint_store
		if not store:
			return
		state = {
			"current_round": self._ctx.state.current_round,
			"messages": self._messages.get_all(),
			"input_tokens": self._ctx.state.total_input_tokens,
			"output_tokens": self._ctx.state.total_output_tokens,
			"metadata": {
				"run_id": run_id,
				"session_id": self._ctx.state.metadata.get("session_id", ""),
				"agent_name": self._ctx.state.metadata.get("agent_name", ""),
			},
		}
		if extra_state:
			state.update(extra_state)
		try:
			await store.save(Checkpoint(
				run_id=run_id,
				sequence=len(self._messages.get_all()) + self._ctx.state.current_round,
				status=status,
				kind=kind,
				state=state,
			))
		except Exception as e:
			logger.warning(f"Checkpoint save error: {e}")


class ExecutionRunLifecycle:
	"""Plugin-facing lifecycle hooks for an executor run."""

	def __init__(self, plugin_manager: PluginManager, ctx: ExecutionContext) -> None:
		self._pm = plugin_manager
		self._ctx = ctx

	async def start(self) -> None:
		await self._pm.on_execution_start(self._ctx)

	async def complete(self, content: str) -> str:
		trace = {
			"input_tokens": self._ctx.state.total_input_tokens,
			"output_tokens": self._ctx.state.total_output_tokens,
			"rounds": self._ctx.state.current_round,
		}
		return await self._pm.on_execution_complete(self._ctx, content, trace)

	async def error(self, error: Exception) -> None:
		await self._pm.on_error(self._ctx, error)

	async def end(self, result: str, error: str) -> None:
		await self._pm.on_execution_end(self._ctx, result, error)


class ReActRoundRunner:
	"""Runs the ReAct/POR decision loop while Executor owns public entrypoints."""

	def __init__(self, executor: "Executor") -> None:
		self._executor = executor

	async def run(self, user_message: str) -> AsyncIterator[Event]:
		async for event in self._executor._run_react_loop(user_message):
			yield event


class Executor:
	"""ReAct 执行器，使用 TransactionRouter 切换 POR 模式。"""

	def __init__(
		self,
		llm_caller: LLMCaller,
		registry: ToolRegistry,
		plugin_manager: PluginManager,
		ctx: ExecutionContext,
		routing_mode: str = "auto",
	) -> None:
		self._llm = llm_caller
		self._registry = registry
		self._pm = plugin_manager
		self._ctx = ctx
		self._messages = MessageStore()
		self._checkpoint_recorder = CheckpointRecorder(ctx, self._messages)
		self._turn_runner = ReActTurnRunner(llm_caller, registry, plugin_manager, ctx, self._messages)
		self._lifecycle = ExecutionRunLifecycle(plugin_manager, ctx)
		self._round_runner = ReActRoundRunner(self)
		self._router = TransactionRouter(mode=routing_mode)
		self._run_id = ""
		self._restored_from_checkpoint = False
		self.skip_user_init: bool = False

	@property
	def message_store(self) -> MessageStore:
		return self._messages

	def restore_checkpoint(self, checkpoint: Checkpoint) -> None:
		"""Restore executor message store and counters from a checkpoint.

		This prepares the executor for explicit resume flows. It does not start
		execution by itself and intentionally avoids calling plugin hooks.
		"""
		self._run_id = checkpoint.run_id
		self._ctx.state.metadata["run_id"] = checkpoint.run_id
		metadata = checkpoint.state.get("metadata", {})
		if isinstance(metadata, dict):
			for key, value in metadata.items():
				if value not in ("", None):
					self._ctx.state.metadata[key] = value
		self._ctx.state.current_round = int(checkpoint.state.get("current_round", 0))
		self._ctx.state.total_input_tokens = int(checkpoint.state.get("input_tokens", 0))
		self._ctx.state.total_output_tokens = int(checkpoint.state.get("output_tokens", 0))
		messages = checkpoint.state.get("messages", [])
		if isinstance(messages, list):
			self._messages.set_all(messages)
		self._restored_from_checkpoint = True

	async def run_stream(self, user_message: str) -> AsyncIterator[Event]:
		"""流式执行入口。"""
		self._start_time = time.time()
		self._run_id = self._ctx.state.metadata.get("run_id") or uuid.uuid4().hex[:16]
		self._ctx.state.metadata["run_id"] = self._run_id
		if self._restored_from_checkpoint:
			self._restored_from_checkpoint = False
		else:
			self._ctx.state.current_round = 0
			self._init_messages(user_message)
		await self._lifecycle.start()
		await self._save_checkpoint("execution", CheckpointStatus.RUNNING, {"phase": "start"})
		result = ""
		error = ""
		try:
			async for event in self._react_loop(user_message):
				if event.type == EventType.DONE:
					result = await self._lifecycle.complete(event.content)
					await self._save_checkpoint("execution", CheckpointStatus.COMPLETED, {"phase": "done", "result": result})
					yield Event.done(result)
				elif event.type == EventType.ERROR:
					error = event.content
					await self._save_checkpoint("execution", CheckpointStatus.FAILED, {"phase": "error", "error": error})
					yield event
				else:
					yield event
		except Exception as e:
			error = str(e)
			await self._save_checkpoint("execution", CheckpointStatus.FAILED, {"phase": "exception", "error": error})
			await self._lifecycle.error(e)
			raise
		finally:
			await self._lifecycle.end(result, error)

	async def resume_por(self, run_id: str, user_message: str = "") -> AsyncIterator[Event]:
		"""Resume a POR checkpoint using the current executor services."""
		self._start_time = time.time()
		self._run_id = run_id
		self._ctx.state.metadata["run_id"] = run_id
		await self._lifecycle.start()
		result = ""
		error = ""
		try:
			from axc_agent_engine.planning.por_runner import PORRunner
			from axc_agent_engine.planning.runtime import PlanRuntime
			runtime = PlanRuntime(
				llm_caller=self._llm, message_store=self._messages,
				registry=self._registry, plugin_manager=self._pm, ctx=self._ctx,
			)
			runner = PORRunner(runtime=runtime)
			async for event in runner.resume(run_id, user_message):
				if event.type == EventType.DONE:
					result = await self._lifecycle.complete(event.content)
					await self._save_checkpoint("execution", CheckpointStatus.COMPLETED, {"phase": "por_resume_done", "result": result})
					yield Event.done(result)
				elif event.type == EventType.ERROR:
					error = event.content
					await self._save_checkpoint("execution", CheckpointStatus.FAILED, {"phase": "por_resume_error", "error": error})
					yield event
				else:
					yield event
		except Exception as e:
			error = str(e)
			await self._save_checkpoint("execution", CheckpointStatus.FAILED, {"phase": "por_resume_exception", "error": error})
			await self._lifecycle.error(e)
			raise
		finally:
			await self._lifecycle.end(result, error)

	async def _react_loop(self, user_message: str) -> AsyncIterator[Event]:
		async for event in self._round_runner.run(user_message):
			yield event

	async def _run_react_loop(self, user_message: str) -> AsyncIterator[Event]:
		"""ReAct 主循环。"""
		if self._router.mode == "por_first":
			try:
				plan = await PlanningService.generate_plan(self._llm, self._ctx, user_message)
			except Exception as e:
				await self._pm.on_error(self._ctx, e)
				yield Event.error(str(e))
				return
			if plan.steps:
				async for event in self._enter_por_mode(plan, user_message):
					yield event
				return
		while self._ctx.state.current_round < self._ctx.config.max_rounds:
			self._ctx.check_cancelled()
			if self._ctx.config.total_timeout > 0 and time.time() - self._start_time > self._ctx.config.total_timeout:
				yield Event(type=EventType.ERROR, content=f"Total execution timeout ({self._ctx.config.total_timeout}s)")
				return
			self._ctx.state.current_round += 1
			await self._save_checkpoint("round", CheckpointStatus.RUNNING, {"phase": "round_start"})
			stop, reason = self._pm.check_should_stop(self._ctx)
			if stop:
				yield Event.done(reason)
				return
			try:
				turn_result = None
				async for ev in self._turn_runner.run(user_message, stream_llm_call=self._stream_llm_call):
					if isinstance(ev, ReActTurnResult):
						turn_result = ev
					else:
						yield ev
			except Exception as e:
				await self._pm.on_error(self._ctx, e)
				yield Event.error(str(e))
				return
			if turn_result is None:
				yield Event.error("LLM call returned no result")
				return
			message = turn_result.message
			# 运行时路由：final_answer -> DONE，tool_calls -> ReAct，结构化计划 -> POR。
			decision = self._router.route(message)
			if decision.action == "por_plan":
				route_plan = decision.plan
				if route_plan is None:
					try:
						route_plan = await PlanningService.generate_plan(self._llm, self._ctx, user_message)
					except Exception as e:
						await self._pm.on_error(self._ctx, e)
						yield Event.error(str(e))
						return
				if not route_plan.steps:
					yield Event.error("PlanningService returned an empty plan")
					return
				async for event in self._enter_por_mode(route_plan, user_message):
					yield event
				return
			content = turn_result.content
			if not turn_result.has_tool_calls:
				yield Event(type=EventType.STREAM_END, content=content)
				await self._pm.on_round_end(self._ctx, user_message, content, [])
				yield Event.done(content)
				return
			parsed_calls = turn_result.parsed_calls
			await self._pm.on_round_end(self._ctx, user_message, content, parsed_calls)
			await self._save_checkpoint("round", CheckpointStatus.COMPLETED, {
				"phase": "round_end",
				"tool_calls": [call.get("name", "") for call in parsed_calls],
			})
			stop, reason = self._pm.check_should_stop(self._ctx)
			if stop:
				yield Event.done(reason)
				return
		yield Event.error(f"Exceeded max rounds {self._ctx.config.max_rounds}")

	async def _save_checkpoint(self, kind: str, status: CheckpointStatus, extra_state: dict | None = None) -> None:
		"""Persist a best-effort checkpoint when a CheckpointStore is configured."""
		await self._checkpoint_recorder.save(self._run_id, kind, status, extra_state)

	def _init_messages(self, user_message: str) -> None:
		"""初始化消息列表。"""
		self._messages.init_system_prompt(self._ctx.config.system_prompt)
		extra_context = self._pm.collect_context(self._ctx)
		if extra_context:
			self._messages.upsert_plugin_context(extra_context)
		if not self.skip_user_init:
			user_msg = {"role": "user", "content": user_message}
			self._messages.append(user_msg)
			self._ctx.add_image_tokens([user_msg])

	async def _enter_por_mode(self, plan, user_message: str) -> AsyncIterator[Event]:
		"""切换到 POR 执行模式。"""
		from axc_agent_engine.planning.por_runner import PORRunner
		from axc_agent_engine.planning.runtime import PlanRuntime
		runtime = PlanRuntime(
			llm_caller=self._llm, message_store=self._messages,
			registry=self._registry, plugin_manager=self._pm, ctx=self._ctx,
		)
		runner = PORRunner(runtime=runtime)
		async for event in runner.run(plan, user_message):
			yield event

	async def _stream_llm_call(
		self, messages: list[dict], tools_schema: list[dict] | None,
	) -> AsyncIterator[Event | tuple[dict, list[Event]]]:
		"""通过 asyncio.Queue 运行 LLM 调用并输出实时 delta。"""
		import asyncio
		queue: asyncio.Queue[Event | None] = asyncio.Queue(maxsize=1000)
		self._ctx.runtime.event_queue = queue
		done_marker = None
		llm_result: list = []

		async def _run_llm():
			try:
				msg, evts = await self._llm.call(self._ctx, messages, tools_schema)
				llm_result.append((msg, evts))
			except Exception as e:
				llm_result.append(e)
			finally:
				await queue.put(done_marker)

		task = asyncio.create_task(_run_llm())
		try:
			while True:
				event = await queue.get()
				if event is done_marker:
					break
				yield event
		finally:
			self._ctx.runtime.event_queue = None
			self._ctx.runtime.stream_delta_emitted = False
			if not task.done():
				task.cancel()
				try:
					await task
				except (asyncio.CancelledError, Exception):
					pass
		if not llm_result:
			raise ProviderError("LLM call produced no result")
		result = llm_result[0]
		if isinstance(result, Exception):
			raise result
		yield result
