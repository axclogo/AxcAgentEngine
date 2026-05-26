"""pydantic-graph runtime for Plan-Observe-Replan execution.
中文：此文档说明相关引擎组件的行为。"""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from pydantic_graph import GraphBuilder

from axc_agent_engine.core.events import Event, EventType
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


class PORGraphRuntime:
	"""Runs POR through pydantic-graph while delegating work to a service object.
中文：此文档说明相关引擎组件的行为。"""

	def __init__(self, service: Any) -> None:
		self._service = service
		self._graph = _build_graph()

	async def run(
		self,
		plan: Plan,
		user_message: str,
		initial_events: list[Event] | None = None,
		resumed: bool = False,
	) -> AsyncIterator[Event]:
		state = PORGraphState(
			plan=plan,
			user_message=user_message,
			events=list(initial_events or []),
			resumed=resumed,
		)
		try:
			result: PORGraphResult = await self._graph.run(state=state, deps=self._service)
		except Exception as e:
			yield Event(type=EventType.ERROR, content=str(e))
			return
		for event in result.events:
			yield event
		if result.error:
			yield Event(type=EventType.ERROR, content=result.error)


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
