"""示例：Supervisor 模式 — 管理者分配任务"""
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
	sup = engine.load_agent(os.path.join(base, "agent_supervisor.yaml"))
	pm = engine.load_agent(os.path.join(base, "agent_pm.yaml"))
	dev = engine.load_agent(os.path.join(base, "agent_dev.yaml"))
	session = MultiAgentSession(
		agents=[pm, dev],
		dispatcher=engine._dispatcher,
		mode=SessionMode.SUPERVISOR,
		topic="设计一个用户反馈系统的技术方案",
		max_rounds=6,
		supervisor=sup,
	)
	async for event in session.stream():
		if event.type == "message":
			print(f"\n[{event.agent_name}] {event.content}")
		elif event.type == "done":
			print(f"\n--- 讨论结束：{event.content} ---")
	await engine.close()


if __name__ == "__main__":
	asyncio.run(main())
