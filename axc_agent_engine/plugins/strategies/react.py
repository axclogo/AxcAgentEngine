"""Official ReAct strategy. 官方 ReAct 执行策略。"""

from collections.abc import AsyncGenerator, AsyncIterator, Callable

from axc_agent_engine.core.errors import ExecutionTimeoutError, LLMError, MaxRoundsError
from axc_agent_engine.core.events import Event, EventType
from axc_agent_engine.plugins.config_schema import config_schema
from axc_agent_engine.plugins.strategy import ExecutionStrategyPlugin, ReActTurnResult, StrategyRuntime
from axc_agent_engine.runtime.tasks import managed_stream

Handoff = Callable[[dict, str], AsyncIterator[Event] | None]


class ReActLoop:
	def __init__(self, runtime: StrategyRuntime, handoff: Handoff | None = None) -> None:
		self.runtime = runtime
		self.handoff = handoff

	async def run(self, message: str, state: dict | None) -> AsyncGenerator[Event, None]:
		if state is not None:
			raise ValueError("ReAct requires a round checkpoint")
		runtime = self.runtime
		while True:
			try:
				runtime.begin_round()
			except (MaxRoundsError, ExecutionTimeoutError) as exc:
				yield Event.error(str(exc))
				return
			await runtime.checkpoint_round()
			if (reason := runtime.stop_reason()) is not None:
				yield Event.done(reason)
				return
			result: ReActTurnResult
			try:
				async with managed_stream(runtime.model_turn(message)) as turn:
					async for item in turn:
						if isinstance(item, ReActTurnResult):
							result = item
						else:
							yield item
			except (LLMError, RuntimeError) as exc:
				await runtime.report_error(exc)
				yield Event.error(str(exc))
				return
			message = result.user_message or message
			next_loop = self.handoff(result.message, message) if self.handoff else None
			if next_loop is not None:
				async with managed_stream(next_loop) as events:
					async for event in events:
						yield event
				return
			if not result.has_tool_calls:
				yield Event(type=EventType.STREAM_END, content=result.content)
			await runtime.end_round(message, result.content, result.parsed_calls)
			if not result.has_tool_calls:
				yield Event.done(result.content)
				return
			await runtime.checkpoint_round(
				[call.get("name", "") for call in result.parsed_calls],
				state={"phase": "react"} if self.handoff else None,
			)
			if (reason := runtime.stop_reason()) is not None:
				yield Event.done(reason)
				return


class ReActPlugin(ExecutionStrategyPlugin):
	name = "react"
	display_name = "ReAct"
	config_schema = config_schema(name, display_name, "按轮次调用模型和工具。", [])

	def create_loop(self, runtime: StrategyRuntime) -> ReActLoop:
		return ReActLoop(runtime)
