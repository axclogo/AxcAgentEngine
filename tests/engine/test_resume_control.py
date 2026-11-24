"""Run ownership and resume-time input contracts. 验证运行独占与恢复输入。"""

import asyncio
import runpy

import pytest

from axc_agent_engine import Event, EventType, InMemoryCheckpointStore, ToolDefinition
from axc_agent_engine.planning.checkpointing import plan_to_state
from axc_agent_engine.planning.planner import Plan, PlanStep
from axc_agent_engine.runtime.checkpoint import Checkpoint
from axc_agent_engine.tools.tool_output import ToolOutput
from tests.engine.test_live_input import EXAMPLES, CountPlugin, PausedModel, collect, host


@pytest.mark.parametrize("session", ["", "account"])
async def test_concurrent_resume_cannot_repeat_write(tmp_path, session):
	entered, release = asyncio.Event(), asyncio.Event()
	writes = []

	class PaymentPlugin(CountPlugin):
		def get_tools(self):
			async def pay(args, ctx):
				writes.append(ctx["tool_call_id"])
				return ToolOutput.text("paid")

			return [ToolDefinition(name="pay", execute=pay)]

		def create_loop(self, runtime):
			class Loop:
				async def run(self, message, state):
					if not state:
						entered.set()
						await release.wait()
						results = await runtime.execute_tools([{"id": "payment", "name": "pay", "arguments": {}}])
						runtime.append_tool_results(results)
						await runtime.save_state("payment", {"paid": True})
					yield Event.done("ok")

			return Loop()

	store = InMemoryCheckpointStore()
	async with host(tmp_path, {"count": {"enabled": True}}, object(), store, PaymentPlugin) as (_, agent):
		task = asyncio.create_task(collect(agent.stream("pay", session_id=session, metadata={"run_id": "r"})))
		try:
			await asyncio.wait_for(entered.wait(), 2)
			with pytest.raises(ValueError, match="already active"):
				await asyncio.wait_for(agent.resume("r"), 1)
			with pytest.raises(ValueError, match="already active"):
				await asyncio.wait_for(collect(agent.stream("repeat", session_id=session, metadata={"run_id": "r"})), 1)
		finally:
			release.set()
			await task
		assert await agent.resume("r") == "ok"
		assert writes == ["payment"]
		checkpoints = await store.list("r")
		assert [cp.sequence for cp in checkpoints] == list(range(1, len(checkpoints) + 1))


async def test_queued_resume_owns_id_and_rejects_replaced_snapshot(tmp_path):
	looked_up = asyncio.Event()

	class Store(InMemoryCheckpointStore):
		async def latest(self, run_id):
			saved = await super().latest(run_id)
			if run_id == "waiting":
				looked_up.set()
			return saved

	store, model = Store(), PausedModel()
	meta = {"agent_name": "host", "session_id": "same"}
	await store.save(Checkpoint(run_id="waiting", sequence=1, state={"strategy": "react", "metadata": meta}))
	async with host(tmp_path, {"react": {"enabled": True}}, model, store) as (_, agent):
		blocker = asyncio.create_task(collect(agent.stream("block", session_id="same", metadata={"run_id": "block"})))
		await asyncio.wait_for(model.started.wait(), 2)
		first = asyncio.create_task(agent.resume("waiting"))
		try:
			await asyncio.wait_for(looked_up.wait(), 2)
			with pytest.raises(ValueError, match="already active"):
				await asyncio.wait_for(agent.resume("waiting"), 1)
			replacement = Checkpoint(run_id="waiting", sequence=2, state={"strategy": "react", "metadata": meta})
			await store.save(replacement)
		finally:
			model.release.set()
			await blocker
		with pytest.raises(ValueError, match="Checkpoint changed"):
			await first
		assert (await store.latest("waiting")).id == replacement.id
		assert len(model.requests) == 1


