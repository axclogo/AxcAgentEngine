"""Self-developed ReAct execution kernel.

This module intentionally has no dependency on POR graph or workflow runtime.
中文：此文档说明相关引擎组件的行为。"""
from __future__ import annotations

import time
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from axc_agent_engine.core.context import ExecutionContext
from axc_agent_engine.core.events import Event, EventType
from axc_agent_engine.core.llm_caller import LLMCaller
from axc_agent_engine.core.message_store import MessageStore
from axc_agent_engine.core.plugin_manager import PluginManager
from axc_agent_engine.core.react_loop import EventFilter, ReActTurnResult, ReActTurnRunner
from axc_agent_engine.runtime.checkpoint import CheckpointStatus
from axc_agent_engine.tools.registry import ToolRegistry


SaveCheckpoint = Callable[[str, CheckpointStatus, dict[str, Any] | None], Awaitable[None]]
EnterPOR = Callable[[Any, str], AsyncIterator[Event]]
DetectPlan = Callable[[dict[str, Any], str], Awaitable[tuple[bool, Any, str]]]


class ReActKernel:
	"""Owns the self-developed ReAct loop and delegates POR only through callbacks.
中文：此文档说明相关引擎组件的行为。"""

	def __init__(
		self,
		llm_caller: LLMCaller,
		registry: ToolRegistry,
		plugin_manager: PluginManager,
		ctx: ExecutionContext,
		messages: MessageStore,
		save_checkpoint: SaveCheckpoint | None = None,
		enter_por: EnterPOR | None = None,
		detect_plan: DetectPlan | None = None,
	) -> None:
		self._llm = llm_caller
		self._registry = registry
		self._pm = plugin_manager
		self._ctx = ctx
		self._messages = messages
		self._turn_runner = ReActTurnRunner(llm_caller, registry, plugin_manager, ctx, messages)
		self._save_checkpoint = save_checkpoint
		self._enter_por = enter_por
		self._detect_plan = detect_plan
		self.start_time = 0.0

	async def run(
		self,
		user_message: str,
		stream_llm_call: Callable[[list[dict], list[dict] | None], AsyncIterator[Event | tuple[dict, list[Event]]]],
	) -> AsyncIterator[Event]:
		"""Run the ReAct loop until final answer, POR handoff, or error.
中文：此文档说明相关引擎组件的行为。"""
		if self.start_time <= 0:
			self.start_time = time.time()
		while self._ctx.state.current_round < self._ctx.config.max_rounds:
			self._ctx.check_cancelled()
			if self._timed_out():
				yield Event(type=EventType.ERROR, content=f"总执行超时（{self._ctx.config.total_timeout}s）")
				return
			self._ctx.state.current_round += 1
			await self._checkpoint("round", CheckpointStatus.RUNNING, {"phase": "round_start"})
			stop, reason = self._pm.check_should_stop(self._ctx)
			if stop:
				yield Event.done(reason)
				return
			try:
				turn_result = None
				async for item in self._turn_runner.run(user_message, stream_llm_call=stream_llm_call):
					if isinstance(item, ReActTurnResult):
						turn_result = item
					else:
						yield item
			except Exception as e:
				await self._pm.on_error(self._ctx, e)
				yield Event.error(str(e))
				return
			if turn_result is None:
				yield Event.error("LLM call returned no result")
				return
			if self._detect_plan and self._enter_por:
				should_enter_por, plan, error = await self._detect_plan(turn_result.message, user_message)
				if error:
					yield Event.error(error)
					return
				if should_enter_por:
					async for event in self._enter_por(plan, user_message):
						yield event
					return
			if not turn_result.has_tool_calls:
				yield Event(type=EventType.STREAM_END, content=turn_result.content)
				await self._pm.on_round_end(self._ctx, user_message, turn_result.content, [])
				yield Event.done(turn_result.content)
				return
			await self._pm.on_round_end(self._ctx, user_message, turn_result.content, turn_result.parsed_calls)
			await self._checkpoint("round", CheckpointStatus.COMPLETED, {
				"phase": "round_end",
				"tool_calls": [call.get("name", "") for call in turn_result.parsed_calls],
			})
			stop, reason = self._pm.check_should_stop(self._ctx)
			if stop:
				yield Event.done(reason)
				return
		yield Event.error(f"Exceeded max rounds {self._ctx.config.max_rounds}")

	async def run_step(
		self,
		max_rounds: int,
		step_timeout: float = 0,
		emit_tool_events: bool = False,
		event_filter: EventFilter | None = None,
		increment_parent_round: bool = True,
	) -> AsyncIterator[Event | ReActTurnResult]:
		"""Run a bounded ReAct sub-loop for one POR step.
中文：此文档说明相关引擎组件的行为。"""
		start_time = time.time()
		for _ in range(max_rounds):
			self._ctx.check_cancelled()
			if step_timeout > 0 and time.time() - start_time > step_timeout:
				yield ReActTurnResult(message={}, content=f"步骤执行超时（{step_timeout}s）", failed=True)
				return
			if increment_parent_round:
				self._ctx.state.current_round += 1
			turn_result = None
			async for item in self._turn_runner.run(
				emit_tool_events=emit_tool_events,
				event_filter=event_filter,
			):
				if isinstance(item, ReActTurnResult):
					turn_result = item
				else:
					yield item
			if turn_result is None:
				yield ReActTurnResult(message={}, content="LLM 调用失败：没有结果", failed=True)
				return
			yield turn_result
			if not turn_result.has_tool_calls:
				return
		yield ReActTurnResult(message={}, content="步骤超过子循环轮次限制", failed=True)

	def _timed_out(self) -> bool:
		return self._ctx.config.total_timeout > 0 and time.time() - self.start_time > self._ctx.config.total_timeout

	async def _checkpoint(self, kind: str, status: CheckpointStatus, extra_state: dict[str, Any] | None = None) -> None:
		if self._save_checkpoint:
			await self._save_checkpoint(kind, status, extra_state)
