"""Storage-neutral execution recovery helpers."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from axc_agent_engine.runtime.checkpoint import Checkpoint, CheckpointStatus, CheckpointStore


RECOVERABLE_STATUSES = {CheckpointStatus.RUNNING, CheckpointStatus.INTERRUPTED}


@dataclass(frozen=True)
class RecoverableRun:
	"""Latest recoverable checkpoint for one run."""
	run_id: str
	kind: str
	status: str
	sequence: int
	phase: str = ""
	agent_name: str = ""
	session_id: str = ""
	checkpoint: Checkpoint | None = None
	metadata: dict[str, Any] = field(default_factory=dict)


class ExecutionRecoveryService:
	"""Find and mark recoverable execution checkpoints.

	The service intentionally knows only about CheckpointStore. Host services can
	map run_id/session_id/agent_name to API sessions, WebSocket sessions, or DB
	rows outside the engine.
	"""

	def __init__(self, store: CheckpointStore) -> None:
		self.store = store

	async def list_recoverable(self, kind: str | None = None) -> list[RecoverableRun]:
		runs: list[RecoverableRun] = []
		for status in sorted(RECOVERABLE_STATUSES, key=str):
			for run_id in await self.store.list_runs(status=str(status), kind=kind):
				checkpoint = await self.store.latest(run_id)
				if checkpoint:
					runs.append(_recoverable_from_checkpoint(checkpoint))
		runs.sort(key=lambda item: item.sequence)
		return runs

	async def latest(self, run_id: str) -> RecoverableRun | None:
		checkpoint = await self.store.latest(run_id)
		if not checkpoint or str(checkpoint.status) not in {str(s) for s in RECOVERABLE_STATUSES}:
			return None
		return _recoverable_from_checkpoint(checkpoint)

	async def mark_interrupted(self, run_id: str, reason: str = "") -> Checkpoint | None:
		checkpoint = await self.store.latest(run_id)
		if not checkpoint:
			return None
		interrupted = Checkpoint(
			run_id=checkpoint.run_id,
			sequence=checkpoint.sequence + 1,
			status=CheckpointStatus.INTERRUPTED,
			kind=checkpoint.kind,
			state=dict(checkpoint.state),
			metadata={**dict(checkpoint.metadata), "interrupted_reason": reason},
		)
		await self.store.save(interrupted)
		return interrupted

	async def mark_failed(self, run_id: str, reason: str = "") -> Checkpoint | None:
		checkpoint = await self.store.latest(run_id)
		if not checkpoint:
			return None
		failed = Checkpoint(
			run_id=checkpoint.run_id,
			sequence=checkpoint.sequence + 1,
			status=CheckpointStatus.FAILED,
			kind=checkpoint.kind,
			state={**dict(checkpoint.state), "error": reason or checkpoint.state.get("error", "")},
			metadata=dict(checkpoint.metadata),
		)
		await self.store.save(failed)
		return failed


def _recoverable_from_checkpoint(checkpoint: Checkpoint) -> RecoverableRun:
	state_metadata = checkpoint.state.get("metadata", {})
	metadata = state_metadata if isinstance(state_metadata, dict) else {}
	return RecoverableRun(
		run_id=checkpoint.run_id,
		kind=checkpoint.kind,
		status=str(checkpoint.status),
		sequence=checkpoint.sequence,
		phase=str(checkpoint.metadata.get("phase") or checkpoint.state.get("phase") or ""),
		agent_name=str(metadata.get("agent_name") or checkpoint.metadata.get("agent_name") or ""),
		session_id=str(metadata.get("session_id") or checkpoint.metadata.get("session_id") or ""),
		checkpoint=checkpoint,
		metadata=dict(checkpoint.metadata),
	)
