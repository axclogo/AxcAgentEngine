"""示例：群聊模式 — 多 Agent 轮流讨论"""
import asyncio
import os

from axc_agent_engine import Engine, LLMConfig
from axc_agent_engine.sidecar.multi_agent import MultiAgentSession, SessionMode
from axc_agent_engine.storage.memory import InMemoryMessageBus


async def main():
	engine = Engine(
		default_llm=LLMConfig(
			base_url=os.environ["AXC_LLM_BASE_URL"],
			api_key=os.environ["AXC_LLM_API_KEY"],
			model=os.environ.get("AXC_LLM_MODEL", "gpt-4o"),
		),
		message_bus=InMemoryMessageBus(),
	)
	base = os.path.dirname(__file__)
	pm = engine.load_agent(os.path.join(base, "agent_pm.yaml"))
	dev = engine.load_agent(os.path.join(base, "agent_dev.yaml"))
	session = MultiAgentSession(
		agents=[pm, dev],
		dispatcher=engine._dispatcher,
		mode=SessionMode.GROUP_CHAT,
		topic="设计一个用户反馈系统",
		max_rounds=4,
	)
	async for event in session.stream():
		if event.type == "message":
			print(f"\n[{event.agent_name}] {event.content}")
		elif event.type == "done":
			print(f"\n--- 讨论结束：{event.content} ---")
	await engine.close()


if __name__ == "__main__":
	asyncio.run(main())
