"""Tests for durable execution checkpoint primitives."""
from __future__ import annotations

import pytest

from axc_agent_engine.runtime.checkpoint import Checkpoint, CheckpointStatus, CheckpointStore, InMemoryCheckpointStore


async def test_save_and_get_latest_checkpoint():
	store = InMemoryCheckpointStore()
	await store.save(Checkpoint(run_id="run-1", sequence=1, state={"round": 1}))
	await store.save(Checkpoint(run_id="run-1", sequence=2, status=CheckpointStatus.COMPLETED, state={"round": 2}))

	latest = await store.latest("run-1")

	assert latest is not None
	assert latest.sequence == 2
	assert latest.status == CheckpointStatus.COMPLETED
	assert latest.state == {"round": 2}


async def test_list_is_sorted_by_sequence():
	store = InMemoryCheckpointStore()
	await store.save(Checkpoint(run_id="run-1", sequence=3))
	await store.save(Checkpoint(run_id="run-1", sequence=1))
	await store.save(Checkpoint(run_id="run-1", sequence=2))

	items = await store.list("run-1")

	assert [item.sequence for item in items] == [1, 2, 3]


async def test_limits_checkpoints_per_run():
	store = InMemoryCheckpointStore(max_checkpoints_per_run=2)
	for seq in range(4):
		await store.save(Checkpoint(run_id="run-1", sequence=seq))

	items = await store.list("run-1")

	assert [item.sequence for item in items] == [2, 3]


async def test_limits_run_count_lru():
	store = InMemoryCheckpointStore(max_runs=2)
	await store.save(Checkpoint(run_id="run-1", sequence=1))
	await store.save(Checkpoint(run_id="run-2", sequence=1))
	await store.latest("run-1")
	await store.save(Checkpoint(run_id="run-3", sequence=1))

	assert await store.latest("run-1") is not None
	assert await store.latest("run-2") is None
	assert await store.latest("run-3") is not None


async def test_delete_run_and_stats():
	store = InMemoryCheckpointStore()
	await store.save(Checkpoint(run_id="run-1", sequence=1))
	await store.save(Checkpoint(run_id="run-2", sequence=1))
	await store.delete_run("run-1")

	assert await store.latest("run-1") is None
	assert store.stats()["runs"] == 1
	assert store.stats()["checkpoints"] == 1


async def test_checkpoint_filters_and_unbounded_limits():
	store = InMemoryCheckpointStore(max_runs=0, max_checkpoints_per_run=0)
	await store.save(Checkpoint(run_id="run-1", sequence=1, status=CheckpointStatus.RUNNING, kind="round"))
	await store.save(Checkpoint(run_id="run-1", sequence=2, status=CheckpointStatus.COMPLETED, kind="por"))
	await store.save(Checkpoint(run_id="run-2", sequence=1, status=CheckpointStatus.FAILED, kind="round"))
	assert await store.latest("missing") is None
	assert await store.list("missing") == []
	assert await store.list_runs(status=CheckpointStatus.COMPLETED) == ["run-1"]
	assert await store.list_runs(kind="round") == ["run-2"]
	assert await store.list_runs(status=CheckpointStatus.RUNNING) == []
	assert len(await store.list("run-1")) == 2


async def test_requires_run_id():
	store = InMemoryCheckpointStore()
	with pytest.raises(ValueError, match="run_id"):
		await store.save(Checkpoint(sequence=1))


def test_checkpoint_protocol_compliance():
	store = InMemoryCheckpointStore()
	assert isinstance(store, CheckpointStore)


def test_checkpoint_to_dict():
	checkpoint = Checkpoint(run_id="run-1", sequence=1, state={"k": "v"})
	data = checkpoint.to_dict()
	assert data["run_id"] == "run-1"
	assert data["sequence"] == 1
	assert data["state"] == {"k": "v"}
