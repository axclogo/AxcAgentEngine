"""示例：红蓝对抗模式 — 攻防演练"""
import asyncio
import os

from axc_agent_engine import Engine, LLMConfig
from axc_agent_engine.sidecar.multi_agent import MultiAgentSession, SessionMode
from axc_agent_engine.storage.in_memory import InMemoryMessageBus


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
	red = engine.load_agent(os.path.join(base, "agent_red.yaml"))
	blue = engine.load_agent(os.path.join(base, "agent_blue.yaml"))
	session = MultiAgentSession(
		agents=[red, blue],
		dispatcher=engine._dispatcher,
		mode=SessionMode.REDBLUE,
		topic="某电商平台的安全攻防演练",
		max_rounds=6,
		persona={
			"red-team": {"team": "red", "role": "攻击方", "stance": "寻找漏洞"},
			"blue-team": {"team": "blue", "role": "防守方", "stance": "加固防线"},
		},
	)
	async for event in session.stream():
		if event.type == "message":
			print(f"\n[{event.agent_name}] {event.content}")
		elif event.type == "done":
			print(f"\n--- 演练结束：{event.content} ---")
	await engine.close()


if __name__ == "__main__":
	asyncio.run(main())
