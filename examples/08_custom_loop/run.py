"""Run the selected strategy. 运行模板显式选择的循环。"""

import asyncio
import os
from pathlib import Path

from single_reply import SingleReplyPlugin

from axc_agent_engine import AgentModels, Engine, LLMConfig, PluginRegistry
from axc_agent_engine.llm.client import OpenAIClient
from axc_agent_engine.plugins.builtin import PORPlugin


def create_engine() -> Engine:
	registry = PluginRegistry()
	registry.register_many([SingleReplyPlugin, PORPlugin])
	return Engine(plugin_registry=registry)


async def main() -> None:
	engine = create_engine()
	model = OpenAIClient(
		LLMConfig(
			base_url=os.environ["AXC_LLM_BASE_URL"],
			api_key=os.environ["AXC_LLM_API_KEY"],
			model=os.environ.get("AXC_LLM_MODEL", "gpt-4o"),
		)
	)
	try:
		template = engine.load_agent_template(str(Path(__file__).with_name("agent.yaml")))
		agent = template.instantiate(models=AgentModels(default=model))
		print(await agent.chat("循环插件负责什么？"))
	finally:
		await engine.close()


if __name__ == "__main__":
	asyncio.run(main())
