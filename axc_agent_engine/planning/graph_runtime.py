"""pydantic-graph runtime for Plan-Observe-Replan execution. POR 的 pydantic-graph 运行时。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from typing import Any

from pydantic_graph import GraphBuilder

from axc_agent_engine.core.errors import LLMError
from axc_agent_engine.core.events import TERMINAL_EVENTS, Event
from axc_agent_engine.planning.graph_nodes import (
	announce_plan,
	execute_step,
	finalize_plan,
	observe_step,
	replan_step,
	select_steps,
)
from axc_agent_engine.planning.graph_state import PORGraphResult, PORGraphState
from axc_agent_engine.planning.planner import Plan
from axc_agent_engine.runtime.tasks import cancel_and_wait


class PORGraphRuntime:
	"""Runs POR through pydantic-graph while delegating work to a service object. 通过 pydantic-graph 驱动节点并将具体操作委托给执行服务。"""

	def __init__(self, service: Any) -> None:
		self._service = service
		self._graph = _build_graph()

	async def run(
		self,
		plan: Plan,
		user_message: str,
		initial_events: list[Event] | None = None,
		resume_state: dict | None = None,
	) -> AsyncGenerator[Event, None]:
		queue: asyncio.Queue[Event | None] = asyncio.Queue()
		terminal = None

		def emit(event: Event) -> None:
			nonlocal terminal
			if event.type in TERMINAL_EVENTS:
				terminal = event
			else:
				queue.put_nowait(event)

		state = PORGraphState(plan=plan, user_message=user_message, emit=emit, resume_state=resume_state)
		for initial in initial_events or []:
			queue.put_nowait(initial)

		async def drive() -> None:
			try:
				result = await self._graph.run(state=state, deps=self._service)
				if result.error:
					queue.put_nowait(Event.error(result.error))
				elif terminal is not None:
					queue.put_nowait(terminal)
			except (ExceptionGroup, RuntimeError, ValueError, LLMError) as exc:
				queue.put_nowait(Event.error(str(exc)))
			finally:
				queue.put_nowait(None)

		task = asyncio.create_task(drive())
		try:
			while (event := await queue.get()) is not None:
				yield event
			await task
		finally:
			await cancel_and_wait(task)


def _build_graph():
	builder = GraphBuilder(
		name="por_graph_runtime",
		state_type=PORGraphState,
		output_type=PORGraphResult,
	)
	announce = builder.step(announce_plan, node_id="announce_plan")
	select = builder.step(select_steps, node_id="select_steps")
	execute = builder.step(execute_step, node_id="execute_step")
	observe = builder.step(observe_step, node_id="observe_step")
	replan_node = builder.step(replan_step, node_id="replan_step")
	finalize = builder.step(finalize_plan, node_id="finalize_plan")
	route = builder.decision(node_id="route_after_replan")
	route = route.branch(builder.match(None, matches=lambda state: bool(state.should_continue)).to(select))
	route = route.branch(builder.match(None, matches=lambda state: not bool(state.should_continue)).to(finalize))
	builder.add(
		builder.edge_from(builder.start_node).to(announce),
		builder.edge_from(announce).to(select),
		builder.edge_from(select).to(execute),
		builder.edge_from(execute).to(observe),
		builder.edge_from(observe).to(replan_node),
		builder.edge_from(replan_node).to(route),
		builder.edge_from(finalize).to(builder.end_node),
	)
	return builder.build()
