"""Release contracts for active input, recovery and isolation. 验收运行中输入、恢复及隔离。"""

import asyncio
import json
import os
import runpy
import subprocess
import sys
from contextlib import aclosing, asynccontextmanager
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from axc_agent_engine import (
	AgentModels,
	Engine,
	Event,
	EventType,
	InMemoryCheckpointStore,
	LLMStreamChunk,
	PluginRegistry,
	ToolDefinition,
)
from axc_agent_engine.plugins.builtin import CompressPlugin, PORPlugin, ReActPlugin
from tests.engine.test_strategy_plugins import CountPlugin
from tests.planning.test_routing_integration import RoutingProvider

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"


@asynccontextmanager
async def host(tmp_path, plugins, model, store=None, extra=None, name="host"):
	registry = PluginRegistry()
	registry.register_many([ReActPlugin, PORPlugin, CompressPlugin, *([extra] if extra else [])])
	path = tmp_path / f"{name}.yaml"
	path.write_text(yaml.safe_dump({"name": name, "plugins": plugins}))
	engine = Engine(plugin_registry=registry, checkpoint_store=store)
	try:
		yield engine, engine.load_agent_template(str(path)).instantiate(models=AgentModels(default=model))
	finally:
		await engine.close()


class PausedModel(RoutingProvider):
	def __init__(self):
		super().__init__([])
		self.started = asyncio.Event()
		self.release = asyncio.Event()

	async def stream(self, messages, tools=None, **kwargs):
		self.requests.append(messages)
		self.started.set()
		await self.release.wait()
		yield LLMStreamChunk(content_delta=next(m["content"] for m in reversed(messages) if m["role"] == "user"))


async def collect(events):
	return [e async for e in events]


@pytest.mark.parametrize("strategy", ["react", "por", "single_reply"])
async def test_inflight_fifo_receipts_and_durable_retry(tmp_path, strategy, monkeypatch):
	model = PausedModel()
	extra = (
		runpy.run_path(str(EXAMPLES / "08_custom_loop/single_reply.py"))["SingleReplyPlugin"]
		if strategy == "single_reply"
		else None
	)
	config = {strategy: {"enabled": True}}
	if strategy == "por":
		config["por"]["mode"] = "auto"
	store = InMemoryCheckpointStore()
	async with host(tmp_path, config, model, store, extra) as (_, agent):
		task = asyncio.create_task(collect(agent.stream("start", session_id="s", metadata={"run_id": "r"})))
		await asyncio.wait_for(model.started.wait(), 2)
		assert await agent.send_message("r", "bad", session_id="other", message_id="wrong") == "closed"
		assert await agent.send_message("missing", "bad", session_id="s", message_id="wrong") == "closed"
		for key in ["first", "second", "second"]:
			assert await agent.send_message("r", key, session_id="s", message_id=key) == "accepted"
		with pytest.raises(ValueError, match="different"):
			await agent.send_message("r", "different", session_id="s", message_id="first")
		saved = await store.latest("r")
		model.release.set()
		events = await asyncio.wait_for(task, 2)
		assert events[-1].metadata["processed_message_ids"] == ["first", "second"]
		assert [m["content"] for m in model.requests[-1] if m["role"] == "user"] == ["start", "first", "second"]
		assert events[-1].content == "second"
		assert sum(e.type is EventType.DONE for e in events) == 1
		assert await agent.send_message("r", "second", session_id="s", message_id="second") == "processed"
		assert await agent.send_message("r", "late", session_id="s", message_id="late") == "closed"
		assert await agent.send_message("r", "second", session_id="other", message_id="second") == "closed"
		await store.save(replace(saved, run_id="restart"))
	async with host(tmp_path, config, model, store, extra) as (_, agent):
		assert (await collect(agent.resume_stream("restart")))[-1].content == "second"
		assert await agent.send_message("restart", "second", session_id="s", message_id="second") == "processed"


