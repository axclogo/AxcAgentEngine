"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
核心执行器 — 带 TransactionRouter 的 ReAct 主循环，支持 POR。

每轮流程：transform_messages → check_stop → LLM 调用 → 路由 → 工具执行 → post hooks。
对调用方输出实时 Event 流。"""
from __future__ import annotations

import time
import uuid
import asyncio
from typing import AsyncIterator

from axc_agent_engine.runtime.checkpoint import CheckpointStatus
from axc_agent_engine.core.errors import CancelledError
from axc_agent_engine.core.context import ExecutionContext
from axc_agent_engine.core.execution_run import CheckpointRecorder, ExecutionRunLifecycle, StreamLLMBridge
from axc_agent_engine.core.llm_caller import LLMCaller
from axc_agent_engine.core.message_store import MessageStore
from axc_agent_engine.core.plugin_manager import PluginManager
from axc_agent_engine.core.events import Event, EventType
from axc_agent_engine.core.react_kernel import ReActKernel
from axc_agent_engine.planning.planning_service import PlanningService
from axc_agent_engine.planning.router import TransactionRouter
from axc_agent_engine.tools.registry import ToolRegistry

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
		self._lifecycle = ExecutionRunLifecycle(plugin_manager, ctx)
		self._stream_bridge = StreamLLMBridge(llm_caller, ctx)
		self._router = TransactionRouter(mode=routing_mode)
		self._react_kernel = ReActKernel(
			llm_caller=llm_caller,
			registry=registry,
			plugin_manager=plugin_manager,
			ctx=ctx,
			messages=self._messages,
			save_checkpoint=self._save_checkpoint,
			enter_por=self._enter_por_mode,
			detect_plan=self._detect_plan_handoff,
		)
		self._run_id = ""
		self._restored_from_checkpoint = False
		self.skip_user_init: bool = False

	@property
	def message_store(self) -> MessageStore:
		return self._messages

	def load_resume_snapshot(
		self,
		run_id: str,
		snapshot: dict,
	) -> None:
		"""Load a WorkflowRuntime-provided execution snapshot.
中文：此文档说明相关引擎组件的行为。"""
		self._run_id = run_id
		self._ctx.state.metadata["run_id"] = run_id
		por_checkpoint = snapshot.get("por_checkpoint")
		if isinstance(por_checkpoint, dict):
			self._ctx.state.metadata["por_resume_checkpoint"] = por_checkpoint
			self._restored_from_checkpoint = True
			return
		metadata = snapshot.get("metadata", {})
		if isinstance(metadata, dict):
			for key, value in metadata.items():
				if value not in ("", None):
					self._ctx.state.metadata[key] = value
		self._ctx.state.current_round = int(snapshot.get("current_round", 0))
		self._ctx.state.total_input_tokens = int(snapshot.get("input_tokens", 0))
		self._ctx.state.total_output_tokens = int(snapshot.get("output_tokens", 0))
		messages = snapshot.get("messages", [])
		if isinstance(messages, list):
			self._messages.set_all(messages)
		self._restored_from_checkpoint = True

	async def run_stream(self, user_message: str) -> AsyncIterator[Event]:
		"""English: This documentation describes the related engine component behavior.
