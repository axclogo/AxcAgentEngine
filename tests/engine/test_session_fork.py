"""Execution fork boundaries and isolated continuation. 执行分叉切点与独立续跑。"""

import asyncio
import runpy
from dataclasses import replace

import pytest

from axc_agent_engine import Event, EventType, InMemoryCheckpointStore, LLMStreamChunk, ToolDefinition
from axc_agent_engine.core.context import ExecutionContext
from axc_agent_engine.plugins.builtin import MemoryPlugin, PORPlugin, ReActPlugin
from axc_agent_engine.tools.tool_output import ToolOutput
from tests.engine.test_live_input import EXAMPLES, CountPlugin, PausedModel, collect, host


async def test_historical_fork_restarts_without_ancestors_or_replayed_input(tmp_path):
	class SummaryModel(PausedModel):
		async def ask(self, prompt, **kwargs):
			return "summary of first turn"

	model, store = SummaryModel(), InMemoryCheckpointStore()
	model.release.set()
	config = {"react": {"enabled": True}, "compress": {"enabled": True, "summary": {"after_rounds": 1}}}
	owner = {"tenant_id": "a", "user_id": "user"}
	async with host(tmp_path, config, model, store) as (_, agent):
		await collect(agent.stream("first", session_id="original", metadata={"run_id": "r", **owner}))
		first = await store.latest("r")
		await agent.resume("r", "second", metadata=owner)
		before = [cp.to_dict() for cp in await store.list("r")]
		for invalid in ({}, {"tenant_id": "b", "user_id": "user"}):
			with pytest.raises(ValueError, match="another tenant or user"):
				await agent.fork_run("r", first.id, metadata=invalid)
		branch = await agent.fork_run("r", first.id, metadata=owner)
		child = await store.latest(branch.run_id)
		grandchild = await agent.fork_run(branch.run_id, child.id, metadata=owner)
		assert len({branch.session_id, grandchild.session_id, "original"}) == 3
		assert [cp.to_dict() for cp in await store.list("r")] == before
		assert len(model.requests) == 2
		await store.delete_run("r")
		await store.delete_run(branch.run_id)
	async with host(tmp_path, config, model, store) as (_, agent):
		with pytest.raises(ValueError, match="original tenant"):
			await agent.resume(grandchild.run_id)
		assert await agent.resume(grandchild.run_id, metadata=owner) == "first"
		assert len(model.requests) == 2
		events = await collect(agent.resume_stream(grandchild.run_id, "alternate", metadata=owner))
		assert events[-1].content == "alternate"
		history = model.requests[-1]
		assert [m["content"] for m in history if m["role"] == "user"] == ["first", "alternate"]
		assert "summary of first turn" in str(history)
		latest = await store.latest(grandchild.run_id)
		assert latest.metadata["forked_from"] == {"run_id": branch.run_id, "checkpoint_id": child.id}
		assert latest.state["metadata"]["session_id"] == grandchild.session_id


