"""One offline POR run with live progress and a follow-up. 离线演示计划进度与运行中插话。"""

import argparse
import asyncio
import json
from pathlib import Path

from axc_agent_engine import (
	AgentModels,
	Engine,
	EventType,
	InMemoryCheckpointStore,
	LLMMessage,
	LLMResponse,
	LLMStreamChunk,
	PluginRegistry,
)
from axc_agent_engine.plugins.builtin import PORPlugin


class DemoModel:
	model = "offline-demo"
	tool_name_mapping = None

	def __init__(self):
		self.release = asyncio.Event()

	async def chat(self, messages, tools=None, **options):
		revised = "用户补充：" in messages[-1]["content"]
		revised_step = "检查英文文档" if "检查英文" in messages[-1]["content"] else "整理中文文档"
		steps = [revised_step] if revised else ["读取目录", "整理全部文档"]
		plan = {
			"goal": steps[-1],
			"steps": [
				{"step_id": i, "description": text, "depends_on": [i - 1] if i > 1 else []}
				for i, text in enumerate(steps, 1)
			],
		}
		return LLMResponse(message=LLMMessage(content=json.dumps(plan, ensure_ascii=False)))

	async def stream(self, messages, tools=None, **options):
		await self.release.wait()
		text = "已按补充要求整理中文文档" if any("只整理中文" in str(m) for m in messages) else "已读取目录"
		if any("检查英文" in str(m) for m in messages):
			text = "已检查英文文档"
		yield LLMStreamChunk(content_delta=text)

	async def ask(self, prompt, **options):
		return '{"action":"done","step_ok":true,"goal_achieved":true}'

	async def close(self):
		pass


async def main(cancel=False, fork=False):
	registry = PluginRegistry()
	registry.register(PORPlugin)
	store = InMemoryCheckpointStore()
	engine = Engine(plugin_registry=registry, checkpoint_store=store)
	model = DemoModel()
	try:
		agent = engine.load_agent_template(str(Path(__file__).with_name("agent.yaml"))).instantiate(
			models=AgentModels(default=model)
		)
		async for event in agent.stream("整理文档", session_id="docs", metadata={"run_id": "demo"}):
			if event.type == EventType.STEP_START and not model.release.is_set():
				print(event.type.value, event.content)
				print(await agent.send_message("demo", "剩下的只整理中文", session_id="docs", message_id="only-zh"))
				if cancel:
					engine.cancel_run("demo")
				else:
					model.release.set()
			if event.type == EventType.CANCELLED:
				print(event.type.value, event.metadata.get("unprocessed_message_ids", []))
			if event.type in {EventType.PLAN_CREATED, EventType.DONE}:
				print(event.type.value, event.content, event.metadata.get("processed_message_ids", []))
		if fork:
			model.release.set()
			cut = next(cp for cp in await store.list("demo") if cp.forkable and cp.state["phase"] == "plan_created")
			child = await agent.fork_run("demo", cut.id)
			print("fork", child.session_id, child.run_id)
			async for event in agent.resume_stream(child.run_id, "改为检查英文文档"):
				if event.type == EventType.DONE:
					print(event.type.value, event.content)
	finally:
		await engine.close()


if __name__ == "__main__":
	parser = argparse.ArgumentParser()
	parser.add_argument("--cancel", action="store_true")
	parser.add_argument("--fork", action="store_true")
	options = parser.parse_args()
	asyncio.run(main(options.cancel, options.fork))
