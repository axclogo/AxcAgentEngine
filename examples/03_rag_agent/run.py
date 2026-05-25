"""RAG 示例：基于知识库的问答"""
import asyncio
import os

from axc_agent_engine import Engine, LLMConfig, EventType
from axc_agent_engine.plugins.builtin import BuiltinToolsPlugin, KnowledgePlugin
from axc_agent_engine.plugins.registry import PluginRegistry


async def main():
	registry = PluginRegistry()
	registry.register_many([KnowledgePlugin, BuiltinToolsPlugin])
	engine = Engine(
		default_llm=LLMConfig(
			base_url=os.environ["AXC_LLM_BASE_URL"],
			api_key=os.environ["AXC_LLM_API_KEY"],
			model=os.environ.get("AXC_LLM_MODEL", "gpt-4o"),
		),
		plugin_registry=registry,
	)
	agent = engine.load_agent(os.path.join(os.path.dirname(__file__), "agent.yaml"))
	async for event in agent.stream("AxcAgentEngine 支持哪些 LLM？"):
		if event.type == EventType.STREAM_DELTA:
			print(event.content, end="", flush=True)
		elif event.type == EventType.DONE:
			print()
	await engine.close()


if __name__ == "__main__":
	asyncio.run(main())