@pytest.mark.parametrize("strategy", ["react", "count"])
async def test_resume_input_follows_saved_pending_text_once(tmp_path, strategy):
	observed = []

	class DirectReader(CountPlugin):
		def create_loop(self, runtime):
			class Loop:
				async def run(self, message, state):
					observed.append((message, runtime.history()))
					assert state == {"position": 1}
					yield Event.done("custom handled")

			return Loop()

	store, model = InMemoryCheckpointStore(), PausedModel()
	model.release.set()
	snapshot = {
		"strategy": strategy,
		"messages": [{"role": "user", "content": "initial"}],
		"inbox": {"entries": [["m1", "previously accepted"]], "consumed": 0},
	}
	if strategy == "count":
		snapshot["strategy_state"] = {"position": 1}
	await store.save(Checkpoint(run_id="r", state=snapshot))
	async with host(tmp_path, {strategy: {"enabled": True}}, model, store, DirectReader) as (_, agent):
		events = await collect(agent.resume_stream("r", message="change"))
		assert len(events[-1].metadata["processed_message_ids"]) == 2
		assert events[-1].metadata["processed_message_ids"][0] == "m1"
		if strategy == "count":
			assert len(observed) == 1
			message, history = observed[0]
			assert message == "previously accepted\nchange"
		else:
			assert events[-1].content == "change"
			history = model.requests[-1]
		assert [m["content"] for m in history if m["role"] == "user"] == ["initial", "previously accepted", "change"]


async def test_resume_input_revises_por_before_dispatch(tmp_path):
	model = runpy.run_path(str(EXAMPLES / "09_live_input/run.py"))["DemoModel"]()
	model.release.set()
	store = InMemoryCheckpointStore()
	plan = Plan(goal="old", steps=[PlanStep(step_id=1, description="obsolete")])
	await store.save(Checkpoint(run_id="r", state={"strategy": "por", "strategy_state": plan_to_state(plan)}))
	async with host(tmp_path, {"por": {"enabled": True}}, model, store) as (_, agent):
		events = await collect(agent.resume_stream("r", message="剩下的只整理中文"))
		assert [e.content for e in events if e.type == EventType.STEP_START] == ["整理中文文档"]
		assert sum(bool(e.metadata.get("revised")) for e in events) == 1
		assert len(events[-1].metadata["processed_message_ids"]) == 1
		history = (await store.latest("r")).state["messages"]
		assert sum(m.get("content") == "剩下的只整理中文" for m in history) == 1


async def test_full_inbox_rejects_resume_supplement_without_changing_checkpoint(tmp_path):
	store, model = InMemoryCheckpointStore(), PausedModel()
	model.release.set()
	saved = Checkpoint(
		run_id="r", state={"strategy": "react", "inbox": {"entries": [[str(i), str(i)] for i in range(128)]}}
	)
	await store.save(saved)
	async with host(tmp_path, {"react": {"enabled": True}}, model, store) as (_, agent):
		for text, error in [("  ", "contain text"), ("new", "inbox is full")]:
			with pytest.raises(ValueError, match=error):
				await agent.resume("r", message=text)
		assert (await store.latest("r")).id == saved.id
		assert not model.requests
		events = await collect(agent.resume_stream("r"))
		assert events[-1].metadata["processed_message_ids"] == [str(i) for i in range(128)]


async def test_cancel_while_waiting_for_slot_releases_run_claim(tmp_path):
	queued = asyncio.Event()

	class Store(InMemoryCheckpointStore):
		async def latest(self, run_id):
			if run_id == "queue":
				queued.set()
			return await super().latest(run_id)

	store, model = Store(), PausedModel()
	await store.save(Checkpoint(run_id="queue", state={"strategy": "react", "metadata": {"session_id": "same"}}))
	async with host(tmp_path, {"react": {"enabled": True}}, model, store) as (_, agent):
		blocker = asyncio.create_task(collect(agent.stream("block", session_id="same", metadata={"run_id": "block"})))
		await asyncio.wait_for(model.started.wait(), 2)
		waiting = asyncio.create_task(agent.resume("queue"))
		try:
			await asyncio.wait_for(queued.wait(), 2)
			waiting.cancel()
			with pytest.raises(asyncio.CancelledError):
				await waiting
		finally:
			model.release.set()
			await blocker
		assert await agent.resume("queue", message="safe retry") == "safe retry"


