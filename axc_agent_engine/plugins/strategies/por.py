"""Official POR strategy and automatic handoff. 官方 POR 策略和自动路由。"""

from __future__ import annotations

from collections.abc import AsyncGenerator, AsyncIterator
from typing import TYPE_CHECKING

from axc_agent_engine.core.errors import LLMError
from axc_agent_engine.core.events import Event
from axc_agent_engine.planning.planning_service import PlanningService
from axc_agent_engine.planning.router import TransactionRouter
from axc_agent_engine.planning.runtime import DEFAULT_PARALLEL_STEPS, PlanRuntime
from axc_agent_engine.plugins.config_schema import config_field, config_schema
from axc_agent_engine.plugins.context import PluginContext
from axc_agent_engine.plugins.strategies.react import ReActLoop
from axc_agent_engine.plugins.strategy import ExecutionStrategyPlugin, StrategyRuntime
from axc_agent_engine.runtime.tasks import managed_stream

if TYPE_CHECKING:
	from axc_agent_engine.planning.planner import Plan
	from axc_agent_engine.planning.por_runner import PORRunner


class PORLoop:
	def __init__(self, runtime: StrategyRuntime, mode: str, max_parallel_steps: int) -> None:
		self.runtime = runtime
		self.router = TransactionRouter(mode)
		self.max_parallel_steps = max_parallel_steps
		self.react = ReActLoop(runtime, handoff=self._handoff)

	def _new_por_runner(self) -> PORRunner:
		from axc_agent_engine.planning.por_runner import PORRunner

		runtime = self.runtime
		return PORRunner(
			PlanRuntime(
				llm_caller=runtime._llm,
				message_store=runtime._messages,
				registry=runtime._registry,
				plugin_manager=runtime._plugins,
				ctx=runtime._ctx,
				checkpoint=runtime._checkpoint,
				max_parallel_steps=self.max_parallel_steps,
			)
		)

	def _enter_por_mode(self, plan: Plan, user_message: str) -> AsyncIterator[Event]:
		return self._new_por_runner().run(plan, user_message)

	async def run(self, message: str, state: dict | None) -> AsyncGenerator[Event, None]:
		events: AsyncIterator[Event]
		if state == {"phase": "react"}:
			events = self.react.run(message, None)
		elif state is not None:
			run_id = self.runtime._ctx.state.metadata["run_id"]
			events = self._new_por_runner().run_from_checkpoint_state(state, message, run_id=run_id)
		elif self.router.mode == "por_first":
			try:
				plan = await PlanningService.generate_plan(self.runtime._llm, self.runtime._ctx, message)
			except (LLMError, RuntimeError) as e:
				await self.runtime._plugins.on_error(self.runtime._ctx, e)
				yield Event.error(str(e))
				return
			events = self._enter_por_mode(plan, message)
		else:
			events = self.react.run(message, None)
		async with managed_stream(events):
			async for event in events:
				yield event

	def _handoff(self, message: dict, user_message: str) -> AsyncIterator[Event] | None:
		plan = self.router.route(message).plan
		return self._enter_por_mode(plan, user_message) if plan is not None else None


class PORPlugin(ExecutionStrategyPlugin):
	defer_resume_input = True
	name = "por"
	display_name = "POR"
	config_schema = config_schema(
		name,
		display_name,
		"先规划，再按步骤执行、观察与重规划。",
		[
			config_field(
				"mode",
				"路由模式",
				"string",
				"先规划或在模型输出计划时切换。",
				label_en="Routing mode",
				default="por_first",
				enum=["por_first", "auto"],
			),
			config_field(
				"max_parallel_steps",
				"最大并行步骤数",
				"integer",
				"一次最多启动多少个就绪步骤，设为 1 时串行执行。",
				label_en="Maximum parallel steps",
				default=DEFAULT_PARALLEL_STEPS,
			),
		],
	)

	def __init__(self, mode: str = "por_first") -> None:
		self.mode = mode
		self.max_parallel_steps = DEFAULT_PARALLEL_STEPS

	def initialize(self, config: dict, plugin_ctx: PluginContext) -> None:
		super().initialize(config, plugin_ctx)
		self.mode = config.get("mode", "por_first")
		limit = config.get("max_parallel_steps", DEFAULT_PARALLEL_STEPS)
		if type(limit) is not int or limit < 1:
			raise ValueError("por.max_parallel_steps must be a positive integer")
		self.max_parallel_steps = limit

	def inspection_settings(self) -> dict:
		return {"mode": self.mode, "max_parallel_steps": self.max_parallel_steps}

	def create_loop(self, runtime: StrategyRuntime) -> PORLoop:
		return PORLoop(runtime, self.mode, self.max_parallel_steps)
