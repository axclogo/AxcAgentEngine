"""A host-defined single model turn. 宿主实现的单轮回答策略。"""

from collections.abc import AsyncGenerator
from contextlib import aclosing

from axc_agent_engine import Event
from axc_agent_engine.plugins.config_schema import config_schema
from axc_agent_engine.plugins.strategy import ExecutionStrategyPlugin, StrategyRuntime


class SingleReplyLoop:
	def __init__(self, runtime: StrategyRuntime) -> None:
		self.runtime = runtime

	async def run(self, message: str, state: dict | None) -> AsyncGenerator[Event, None]:
		runtime = self.runtime
		runtime.begin_round()
		async with aclosing(runtime.call_model(message)) as response:
			async for item in response:
				if isinstance(item, Event):
					yield item
				else:
					runtime.append_message(item.message)
					content = item.message["content"]
					await runtime.end_round(message, content, [])
					yield Event.done(content)


class SingleReplyPlugin(ExecutionStrategyPlugin):
	name = "single_reply"
	display_name = "Single reply"
	config_schema = config_schema(name, display_name, "单次调用模型并返回回复。", [])

	def create_loop(self, runtime: StrategyRuntime) -> SingleReplyLoop:
		return SingleReplyLoop(runtime)