async def test_por_progress_precedes_completion_and_revises_only_remaining_steps(tmp_path):
	model = runpy.run_path(str(EXAMPLES / "09_live_input/run.py"))["DemoModel"]()
	async with host(tmp_path, {"por": {"enabled": True}}, model) as (_, agent):
		stream = agent.stream("整理文档", session_id="docs", metadata={"run_id": "r"})
		async with asyncio.timeout(3):
			seen = []
			async for event in stream:
				seen.append(event)
				if event.type == EventType.STEP_START and not model.release.is_set():
					assert (
						await agent.send_message("r", "剩下的只整理中文", session_id="docs", message_id="zh")
						== "accepted"
					)
					model.release.set()
		assert [e.content for e in seen if e.type == EventType.STEP_START] == ["读取目录", "整理中文文档"]
		assert len([e for e in seen if e.metadata.get("revised")]) == 1
		assert seen[-1].metadata["processed_message_ids"] == ["zh"]


async def test_write_interrupted_before_result_requires_reconciliation(tmp_path, monkeypatch):
	calls = []
	written = asyncio.Event()

	async def write(args, ctx):
		calls.append(ctx["tool_call_id"])
		written.set()
		await asyncio.Event().wait()

	class Loop:
		def __init__(self, runtime):
			self.runtime = runtime

		async def run(self, message, state):
			await self.runtime.execute_tools([{"id": "invoice-1", "name": "invoice", "arguments": {}}])
			yield Event.done("unexpected")

	monkeypatch.setattr(CountPlugin, "create_loop", lambda _, runtime: Loop(runtime))
	monkeypatch.setattr(CountPlugin, "get_tools", lambda _: [ToolDefinition(name="invoice", execute=write)])
	store = InMemoryCheckpointStore()
	config = {"count": {"enabled": True}}
	async with host(tmp_path, config, object(), store, CountPlugin) as (engine, agent):
		task = asyncio.create_task(collect(agent.stream("write", session_id="s", metadata={"run_id": "r"})))
		await asyncio.wait_for(written.wait(), 2)
		await agent.send_message("r", "later", session_id="s", message_id="msg")
		assert engine.cancel_run("r")
		assert await agent.send_message("r", "later", session_id="s", message_id="msg") == "unprocessed"
		assert await agent.send_message("r", "new", session_id="s", message_id="new") == "closed"
		with pytest.raises(ValueError, match="different text"):
			await agent.send_message("r", "changed", session_id="s", message_id="msg")
		events = await asyncio.wait_for(task, 2)
		assert events[-1].type == EventType.CANCELLED
		assert events[-1].metadata["unprocessed_message_ids"] == ["msg"]
		assert await agent.send_message("r", "later", session_id="s", message_id="msg") == "unprocessed"
	async with host(tmp_path, config, object(), store, CountPlugin) as (_, agent):
		with pytest.raises(ValueError, match="reconciliation.*invoice-1"):
			await agent.resume("r")
	assert calls == ["invoice-1"]


async def test_model_input_isolated_and_restored_from_compression_boundary(tmp_path):
	class Model(PausedModel):
		async def ask(self, prompt, **kwargs):
			await asyncio.sleep(0)
			return "summary-A" if "private-A" in prompt else "summary-B"

	model = Model()
	model.release.set()
	config = {"react": {"enabled": True}, "compress": {"enabled": True, "summary": {"after_rounds": 1}}}
	async with host(tmp_path, config, model) as (_, agent):
		await asyncio.gather(
			collect(agent.stream("private-A", session_id="a")), collect(agent.stream("private-B", session_id="b"))
		)
		for session, expected, excluded in [
			("a", "summary-A", "summary-B"),
			("b", "summary-B", "summary-A"),
			("", None, "summary-"),
		]:
			await collect(agent.stream("continue", session_id=session))
			actual = str(model.requests[-1])
			assert excluded not in actual
			if expected:
				assert expected in actual