async def test_repeated_completed_resume_preserves_result_and_newer_session(tmp_path):
	store, model = InMemoryCheckpointStore(), PausedModel()
	model.release.set()
	async with host(tmp_path, {"react": {"enabled": True}}, model, store) as (_, agent):
		original = (await collect(agent.stream("first", session_id="s", metadata={"run_id": "old"})))[-1]
		await collect(agent.stream("second", session_id="s"))
		before = await store.list("old")
		for _ in range(2):
			events = await collect(agent.resume_stream("old"))
			assert len(events) == 1
			assert events[0].type == EventType.DONE
			assert (events[0].content, events[0].metadata) == (original.content, original.metadata)
		assert len(model.requests) == 2
		assert await store.list("old") == before
		assert (await agent.get_session("s")).messages[-1]["content"] == "second"
		assert await agent.resume("old", message="third") == "third"
		assert len(model.requests) == 3


@pytest.mark.parametrize("missing", [True, False])
async def test_resume_checks_each_tool_result_before_processing_input(tmp_path, missing):
	store, model = InMemoryCheckpointStore(), PausedModel()
	model.release.set()
	calls = [{"id": key, "type": "function", "function": {"name": "read", "arguments": "{}"}} for key in ["a", "b"]]
	messages = [
		{"role": "user", "content": "start"},
		{"role": "assistant", "content": "", "tool_calls": calls},
		{"role": "tool", "tool_call_id": "b", "content": "second"},
	]
	if not missing:
		messages.append({"role": "tool", "tool_call_id": "a", "content": "first"})
	saved = Checkpoint(run_id="r", state={"strategy": "react", "messages": messages})
	await store.save(saved)
	async with host(tmp_path, {"react": {"enabled": True}}, model, store) as (_, agent):
		if missing:
			with pytest.raises(ValueError, match=r"Missing tool results.*\['a'\]"):
				await agent.resume("r", message="follow up")
			assert await store.list("r") == [saved]
			assert model.requests == []
		else:
			assert await agent.resume("r", message="follow up") == "follow up"
			assert [m["tool_call_id"] for m in model.requests[0] if m["role"] == "tool"] == ["b", "a"]


async def test_por_drains_input_received_while_revising_before_dispatch(tmp_path, monkeypatch):
	from axc_agent_engine.planning.planning_service import PlanningService

	entered, release = asyncio.Event(), asyncio.Event()
	revisions = []

	async def revise(llm, ctx, goal):
		revisions.append(goal)
		if len(revisions) == 1:
			entered.set()
			await release.wait()
		action = "write files" if len(revisions) == 1 else "inspect only"
		return Plan(action, [PlanStep(1, action)])

	monkeypatch.setattr(PlanningService, "generate_plan", revise)
	store = InMemoryCheckpointStore()
	plan = Plan("original", [PlanStep(1, "old step")])
	await store.save(Checkpoint(run_id="r", state={"strategy": "por", "strategy_state": plan_to_state(plan)}))
	model = runpy.run_path(str(EXAMPLES / "09_live_input/run.py"))["DemoModel"]()
	model.release.set()
	async with host(tmp_path, {"por": {"enabled": True}}, model, store) as (_, agent):
		async with asyncio.timeout(3):
			task = asyncio.create_task(collect(agent.resume_stream("r", message="make edits")))
			try:
				await entered.wait()
				assert await agent.send_message("r", "do not write", message_id="steer", session_id="") == "accepted"
			finally:
				release.set()
				events = await task
	assert len(revisions) == 2 and "do not write" in revisions[1]
	assert [e.content for e in events if e.type == EventType.STEP_START] == ["inspect only"]
	assert events[-1].type == EventType.DONE and events[-1].metadata["processed_message_ids"][-1] == "steer"