@pytest.mark.parametrize("private", [True, False])
async def test_memory_scope_at_cut_and_independent_private_updates(tmp_path, private):
	from axc_agent_engine.storage.in_memory import InMemoryKVStore

	backend = InMemoryKVStore()
	model, store = PausedModel(), InMemoryCheckpointStore()
	model.release.set()
	config = {
		"react": {"enabled": True},
		"memory": {"enabled": True, "session_scope": private, "scope_keys": ["region"], "auto_extract": False},
	}
	owner = {"region": 0 if private else "east"}

	async def listed_memories(agent, session_id):
		ctx = ExecutionContext()
		ctx.state.metadata.update(owner, session_id=session_id)
		response = await agent.registry.get("memory_list").execute({}, {"exec_ctx": ctx})
		return {item["content"]: item["metadata"]["scope"] for item in response.content["memories"]}

	async def add_on_round(self, ctx, message, answer, calls):
		await self._tool_memory_add_lesson({"content": message}, {"exec_ctx": ctx})

	class Memory(MemoryPlugin):
		def initialize(self, config, plugin_ctx):
			super().initialize(config, replace(plugin_ctx, kv_store=backend))

		on_round_end = add_on_round

	async with host(tmp_path, config, model, store, Memory) as (_, agent):
		await collect(agent.stream("saved-a", session_id="s", metadata={"run_id": "r", **owner}))
		first = await store.latest("r")
		await agent.resume("r", "later-b", metadata=owner)
		with pytest.raises(ValueError, match="memory scope"):
			await agent.fork_run("r", first.id, metadata={"region": "west"})
		branch = await agent.fork_run("r", first.id, metadata=owner)
		for identity in ({}, {"region": "west"}):
			with pytest.raises(ValueError, match="memory scope"):
				await agent.resume(branch.run_id, "unauthorized", metadata=identity)
		await agent.resume(branch.run_id, "child-c", metadata=owner)
		child = await listed_memories(agent, branch.session_id)
		original = await listed_memories(agent, "s")
		assert set(child) == ({"saved-a", "child-c"} if private else {"saved-a", "later-b", "child-c"})
		assert ("child-c" in original) == (not private)
		scope = (await store.latest(branch.run_id)).state["plugin_scopes"]["memory"]
		assert set(child.values()) == {scope}
	async with host(tmp_path, config, model, store, Memory) as (_, restarted):
		assert await listed_memories(restarted, branch.session_id) == child


async def test_custom_cut_discovery_and_rejection_before_publication(tmp_path):
	class Strategy(CountPlugin):
		def create_loop(self, runtime):
			class Loop:
				async def run(self, message, state):
					if state is None:
						await runtime.save_state("custom", {"n": 1})
						await runtime.save_state("custom", {"n": 2}, forkable=True)
						yield Event.cancelled("paused")
					else:
						yield Event.done(f"continued at {state['n']}")

			return Loop()

	store = InMemoryCheckpointStore()
	config = {"count": {"enabled": True}}
	async with host(tmp_path, config, object(), store, Strategy) as (_, agent):
		await collect(agent.stream("run", metadata={"run_id": "r"}))
		cuts = await store.list("r")
		assert [cp.state["strategy_state"] for cp in cuts if cp.forkable] == [{"n": 2}]
		cut = next(cp for cp in cuts if cp.forkable)
		for invalid in (cuts[0].id, cuts[-1].id, "missing"):
			with pytest.raises(ValueError, match="not found|not a forkable cut"):
				await agent.fork_run("r", invalid)
		bad = replace(cut, run_id="foreign", state={**cut.state, "metadata": {"agent_name": "other"}})
		await store.save(bad)
		with pytest.raises(ValueError, match="another agent"):
			await agent.fork_run("foreign", bad.id)
		assert set(await store.list_runs()) == {"r", "foreign"}
		branch = await agent.fork_run("r", cut.id)
		assert await agent.resume(branch.run_id) == "continued at 2"


async def test_failed_publication_retry_and_incompatible_plugin_state(tmp_path):
	class Store(InMemoryCheckpointStore):
		reject = True

		async def save(self, checkpoint):
			if self.reject and "forked_from" in checkpoint.metadata:
				raise OSError("storage unavailable")
			await super().save(checkpoint)

	store, model = Store(), PausedModel()
	model.release.set()
	config = {"react": {"enabled": True}}
	async with host(tmp_path, config, model, store) as (_, agent):
		await collect(agent.stream("source", metadata={"run_id": "r"}))
		cut = await store.latest("r")
		with pytest.raises(OSError, match="storage unavailable"):
			await agent.fork_run("r", cut.id)
		assert await store.list_runs() == ["r"]
		store.reject = False
		branch = await agent.fork_run("r", cut.id)
		with pytest.raises(ValueError, match="not found"):
			await agent.fork_run(branch.run_id, cut.id)
	async with host(tmp_path, {**config, "compress": {"enabled": True}}, model, store) as (_, changed):
		with pytest.raises(ValueError, match="recorded session plugins"):
			await changed.fork_run("r", cut.id)
		with pytest.raises(ValueError, match="recorded session plugins"):
			await changed.resume(branch.run_id, "new message")
	assert len(model.requests) == 1