async def test_inbox_rolls_back_failed_saves_and_bounds_pending_input():
	from axc_agent_engine.core.message_store import MessageStore
	from axc_agent_engine.runtime.inbox import RunInbox

	fail = True

	async def save(progress):
		if fail:
			raise OSError("disk full")

	history = MessageStore()
	inbox = RunInbox(history, save)
	with pytest.raises(OSError):
		await inbox.send("one", "first")
	assert inbox.entries == {}
	fail = False
	for i in range(128):
		assert await inbox.send(str(i), str(i)) == "accepted"
	assert await inbox.send("overflow", "next") == "full"
	assert await inbox.send("1", "1") == "accepted"
	fail = True
	with pytest.raises(OSError):
		await inbox.take(count=1)
	assert inbox.consumed == history.count == 0
	fail = False
	assert await inbox.take(count=1) == ["0"]
	assert await inbox.send("overflow", "next") == "accepted"
	with pytest.raises(ValueError):
		await inbox.send("", "text")
	for invalid in [[], {"entries": [1]}, {"entries": [["a", "x"], ["a", "y"]]}, {"consumed": True}, {"consumed": 2}]:
		with pytest.raises((ValueError, TypeError), match="Invalid checkpoint"):
			inbox.restore(invalid)
	assert inbox.consumed == 1


async def test_during_terminal_hook_and_foreign_checkpoint(tmp_path, monkeypatch):
	started, finish = asyncio.Event(), asyncio.Event()

	async def complete(self, ctx, result, trace):
		started.set()
		await finish.wait()
		return result

	monkeypatch.setattr(ReActPlugin, "on_execution_complete", complete)
	model = PausedModel()
	model.release.set()
	store = InMemoryCheckpointStore()
	config = {"react": {"enabled": True}}
	async with host(tmp_path, config, model, store) as (_, agent):
		task = asyncio.create_task(collect(agent.stream("one", metadata={"run_id": "r", "session_id": "spoof"})))
		await asyncio.wait_for(started.wait(), 2)
		assert await agent.send_message("r", "late", message_id="id", session_id="") == "closed"
		assert await agent.send_message("r", "late", message_id="id", session_id="spoof") == "closed"
		finish.set()
		assert (await task)[-1].type == EventType.DONE
	async with host(tmp_path, config, model, store, name="other") as (_, other):
		with pytest.raises(ValueError, match="another agent"):
			await other.resume("r")


async def test_por_close_joins_inflight_step(tmp_path):
	class Model(runpy.run_path(str(EXAMPLES / "09_live_input/run.py"))["DemoModel"]):
		def __init__(self):
			super().__init__()
			self.entered, self.exited = asyncio.Event(), asyncio.Event()

		async def stream(self, *args, **kwargs):
			try:
				self.entered.set()
				await self.release.wait()
				yield LLMStreamChunk(content_delta="unexpected")
			finally:
				self.exited.set()

	model, store = Model(), InMemoryCheckpointStore()
	async with host(tmp_path, {"por": {"enabled": True}}, model, store) as (_, agent):
		events = agent.stream("plan", session_id="s", metadata={"run_id": "r"})
		async with asyncio.timeout(3):
			async for event in events:
				if event.type == EventType.STEP_START:
					await model.entered.wait()
					assert await agent.send_message("r", "later", session_id="s", message_id="one") == "accepted"
					await events.aclose()
					break
			await model.exited.wait()
		assert (await store.latest("r")).status == "interrupted"
		assert await agent.send_message("r", "later", session_id="s", message_id="one") == "unprocessed"


async def crash_after_receipt(folder, phase):
	class DiskStore(InMemoryCheckpointStore):
		async def save(self, checkpoint):
			await super().save(checkpoint)
			temp = folder / "pending.json"
			temp.write_text(json.dumps(checkpoint.to_dict()))
			temp.replace(folder / "saved.json")

	class CrashModel(PausedModel):
		async def stream(self, messages, tools=None, **kwargs):
			if any(m.get("content") == "followup" for m in messages):
				os._exit(0)
			async for item in super().stream(messages, tools, **kwargs):
				yield item

	model = CrashModel()
	async with host(folder, {"react": {"enabled": True}}, model, DiskStore()) as (_, agent):
		task = asyncio.create_task(collect(agent.stream("original", session_id="s", metadata={"run_id": "crash"})))
		await asyncio.wait_for(model.started.wait(), 3)
		assert await agent.send_message("crash", "followup", session_id="s", message_id="m") == "accepted"
		if phase == "accepted":
			os._exit(0)
		model.release.set()
		await task
		raise AssertionError("crash point not reached")


