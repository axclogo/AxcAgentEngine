"""工具调用示例：Agent 使用 Python 执行代码"""
import asyncio
import os

from axc_agent_engine import Engine, LLMConfig, EventType
from axc_agent_engine.plugins.builtin import BuiltinToolsPlugin, RiskGuardPlugin, SafetyPlugin
from axc_agent_engine.plugins.registry import PluginRegistry


async def main():
	registry = PluginRegistry()
	registry.register_many([BuiltinToolsPlugin, SafetyPlugin, RiskGuardPlugin])
	engine = Engine(
		default_llm=LLMConfig(
			base_url=os.environ["AXC_LLM_BASE_URL"],
			api_key=os.environ["AXC_LLM_API_KEY"],
			model=os.environ.get("AXC_LLM_MODEL", "gpt-4o"),
		),
		plugin_registry=registry,
	)
	agent = engine.load_agent(os.path.join(os.path.dirname(__file__), "agent.yaml"))
	async for event in agent.stream("用 Python 计算斐波那契数列前 10 项"):
		if event.type == EventType.STREAM_DELTA:
			print(event.content, end="", flush=True)
		elif event.type == EventType.TOOL_CALL:
			print(f"\n[工具: {event.tool_name}]")
		elif event.type == EventType.TOOL_RESULT:
			print(f"[结果: {event.content[:200]}]")
		elif event.type == EventType.DONE:
			print()
	await engine.close()


if __name__ == "__main__":
	asyncio.run(main())