@pytest.mark.parametrize("strategy", ["react", "por"])
async def test_mid_round_fork_has_independent_cancellation_receipts_and_tools(tmp_path, monkeypatch, strategy):
	started, release = asyncio.Event(), asyncio.Event()
	writes = []

	async def write(args, ctx):
		writes.append(ctx["tool_call_id"])
		return ToolOutput.text("saved")

	driver = PORPlugin if strategy == "por" else ReActPlugin
	monkeypatch.setattr(driver, "get_tools", lambda _: [ToolDefinition(name="write", execute=write)])

	class Model(PausedModel):
		async def stream(self, messages, tools=None, **options):
			if not any(m["role"] == "tool" for m in messages):
				yield LLMStreamChunk(
					tool_call_delta={"id": "w", "index": 0, "function": {"name": "write", "arguments": "{}"}}
				)
				return
			if messages[-1]["content"] == "branch instruction":
				started.set()
				await release.wait()
			else:
				self.started.set()
				await self.release.wait()
			yield LLMStreamChunk(content_delta="finished")

	model, store = Model(), InMemoryCheckpointStore()
	settings = {"enabled": True, "mode": "auto"} if strategy == "por" else {"enabled": True}
	async with host(tmp_path, {strategy: settings}, model, store) as (engine, agent):
		parent = asyncio.create_task(collect(agent.stream("original", session_id="s", metadata={"run_id": "p"})))
		async with asyncio.timeout(3):
			await model.started.wait()
			assert await agent.send_message("p", "queued at parent", session_id="s", message_id="a") == "accepted"
			cut = next(cp for cp in await store.list("p") if cp.forkable)
			child = await agent.fork_run("p", cut.id)
			events = asyncio.create_task(collect(agent.resume_stream(child.run_id, "branch instruction")))
			await started.wait()
			assert engine.cancel_run("p")
			assert (await parent)[-1].type == EventType.CANCELLED
			release.set()
			assert (await events)[-1].type == EventType.DONE
		assert writes == ["w"]
		assert (
			await agent.send_message(child.run_id, "queued at parent", session_id=child.session_id, message_id="a")
			== "closed"
		)
		assert await agent.send_message("p", "queued at parent", session_id="s", message_id="a") == "unprocessed"


async def test_exact_cut_rejects_dangling_history_and_legacy_snapshot(tmp_path):
	from axc_agent_engine import Checkpoint

	model, store = PausedModel(), InMemoryCheckpointStore()
	model.release.set()
	async with host(tmp_path, {"react": {"enabled": True}}, model, store) as (_, agent):
		await collect(agent.stream("seed", metadata={"run_id": "r"}))
		done = await store.latest("r")
		open_call = {"role": "assistant", "tool_calls": [{"id": "unfinished"}]}
		await store.save(replace(done, run_id="malformed", state={**done.state, "messages": [open_call]}))
		with pytest.raises(ValueError, match="Missing tool results.*unfinished"):
			await agent.fork_run("malformed", done.id)
		legacy = Checkpoint(run_id="old", state={"metadata": {"agent_name": "host"}, "phase": "round_end"})
		await store.save(legacy)
		with pytest.raises(ValueError, match="not a forkable cut"):
			await agent.fork_run("old", legacy.id)
		assert set(await store.list_runs()) == {"r", "malformed", "old"}


async def test_external_private_recall_disables_forks_only_for_that_configuration(tmp_path):
	model, store = PausedModel(), InMemoryCheckpointStore()
	model.release.set()
	config = {"react": {"enabled": True}, "compress": {"enabled": True, "recall": {"resource": "host-index"}}}
	async with host(tmp_path, config, model, store) as (_, agent):
		await collect(agent.stream("original", metadata={"run_id": "r"}))
		done = await store.latest("r")
		assert not done.forkable
		with pytest.raises(ValueError, match="cannot be forked.*compress"):
			await agent.fork_run("r", done.id)
		assert await agent.resume("r") == "original"