@pytest.mark.parametrize("phase", ["accepted", "processed"])
async def test_real_process_loss_at_input_boundary(tmp_path, phase):
	from axc_agent_engine.runtime.checkpoint import Checkpoint

	await asyncio.to_thread(
		subprocess.run, [sys.executable, "-m", __name__, str(tmp_path), phase], check=True, timeout=15
	)
	store = InMemoryCheckpointStore()
	saved = Checkpoint(**json.loads((tmp_path / "saved.json").read_text()))
	assert saved.state["inbox"]["consumed"] == int(phase == "processed")
	await store.save(saved)
	model = PausedModel()
	model.release.set()
	async with host(tmp_path, {"react": {"enabled": True}}, model, store) as (_, agent):
		assert (await collect(agent.resume_stream("crash")))[-1].content == "followup"
		assert await agent.send_message("crash", "followup", message_id="m", session_id="s") == "processed"
		assert sum(m.get("content") == "followup" for m in model.requests[-1]) == 1


async def test_parallel_tool_batch_admits_input_before_next_model_call(tmp_path, monkeypatch):
	from axc_agent_engine.tools.tool_output import ToolOutput

	round_inputs = []

	async def end_round(self, ctx, message, answer, tools):
		round_inputs.append(message)

	monkeypatch.setattr(ReActPlugin, "on_round_end", end_round)
	entered = set()
	both, finish = asyncio.Event(), asyncio.Event()

	async def read(args, ctx):
		entered.add(ctx["tool_call_id"])
		if len(entered) == 2:
			both.set()
		await finish.wait()
		return ToolOutput.text("ready")

	class Model(PausedModel):
		async def stream(self, messages, tools=None, **kwargs):
			self.requests.append(messages)
			if len(self.requests) == 1:
				for i in range(2):
					yield LLMStreamChunk(
						tool_call_delta={"id": f"call-{i}", "index": i, "function": {"name": "read", "arguments": "{}"}}
					)
			else:
				yield LLMStreamChunk(content_delta=messages[-1]["content"])

	monkeypatch.setattr(
		ReActPlugin, "get_tools", lambda _: [ToolDefinition(name="read", is_read_only=True, execute=read)]
	)
	model, store = Model(), InMemoryCheckpointStore()
	async with host(tmp_path, {"react": {"enabled": True}}, model, store) as (_, agent):
		task = asyncio.create_task(collect(agent.stream("original", metadata={"run_id": "r"})))
		await asyncio.wait_for(both.wait(), 2)
		assert await agent.send_message("r", "followup", session_id="", message_id="one") == "accepted"
		assert len(model.requests) == 1
		with pytest.raises(ValueError, match="already"):
			await collect(agent.stream("duplicate", metadata={"run_id": "r"}))
		finish.set()
		assert (await asyncio.wait_for(task, 2))[-1].content == "followup"
		assert [m["role"] for m in model.requests[-1]][-3:] == ["tool", "tool", "user"]
		assert entered == {"call-0", "call-1"}
		assert round_inputs[-1] == "followup"