@pytest.mark.parametrize("phase", ["step_failed", "replan", "step_done"])
async def test_resume_failed_por_step_preserves_outcome(tmp_path, monkeypatch, phase):
	from axc_agent_engine.core.schema import StepStatus

	failed = PlanStep(1, "original write", status=StepStatus.FAILED)
	plan = Plan("finish", [failed, PlanStep(2, "build")])
	state = plan_to_state(plan, current_step_id=2 if phase == "step_done" else 1, phase=phase)
	if phase == "step_done":
		plan.steps.append(PlanStep(3, "read", status=StepStatus.DONE))
		state = plan_to_state(plan, current_step_id=3, phase=phase)
		state["metadata"]["replan_step_id"] = 1
	replanned = []

	async def revise(plan, failed_step_id, llm):
		replanned.append(failed_step_id)
		assert plan.steps[0].status == StepStatus.FAILED
		plan.steps[1].description = "replacement"
		return plan

	monkeypatch.setattr("axc_agent_engine.planning.por_runner.replan", revise)
	model = runpy.run_path(str(EXAMPLES / "09_live_input/run.py"))["DemoModel"]()
	model.release.set()
	store = InMemoryCheckpointStore()
	await store.save(Checkpoint(run_id="r", state={"strategy": "por", "strategy_state": state}))
	async with host(tmp_path, {"por": {"enabled": True}}, model, store) as (_, agent):
		events = await collect(agent.resume_stream("r"))
	assert replanned == ([] if phase == "replan" else [1])
	assert [e.content for e in events if e.type == EventType.STEP_START] == [
		"build" if phase == "replan" else "replacement"
	]
	assert events[-1].type == EventType.DONE


@pytest.mark.parametrize("legacy", [False, True])
async def test_resume_finalized_por_uses_saved_summary(tmp_path, legacy):
	from dataclasses import replace

	model = runpy.run_path(str(EXAMPLES / "09_live_input/run.py"))["DemoModel"]()
	model.release.set()
	store = InMemoryCheckpointStore()
	async with host(tmp_path, {"por": {"enabled": True}}, model, store) as (_, agent):
		original = (await collect(agent.stream("project", metadata={"run_id": "original"})))[-1]
	checkpoint = next(cp for cp in await store.list("original") if cp.state.get("phase") == "finalize")
	state = checkpoint.state
	assert state["strategy_state"]["metadata"]["summary"] == original.content
	if legacy:
		state["strategy_state"]["metadata"].pop("summary")
	await store.save(replace(checkpoint, run_id="replay", state=state))
	async with host(tmp_path, {"por": {"enabled": True}}, object(), store) as (_, agent):
		events = await collect(agent.resume_stream("replay"))
	assert events[-1].type == EventType.DONE and events[-1].content == original.content
	assert not any(e.type == EventType.STEP_START for e in events)


async def test_message_accepted_at_step_start_checkpoint_preempts_dispatch(tmp_path):
	entered, release = asyncio.Event(), asyncio.Event()

	class Store(InMemoryCheckpointStore):
		async def save(self, checkpoint):
			if checkpoint.state.get("phase") == "step_start" and not entered.is_set():
				entered.set()
				await release.wait()
			await super().save(checkpoint)

	model = runpy.run_path(str(EXAMPLES / "09_live_input/run.py"))["DemoModel"]()
	model.release.set()
	async with host(tmp_path, {"por": {"enabled": True}}, model, Store()) as (_, agent):
		async with asyncio.timeout(3):
			task = asyncio.create_task(collect(agent.stream("initial", metadata={"run_id": "r"})))
			try:
				await entered.wait()
				send = asyncio.create_task(agent.send_message("r", "只整理中文", message_id="new", session_id=""))
				await asyncio.sleep(0)
			finally:
				release.set()
				events = await task
			assert await send == "accepted"
	assert [e.content for e in events if e.type == EventType.STEP_START] == ["整理中文文档"]
	assert events[-1].metadata["processed_message_ids"] == ["new"]