async def test_custom_migration_runs_once_and_source_stays_at_recorded_version(tmp_path):
	import json

	from axc_agent_engine import Checkpoint

	class Writer(CountPlugin):
		def create_loop(self, runtime):
			class Loop:
				async def run(self, message, state):
					await runtime.save_state("custom", {"v1": message}, forkable=True)
					yield Event.cancelled("paused")

			return Loop()

	calls = []

	class Reader(CountPlugin):
		state_version = 2

		def migrate_state(self, state, from_version):
			calls.append(from_version)
			return {"label": state.pop("v1")}

	store = InMemoryCheckpointStore()
	config = {"count": {"enabled": True}}
	async with host(tmp_path, config, object(), store, Writer) as (_, agent):
		await collect(agent.stream("one", metadata={"run_id": "r"}))
		cut = next(cp for cp in await store.list("r") if cp.forkable)
	async with host(tmp_path, config, object(), store, Reader) as (_, agent):
		child = await agent.fork_run("r", cut.id)
		assert cut.state["strategy_state"] == {"v1": "one"}
		assert cut.state["state_version"] == 1
	disk = json.dumps((await store.latest(child.run_id)).to_dict())
	restarted = InMemoryCheckpointStore()
	await restarted.save(Checkpoint(**json.loads(disk)))
	async with host(tmp_path, config, object(), restarted, Reader) as (_, agent):
		assert await agent.resume(child.run_id) == "resumed one"
	assert calls == [1]


async def test_private_state_rejects_changed_scope_config_before_copy(tmp_path):
	model, store = PausedModel(), InMemoryCheckpointStore()
	model.release.set()
	config = {"react": {"enabled": True}, "memory": {"enabled": True, "scope_keys": [], "session_scope": True}}
	async with host(tmp_path, config, model, store, MemoryPlugin) as (_, agent):
		await collect(agent.stream("private", metadata={"run_id": "r"}))
		cut = await store.latest("r")
	config["memory"]["session_scope"] = False
	async with host(tmp_path, config, model, store, MemoryPlugin) as (_, agent):
		with pytest.raises(ValueError, match="recorded memory scope configuration"):
			await agent.fork_run("r", cut.id)
		assert await store.list_runs() == ["r"]


async def test_completed_fork_keeps_admitted_turn_but_starts_its_own_receipts(tmp_path):
	model, store = PausedModel(), InMemoryCheckpointStore()
	model.release.set()
	async with host(tmp_path, {"react": {"enabled": True}}, model, store) as (_, agent):
		await collect(agent.stream("first", session_id="parent", metadata={"run_id": "r"}))
		await agent.resume("r", "second")
		cut = await store.latest("r")
		prior = cut.state["result_metadata"]["processed_message_ids"]
		child = await agent.fork_run("r", cut.id)
		replayed = (await collect(agent.resume_stream(child.run_id)))[-1]
		assert replayed.content == "second"
		assert "processed_message_ids" not in replayed.metadata
		assert replayed.metadata["usage"]["total_tokens"] == 0
		events = await collect(agent.resume_stream(child.run_id, "third"))
		assert set(events[-1].metadata["processed_message_ids"]).isdisjoint(prior)
		assert [m["content"] for m in model.requests[-1] if m["role"] == "user"] == ["first", "second", "third"]
		assert (await store.latest("r")).id == cut.id


async def test_missing_configured_tenant_blocks_fork_of_unowned_cut(tmp_path):
	model, store = PausedModel(), InMemoryCheckpointStore()
	model.release.set()
	config = {"react": {"enabled": True}, "memory": {"enabled": True, "scope_keys": ["tenant_id"]}}
	async with host(tmp_path, config, model, store, MemoryPlugin) as (_, agent):
		await collect(agent.stream("source", metadata={"run_id": "r"}))
		cut = await store.latest("r")
		with pytest.raises(ValueError, match="original memory scope key: tenant_id"):
			await agent.fork_run("r", cut.id)
		assert await store.list_runs() == ["r"]


