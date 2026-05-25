"""自定义插件示例：展示如何开发和使用外部插件"""
import asyncio
import os

from axc_agent_engine import Engine, LLMConfig, EventType
from axc_agent_engine.plugins.builtin import BuiltinToolsPlugin
from axc_agent_engine.plugins.registry import PluginRegistry
from my_plugin import StatsPlugin


async def main():
	registry = PluginRegistry()
	registry.register_many([BuiltinToolsPlugin, StatsPlugin])
	engine = Engine(
		default_llm=LLMConfig(
			base_url=os.environ["AXC_LLM_BASE_URL"],
			api_key=os.environ["AXC_LLM_API_KEY"],
			model=os.environ.get("AXC_LLM_MODEL", "gpt-4o"),
		),
		plugin_registry=registry,
	)
	agent = engine.load_agent(os.path.join(os.path.dirname(__file__), "agent.yaml"))
	async for event in agent.stream("用 Python 算 1+1，然后查看统计"):
		if event.type == EventType.STREAM_DELTA:
			print(event.content, end="", flush=True)
		elif event.type == EventType.DONE:
			print()
	await engine.close()


if __name__ == "__main__":
	asyncio.run(main())
