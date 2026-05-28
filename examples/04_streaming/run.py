"""流式输出示例：展示所有事件类型"""
import asyncio
import os

from axc_agent_engine import AgentModels, Engine, LLMConfig, EventType
from axc_agent_engine.llm.client import OpenAIClient
from axc_agent_engine.plugins.builtin import BuiltinToolsPlugin, TracingPlugin
from axc_agent_engine.plugins.registry import PluginRegistry


def _agent_models() -> AgentModels:
	return AgentModels(default=OpenAIClient(LLMConfig(
		base_url=os.environ["AXC_LLM_BASE_URL"],
		api_key=os.environ["AXC_LLM_API_KEY"],
		model=os.environ.get("AXC_LLM_MODEL", "gpt-4o"),
	)))


async def main():
	registry = PluginRegistry()
	registry.register_many([BuiltinToolsPlugin, TracingPlugin])
	engine = Engine(plugin_registry=registry)
	agent = engine.load_agent_template(os.path.join(os.path.dirname(__file__), "agent.yaml")).instantiate(models=_agent_models())
	async for event in agent.stream("用 Python 算 100 以内的质数"):
		match event.type:
			case EventType.THINKING_START:
				print("[思考中...]")
			case EventType.THINKING_DELTA:
				print(f"  💭 {event.content}", end="")
			case EventType.THINKING_END:
				print("\n[思考完毕]")
			case EventType.STREAM_DELTA:
				print(event.content, end="", flush=True)
			case EventType.TOOL_CALL:
				print(f"\n🔧 调用工具: {event.tool_name}")
			case EventType.TOOL_RESULT:
				print(f"📋 结果: {event.content[:200]}")
			case EventType.PLAN_CREATED:
				print(f"\n📝 创建计划: {event.content}")
				for step in event.steps:
					print(f"   - {step['step_id']}: {step['description']}")
			case EventType.STEP_START:
				print(f"\n▶ 步骤 {event.step_id}: {event.content}")
			case EventType.STEP_COMPLETED:
				print(f"✅ 步骤 {event.step_id} 完成")
			case EventType.ERROR:
				print(f"\n❌ 错误: {event.content}")
			case EventType.DONE:
				print("\n--- 完成 ---")
	await engine.close()


if __name__ == "__main__":
	asyncio.run(main())
