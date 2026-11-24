"""Read a real previous-process snapshot through the Agent API. 通过 Agent 入口恢复前一进程的快照。"""

import asyncio
import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from axc_agent_engine import AgentModels, Engine, Event, InMemoryCheckpointStore, PluginRegistry
from axc_agent_engine.plugins.config_schema import config_schema
from axc_agent_engine.plugins.strategy import ExecutionStrategyPlugin
from axc_agent_engine.plugins.testing import assert_stream_contract
from axc_agent_engine.runtime.checkpoint import Checkpoint


class Writer(ExecutionStrategyPlugin):
	name = "versioned"
	config_schema = config_schema(name, "", "", [])

	def create_loop(self, runtime):
		class Loop:
			async def run(self, message, state):
				await runtime.save_state("partial", {"old_key": message})
				yield Event.cancelled("shutdown")

		return Loop()


class Reader(Writer):
	state_version = 2

	def migrate_state(self, state, from_version):
		if from_version != 1:
			return super().migrate_state(state, from_version)
		return {"current_key": state["old_key"]}

	def create_loop(self, runtime):
		class Loop:
			async def run(self, message, state):
				yield Event.done(state["current_key"])

		return Loop()


def host(path, plugin, store, provider):
	registry = PluginRegistry()
	registry.register(plugin)
	engine = Engine(plugin_registry=registry, checkpoint_store=store)
	agent = engine.load_agent_template(str(path)).instantiate(models=AgentModels(default=provider))
	return engine, agent


async def _write_checkpoint(path, destination):
	store = InMemoryCheckpointStore()
	engine, agent = host(path, Writer, store, object())
	try:
		async for _ in agent.stream("saved in another process", metadata={"run_id": "old"}):
			pass
		checkpoint = await store.latest("old")
		destination.write_text(json.dumps(checkpoint.to_dict()))
	finally:
		await engine.close()


async def test_restore_snapshot_from_previous_process(tmp_path, mock_llm):
	path, saved = tmp_path / "agent.yaml", tmp_path / "snapshot.json"
	path.write_text("name: test\nplugins: {versioned: {enabled: true}}")
	await asyncio.to_thread(
		subprocess.run, [sys.executable, "-m", __name__, str(path), str(saved)], check=True, timeout=10
	)
	checkpoint = Checkpoint(**json.loads(saved.read_text()))
	assert checkpoint.state["state_version"] == 1
	store = InMemoryCheckpointStore()
	engine, agent = host(path, Reader, store, mock_llm)
	try:
		for run_id, state in [
			("old", checkpoint.state),
			("unversioned", {k: v for k, v in checkpoint.state.items() if k != "state_version"}),
		]:
			await store.save(replace(checkpoint, run_id=run_id, state=state))
			await assert_stream_contract(agent.resume_stream(run_id), content="saved in another process")
			after = await store.latest(run_id)
			assert after.sequence > checkpoint.sequence
			assert after.state["state_version"] == 2
			assert after.state["strategy_state"] == {"current_key": "saved in another process"}
			assert "state_version" not in after.state["payload"]
		mock_llm.chat.assert_not_awaited()
	finally:
		await engine.close()


@pytest.mark.parametrize("version", [0, -1, True, 1.5, "2", None, 3])
async def test_invalid_or_unsupported_snapshot_fails_before_loop(tmp_path, mock_llm, version, monkeypatch):
	path = tmp_path / "agent.yaml"
	path.write_text("name: test\nplugins: {versioned: {enabled: true}}")
	store = InMemoryCheckpointStore()
	started = []
	monkeypatch.setattr(Reader, "create_loop", lambda *args: started.append("created"))
	checkpoint = Checkpoint(run_id="r", state={"strategy": "versioned", "state_version": version})
	await store.save(checkpoint)
	engine, agent = host(path, Reader, store, mock_llm)
	try:
		with pytest.raises(ValueError, match="state_version|cannot restore state version"):
			await agent.resume("r")
		assert await store.list("r") == [checkpoint]
		assert started == []
	finally:
		await engine.close()


@pytest.mark.parametrize("bad_state,is_migration", [([], False), (None, True)])
async def test_invalid_strategy_state_requires_explicit_migration(
	tmp_path, mock_llm, bad_state, is_migration, monkeypatch
):
	path = tmp_path / "agent.yaml"
	path.write_text("name: test\nplugins: {versioned: {enabled: true}}")
	store = InMemoryCheckpointStore()
	await store.save(
		Checkpoint(run_id="r", state={"strategy": "versioned", "state_version": 1, "strategy_state": bad_state})
	)
	if is_migration:
		monkeypatch.setattr(Reader, "migrate_state", lambda *args: "wrong shape")
	engine, agent = host(path, Reader, store, mock_llm)
	try:
		with pytest.raises(ValueError, match="dict or None"):
			await agent.resume("r")
	finally:
		await engine.close()


def test_strategy_declaration_rejects_boolean_version():
	with pytest.raises(ValueError, match="positive integer"):

		class Invalid(Writer):
			state_version = True


if __name__ == "__main__":
	asyncio.run(_write_checkpoint(Path(sys.argv[1]), Path(sys.argv[2])))


async def test_legacy_round_cannot_run_on_unrelated_host_loop(tmp_path, mock_llm):
	path = tmp_path / "agent.yaml"
	path.write_text("name: test\nplugins: {versioned: {enabled: true}}")
	store = InMemoryCheckpointStore()
	checkpoint = Checkpoint(run_id="legacy", kind="round", state={"messages": []})
	await store.save(checkpoint)
	engine, agent = host(path, Writer, store, mock_llm)
	try:
		with pytest.raises(ValueError, match="unnamed checkpoint"):
			await agent.resume("legacy")
		assert await store.list("legacy") == [checkpoint]
		mock_llm.chat.assert_not_awaited()
	finally:
		await engine.close()