@pytest.mark.parametrize("hook", ["capture_session", "fork_scope"])
async def test_invalid_plugin_fork_contract_fails_at_its_boundary(tmp_path, monkeypatch, hook):
	model, store = PausedModel(), InMemoryCheckpointStore()
	model.release.set()

	monkeypatch.setattr(ReActPlugin, hook, lambda *args: [])
	async with host(tmp_path, {"react": {"enabled": True}}, model, store) as (_, agent):
		if hook == "capture_session":
			with pytest.raises(TypeError, match="react.capture_session"):
				await collect(agent.stream("source", metadata={"run_id": "r"}))
			assert all(not cp.forkable for cp in await store.list("r"))
		else:
			await collect(agent.stream("source", metadata={"run_id": "r"}))
			cut = await store.latest("r")
			with pytest.raises(TypeError, match="react.fork_scope"):
				await agent.fork_run("r", cut.id)
		assert await store.list_runs() == ["r"]


async def test_finalized_por_fork_reuses_summary(tmp_path, monkeypatch):
	model = runpy.run_path(str(EXAMPLES / "09_live_input/run.py"))["DemoModel"]()
	model.release.set()
	store = InMemoryCheckpointStore()
	async with host(tmp_path, {"por": {"enabled": True}}, model, store) as (_, agent):
		await collect(agent.stream("plan", metadata={"run_id": "p"}))
		finalized = next(cp for cp in await store.list("p") if cp.state["phase"] == "finalize")
		child = await agent.fork_run("p", finalized.id)

		def unexpected(*args, **kwargs):
			pytest.fail("Finalized branch called the model again")

		monkeypatch.setattr(model, "stream", unexpected)
		monkeypatch.setattr(model, "chat", unexpected)
		assert await agent.resume(child.run_id) == finalized.state["strategy_state"]["metadata"]["summary"]


async def test_resuming_a_branch_cannot_redirect_its_stored_plugin_scope(tmp_path, monkeypatch):
	model, store = PausedModel(), InMemoryCheckpointStore()
	model.release.set()
	config = {"react": {"enabled": True}, "memory": {"enabled": True, "scope_keys": ["region"]}}
	identity = {"region": "east"}
	async with host(tmp_path, config, model, store, MemoryPlugin) as (_, agent):
		await collect(agent.stream("source", metadata={"run_id": "r", **identity}))
		cut = await store.latest("r")
		child = await agent.fork_run("r", cut.id, metadata=identity)
		seed = await store.latest(child.run_id)
		monkeypatch.setattr(MemoryPlugin, "fork_scope", lambda *args: "west")
		with pytest.raises(ValueError, match="change stored plugin scopes"):
			await agent.resume(child.run_id, "continue", metadata=identity)
		assert (await store.latest(child.run_id)).id == seed.id
		assert len(model.requests) == 1


async def test_fork_requires_durable_source(tmp_path):
	async with host(tmp_path, {"react": {"enabled": True}}, PausedModel()) as (_, agent):
		with pytest.raises(ValueError, match="CheckpointStore is required"):
			await agent.fork_run("unknown", "cut")


@pytest.mark.parametrize("scopes", ["memory:tenant", {"memory": False}, {"memory": " "}])
async def test_invalid_stored_scope_cannot_resume_a_run(tmp_path, scopes):
	from axc_agent_engine import Checkpoint

	store = InMemoryCheckpointStore()
	await store.save(Checkpoint(run_id="r", state={"strategy": "react", "plugin_scopes": scopes}))
	async with host(tmp_path, {"react": {"enabled": True}}, PausedModel(), store) as (_, agent):
		with pytest.raises(ValueError, match="Invalid checkpoint plugin_scopes"):
			await collect(agent.resume_stream("r"))
	assert len(await store.list("r")) == 1