@pytest.mark.parametrize("interrupted", [True, False])
async def test_por_durable_write_boundary(tmp_path, monkeypatch, interrupted):
	from axc_agent_engine.tools.tool_output import ToolOutput

	writes = []
	written = asyncio.Event()

	async def invoice(args, ctx):
		writes.append(ctx["tool_call_id"])
		written.set()
		if interrupted:
			await asyncio.Event().wait()
		return ToolOutput.text("saved")

	class Model(runpy.run_path(str(EXAMPLES / "09_live_input/run.py"))["DemoModel"]):
		async def stream(self, messages, tools=None, **kwargs):
			if not writes:
				yield LLMStreamChunk(
					tool_call_delta={"id": "stable", "index": 0, "function": {"name": "invoice", "arguments": "{}"}}
				)
			else:
				yield LLMStreamChunk(content_delta="saved")

	model, store = Model(), InMemoryCheckpointStore()
	monkeypatch.setattr(PORPlugin, "get_tools", lambda _: [ToolDefinition(name="invoice", execute=invoice)])
	async with host(tmp_path, {"por": {"enabled": True}}, model, store) as (engine, agent):
		task = asyncio.create_task(collect(agent.stream("write", metadata={"run_id": "r"})))
		await asyncio.wait_for(written.wait(), 2)
		if interrupted:
			pending = await store.latest("r")
			assert not pending.forkable
			with pytest.raises(ValueError, match="unsettled writes"):
				await agent.fork_run("r", pending.id)
			assert engine.cancel_run("r")
			assert (await asyncio.wait_for(task, 2))[-1].type == EventType.CANCELLED
			with pytest.raises(ValueError, match="reconciliation.*stable"):
				await agent.resume("r")
		else:
			assert (await asyncio.wait_for(task, 2))[-1].type == EventType.DONE
			checkpoints = await store.list("r")
			assert any("stable" in p.state.get("unsettled_writes", {}) for p in checkpoints)
			saved = next(p for p in checkpoints if p.state.get("phase") in {"step_done", "goal_done"})
			assert not saved.state.get("unsettled_writes")
			branch = await agent.fork_run("r", saved.id)
			resumed = await collect(agent.resume_stream(branch.run_id))
			assert resumed[-1].type == EventType.DONE
			assert all(e.step_id != 1 for e in resumed if e.type == EventType.STEP_START)
		assert writes == ["stable"]


async def test_fast_parallel_por_step_reported_while_sibling_is_running(tmp_path):
	from axc_agent_engine import LLMMessage, LLMResponse

	class Model(runpy.run_path(str(EXAMPLES / "09_live_input/run.py"))["DemoModel"]):
		async def chat(self, messages, tools=None, **options):
			if "用户补充：" in messages[-1]["content"]:
				return await super().chat(messages, tools, **options)
			steps = [{"step_id": i, "description": name} for i, name in enumerate(["slow", "fast"], 1)]
			return LLMResponse(message=LLMMessage(content=json.dumps({"goal": "both", "steps": steps})))

		async def stream(self, messages, tools=None, **options):
			if "1: slow" in messages[-1]["content"]:
				await self.release.wait()
			yield LLMStreamChunk(content_delta="step output")

	model, store = Model(), InMemoryCheckpointStore()
	async with host(tmp_path, {"por": {"enabled": True}}, model, store) as (_, agent):
		seen = []
		async with asyncio.timeout(3):
			async for event in agent.stream("plan", session_id="s", metadata={"run_id": "p"}):
				seen.append(event)
				if event.type == EventType.STEP_COMPLETED and event.step_id == 2:
					assert not model.release.is_set()
					saved = await store.latest("p")
					plan = saved.state["strategy_state"]["payload"]["plan"]
					assert plan["steps"][0]["status"] != "done"
					assert not saved.forkable
					with pytest.raises(ValueError, match="not a forkable cut"):
						await agent.fork_run("p", saved.id)
					created = next(cp for cp in await store.list("p") if cp.state["phase"] == "plan_created")
					early = await agent.fork_run("p", created.id)
					assert (await store.latest(early.run_id)).state["strategy_state"] == created.state["strategy_state"]
					assert await agent.send_message("p", "new scope", session_id="s", message_id="change") == "accepted"
					model.release.set()
		assert seen[-1].metadata["processed_message_ids"] == ["change"]
		assert [e.step_id for e in seen if e.type == EventType.STEP_COMPLETED] == [2, 1, 3]

		batch = next(
			cp for cp in await store.list("p") if cp.forkable and cp.state.get("inbox", {}).get("consumed") == 0
		)
		assert dict(batch.state["inbox"]["entries"]) == {"change": "new scope"}
		branch = await agent.fork_run("p", batch.id)
		events = await collect(agent.resume_stream(branch.run_id))
		assert events[-1].type == EventType.DONE
		assert all(e.type != EventType.STEP_START for e in events)
		assert "processed_message_ids" not in events[-1].metadata
		assert "new scope" not in str((await store.latest(branch.run_id)).state["messages"])