中文：流式执行入口。"""
		self._run_id = self._ctx.state.metadata.get("run_id") or uuid.uuid4().hex[:16]
		self._ctx.state.metadata["run_id"] = self._run_id
		run_control = self._ctx.services.run_control
		task = asyncio.current_task()
		if run_control:
			run_control.register(self._run_id, self._ctx, task)
		self._react_kernel.start_time = time.time()
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
					usage = _usage_metadata(self._ctx)
					await self._save_checkpoint("execution", CheckpointStatus.COMPLETED, {"phase": "done", "result": result, "usage": usage})
					yield Event.done(result, {"usage": usage})
				elif event.type == EventType.ERROR:
					error = event.content
					await self._save_checkpoint("execution", CheckpointStatus.FAILED, {"phase": "error", "error": error})
					yield event
				elif event.type == EventType.CANCELLED:
					error = event.content
					await self._save_checkpoint("execution", CheckpointStatus.INTERRUPTED, {"phase": "cancelled", "error": error})
					yield event
				else:
					yield event
		except asyncio.CancelledError as e:
			self._ctx.cancel(str(e) or self._ctx.state.cancel_reason or "cancelled")
			error = self._ctx.state.cancel_reason or "cancelled"
			await self._save_checkpoint("execution", CheckpointStatus.INTERRUPTED, {"phase": "cancelled", "error": error})
			yield Event.cancelled(error, {"usage": _usage_metadata(self._ctx)})
		except CancelledError as e:
			error = str(e) or self._ctx.state.cancel_reason or "cancelled"
			await self._save_checkpoint("execution", CheckpointStatus.INTERRUPTED, {"phase": "cancelled", "error": error})
			yield Event.cancelled(error, {"usage": _usage_metadata(self._ctx)})
		except Exception as e:
			error = str(e)
			await self._save_checkpoint("execution", CheckpointStatus.FAILED, {"phase": "exception", "error": error})
			await self._lifecycle.error(e)
			raise
		finally:
			await self._lifecycle.end(result, error)
			if run_control:
				run_control.unregister(self._run_id, self._ctx, task)

	async def _react_loop(self, user_message: str) -> AsyncIterator[Event]:
		por_checkpoint = self._ctx.state.metadata.pop("por_resume_checkpoint", None)
		if isinstance(por_checkpoint, dict):
			async for event in self._enter_por_resume_mode(por_checkpoint, user_message):
				yield event
			return
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
		async for event in self._react_kernel.run(user_message, self._stream_llm_call):
			yield event

	async def _detect_plan_handoff(self, message: dict, user_message: str) -> tuple[bool, object | None, str]:
		decision = self._router.route(message)
		if decision.action != "por_plan":
			return False, None, ""
		route_plan = decision.plan
		if route_plan is None:
			try:
				route_plan = await PlanningService.generate_plan(self._llm, self._ctx, user_message)
			except Exception as e:
				await self._pm.on_error(self._ctx, e)
				return False, None, str(e)
		if not route_plan.steps:
			return False, None, "PlanningService returned an empty plan"
		return True, route_plan, ""

	async def _save_checkpoint(self, kind: str, status: CheckpointStatus, extra_state: dict | None = None) -> None:
		"""Persist a best-effort checkpoint when a CheckpointStore is configured.
中文：此文档说明相关引擎组件的行为。"""
		await self._checkpoint_recorder.save(self._run_id, kind, status, extra_state)

	def _init_messages(self, user_message: str) -> None:
		"""English: This documentation describes the related engine component behavior.
中文：初始化消息列表。"""
		self._messages.init_system_prompt(self._ctx.config.system_prompt)
		extra_context = self._pm.collect_context(self._ctx)
		if extra_context:
			self._messages.upsert_plugin_context(extra_context)
		if not self.skip_user_init:
			user_msg = {"role": "user", "content": user_message}
			self._messages.append(user_msg)
			self._ctx.add_image_tokens([user_msg])

	async def _enter_por_mode(self, plan, user_message: str) -> AsyncIterator[Event]:
		"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
切换到 POR 执行模式。"""
		async for event in self._new_por_runner().run(plan, user_message):
			yield event

	async def _enter_por_resume_mode(self, checkpoint_state: dict, user_message: str) -> AsyncIterator[Event]:
		async for event in self._new_por_runner().run_from_checkpoint_state(
			checkpoint_state,
			user_message,
			run_id=self._run_id,
		):
			yield event

	def _new_por_runner(self):
		from axc_agent_engine.planning.por_runner import PORRunner
		from axc_agent_engine.planning.runtime import PlanRuntime
		runtime = PlanRuntime(
			llm_caller=self._llm, message_store=self._messages,
			registry=self._registry, plugin_manager=self._pm, ctx=self._ctx,
		)
		return PORRunner(runtime=runtime)

	async def _stream_llm_call(
		self, messages: list[dict], tools_schema: list[dict] | None,
	) -> AsyncIterator[Event | tuple[dict, list[Event]]]:
		"""Run LLM call through the explicit stream bridge.
中文：此文档说明相关引擎组件的行为。"""
		async for item in self._stream_bridge.call(messages, tools_schema):
			yield item


def _usage_metadata(ctx: ExecutionContext) -> dict[str, int]:
	return {
		"input_tokens": ctx.state.total_input_tokens,
		"output_tokens": ctx.state.total_output_tokens,
		"total_tokens": ctx.state.total_input_tokens + ctx.state.total_output_tokens,
	}
