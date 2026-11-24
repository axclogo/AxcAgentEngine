"""Self-developed ReAct execution kernel.

This module intentionally has no dependency on POR graph or workflow runtime.
引擎自带的 ReAct 执行循环。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from axc_agent_engine.core.context import ExecutionContext, ExecutionState
from axc_agent_engine.core.events import Event
from axc_agent_engine.core.llm_caller import LLMCaller
from axc_agent_engine.core.message_store import MessageStore
from axc_agent_engine.core.plugin_manager import PluginManager
from axc_agent_engine.core.react_loop import EventFilter, ReActTurnResult, ReActTurnRunner
from axc_agent_engine.runtime.tasks import managed_stream, stream_until
from axc_agent_engine.tools.registry import ToolRegistry


class ReActKernel:
	"""Owns the self-developed ReAct loop and delegates POR only through callbacks. 驱动 ReAct 循环，通过回调将计划移交 POR。"""

	def __init__(
		self,
		llm_caller: LLMCaller,
		registry: ToolRegistry,
		plugin_manager: PluginManager,
		ctx: ExecutionContext,
		messages: MessageStore,
	) -> None:
		self._ctx = ctx
		self._turn_runner = ReActTurnRunner(llm_caller, registry, plugin_manager, ctx, messages)

	async def run_step(
		self,
		max_rounds: int,
		step_timeout: float = 0,
		emit_tool_events: bool = False,
		event_filter: EventFilter | None = None,
		round_budget: ExecutionState | None = None,
	) -> AsyncIterator[Event | ReActTurnResult]:
		"""Run a bounded ReAct sub-loop for one POR step. 为一个 POR 步骤运行有轮次上限的 ReAct 子循环。"""
		budget = round_budget or self._ctx.state
		deadline = asyncio.get_running_loop().time() + step_timeout if step_timeout > 0 else None
		for _ in range(max_rounds):
			self._ctx.check_cancelled()
			if budget.current_round >= self._ctx.config.max_rounds:
				yield ReActTurnResult(message={}, content="步骤超过总轮次限制", failed=True)
				return
			budget.current_round += 1
			if budget is not self._ctx.state:
				self._ctx.state.current_round += 1
			turn_result: ReActTurnResult
			stream = self._turn_runner.run(
				emit_tool_events=emit_tool_events, event_filter=event_filter, event_sink=self._ctx.runtime.event_sink
			)
			expired = ReActTurnResult(message={}, content=f"步骤执行超时（{step_timeout}s）", failed=True)
			async with managed_stream(stream_until(stream, deadline, expired)) as turns:
				async for item in turns:
					if isinstance(item, ReActTurnResult):
						turn_result = item
					else:
						yield item
			yield turn_result
			if not turn_result.has_tool_calls:
				return
		yield ReActTurnResult(message={}, content="步骤超过子循环轮次限制", failed=True)