async def test_child_input_and_parent_cancellation_remain_separate(tmp_path):
	model = PausedModel()
	store = InMemoryCheckpointStore()
	config = {"react": {"enabled": True}}
	async with host(tmp_path, config, model, store) as (engine, parent):
		path = tmp_path / "child.yaml"
		path.write_text(yaml.safe_dump({"name": "child", "plugins": config}))
		blocked = PausedModel()
		child = engine.load_agent_template(str(path)).instantiate(models=AgentModels(default=blocked))
		parent_task = asyncio.create_task(collect(parent.stream("parent", session_id="s", metadata={"run_id": "p"})))
		await asyncio.wait_for(model.started.wait(), 2)
		child_task = asyncio.create_task(
			collect(child.stream("child", session_id="s", metadata={"run_id": "c", "parent_run_id": "p"}))
		)
		await asyncio.wait_for(blocked.started.wait(), 2)
		assert await parent.send_message("c", "wrong", session_id="s", message_id="x") == "closed"
		assert await child.send_message("c", "child only", session_id="s", message_id="x") == "accepted"
		assert engine.cancel_run("p")
		p, c = await asyncio.wait_for(asyncio.gather(parent_task, child_task), 2)
		assert p[-1].type == c[-1].type == EventType.CANCELLED
		assert "unprocessed_message_ids" not in p[-1].metadata
		assert c[-1].metadata["unprocessed_message_ids"] == ["x"]


@pytest.mark.parametrize("has_callback", [False, True])
async def test_rejected_write_can_resume_without_reconciliation(tmp_path, has_callback):
	invocations = []

	class Plugin(CountPlugin):
		def get_tools(self):
			async def issue(args, ctx):
				invocations.append(args)

			return [
				ToolDefinition(
					name="issue",
					execute=issue if has_callback else None,
					parameters={
						"type": "object",
						"required": ["qty"],
						"properties": {"qty": {"type": "integer"}},
					},
				)
			]

		def create_loop(self, runtime):
			class Loop:
				async def run(self, message, state):
					if state:
						yield Event.done("restored")
						return
					results = await runtime.execute_tools([{"id": "call-1", "name": "issue", "arguments": {}}])
					assert not results[0].success
					runtime.append_tool_results(results)
					await runtime.save_state("handled", {"rejected": True})
					yield Event.cancelled("paused")

			return Loop()

	store = InMemoryCheckpointStore()
	async with host(tmp_path, {"count": {"enabled": True}}, object(), store, Plugin) as (_, agent):
		await collect(agent.stream("issue", metadata={"run_id": "r"}))
		assert all("unsettled_writes" not in checkpoint.state for checkpoint in await store.list("r"))
		assert await agent.resume("r") == "restored"
		assert invocations == []


if __name__ == "__main__":
	asyncio.run(crash_after_receipt(Path(sys.argv[1]), sys.argv[2]))


async def test_custom_model_hook_receives_all_queued_instructions_in_order(tmp_path):
	ready, proceed = asyncio.Event(), asyncio.Event()
	seen = []

	class Driver(CountPlugin):
		def transform_messages(self, messages, exec_ctx, current_message=""):
			seen.append(current_message)
			return messages

		def create_loop(self, runtime):
			class Loop:
				async def run(self, message, state):
					ready.set()
					await proceed.wait()
					async with aclosing(runtime.call_model(message)) as events:
						async for item in events:
							if isinstance(item, Event):
								yield item
							else:
								runtime.append_message(item.message)
								yield Event.done(item.message["content"])

			return Loop()

	model = PausedModel()
	model.release.set()
	async with host(tmp_path, {"count": {"enabled": True}}, model, extra=Driver) as (_, agent):
		async with asyncio.timeout(3):
			task = asyncio.create_task(collect(agent.stream("start", metadata={"run_id": "r"})))
			try:
				await ready.wait()
				for text in ["first", "second"]:
					assert await agent.send_message("r", text, session_id="", message_id=text) == "accepted"
			finally:
				proceed.set()
				events = await task
		assert seen == ["first\nsecond"]
		assert events[-1].metadata["processed_message_ids"] == ["first", "second"]
