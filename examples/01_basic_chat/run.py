"""最简示例：3 行代码跑一个 Agent"""
import asyncio
import os

from axc_agent_engine import AgentModels, Engine, LLMConfig
from axc_agent_engine.llm.client import OpenAIClient
from axc_agent_engine.plugins.builtin import BuiltinToolsPlugin
from axc_agent_engine.plugins.registry import PluginRegistry


def _agent_models() -> AgentModels:
	return AgentModels(default=OpenAIClient(LLMConfig(
		base_url=os.environ["AXC_LLM_BASE_URL"],
		api_key=os.environ["AXC_LLM_API_KEY"],
		model=os.environ.get("AXC_LLM_MODEL", "gpt-4o"),
	)))


async def main():
	registry = PluginRegistry()
	registry.register(BuiltinToolsPlugin)
	engine = Engine(plugin_registry=registry)
	agent = engine.load_agent_template(os.path.join(os.path.dirname(__file__), "agent.yaml")).instantiate(models=_agent_models())
	result = await agent.chat("你好，现在几点了？")
	print(result)
	await engine.close()


if __name__ == "__main__":
	asyncio.run(main())