@pytest.mark.parametrize("phase", ["step_failed", "finalize"])
async def test_invalid_por_resume_phase_stops_before_dispatch(tmp_path, phase):
	plan = Plan("project", [PlanStep(1, "untouched")])
	state = plan_to_state(plan, current_step_id=2, phase=phase)
	store = InMemoryCheckpointStore()
	await store.save(Checkpoint(run_id="r", state={"strategy": "por", "strategy_state": state}))
	async with host(tmp_path, {"por": {"enabled": True}}, object(), store) as (_, agent):
		events = await collect(agent.resume_stream("r"))
	assert events[-1].type == EventType.ERROR
	assert ("failed step ID" if phase == "step_failed" else "saved summary") in events[-1].content
	assert not any(e.type == EventType.STEP_START for e in events)


async def test_exhausted_replan_budget_keeps_failed_step_terminal(tmp_path):
	from axc_agent_engine.core.constants import MAX_REPLAN_COUNT
	from axc_agent_engine.core.schema import StepStatus

	plan = Plan("goal", [PlanStep(1, "external write", status=StepStatus.FAILED)], replan_count=MAX_REPLAN_COUNT)
	state = plan_to_state(plan, 1, "step_failed")
	store = InMemoryCheckpointStore()
	await store.save(Checkpoint(run_id="r", state={"strategy": "por", "strategy_state": state}))
	async with host(tmp_path, {"por": {"enabled": True}}, object(), store) as (_, agent):
		events = await collect(agent.resume_stream("r"))
	assert events[-1].type == EventType.ERROR and "未完成" in events[-1].content
	assert all(event.type != EventType.STEP_START for event in events)


@pytest.mark.parametrize(
	("fields", "message"),
	[
		({"cursor": "round=2"}, "cursor"),
		({"usage": []}, "usage"),
		({"cursor": {"current_round": "2"}}, "nonnegative integers"),
		({"usage": {"input_tokens": True}}, "nonnegative integers"),
		({"usage": {"output_tokens": -1}}, "nonnegative integers"),
		({"metadata": {"session_id": 123}}, "session_id"),
		({"metadata": []}, "metadata"),
	],
)
async def test_invalid_checkpoint_stops_before_model_or_new_write(tmp_path, fields, message):
	store = InMemoryCheckpointStore()
	await store.save(Checkpoint(run_id="r", state={"strategy": "react", **fields}))
	async with host(tmp_path, {"react": {"enabled": True}}, object(), store) as (_, agent):
		with pytest.raises((TypeError, ValueError), match=message):
			await collect(agent.resume_stream("r"))
	assert len(await store.list("r")) == 1


@pytest.mark.parametrize("complete", [True, False])
async def test_por_resume_at_round_limit_only_summarizes_completed_work(tmp_path, complete):
	from axc_agent_engine.core.schema import RuntimeConfig, StepStatus

	step = PlanStep(
		1, "write", status=StepStatus.DONE if complete else StepStatus.PENDING, result="saved" if complete else ""
	)
	state = plan_to_state(Plan("project", [step]), phase="step_done" if complete else "plan_created")
	store = InMemoryCheckpointStore()
	await store.save(
		Checkpoint(
			run_id="r",
			state={"strategy": "por", "strategy_state": state, "cursor": {"current_round": RuntimeConfig().max_rounds}},
		)
	)
	model = runpy.run_path(str(EXAMPLES / "09_live_input/run.py"))["DemoModel"]()
	model.release.set()
	async with host(tmp_path, {"por": {"enabled": True}}, model, store) as (_, agent):
		events = await collect(agent.resume_stream("r"))
	assert not any(e.type == EventType.STEP_START for e in events)
	assert events[-1].type == (EventType.DONE if complete else EventType.ERROR)
	if not complete:
		assert "轮次限制" in events[-1].content
