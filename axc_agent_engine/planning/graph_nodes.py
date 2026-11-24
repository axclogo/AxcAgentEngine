"""pydantic-graph POR nodes.

The node functions delegate behavior to a service while graph edges own state
transitions and loop control.
由 pydantic-graph 驱动的 POR 节点。"""

from __future__ import annotations

from pydantic_graph import StepContext

from axc_agent_engine.planning.graph_state import PORGraphResult, PORGraphState


async def announce_plan(ctx: StepContext) -> None:
	await ctx.deps.announce_plan(ctx.state)


async def select_steps(ctx: StepContext) -> None:
	await ctx.deps.select_steps(ctx.state)


async def execute_step(ctx: StepContext) -> None:
	await ctx.deps.execute_step(ctx.state)


async def observe_step(ctx: StepContext) -> None:
	await ctx.deps.observe_step(ctx.state)


async def replan_step(ctx: StepContext) -> PORGraphState:
	await ctx.deps.replan_step(ctx.state)
	return ctx.state


async def finalize_plan(ctx: StepContext) -> PORGraphResult:
	return await ctx.deps.finalize_plan(ctx.state)
