"""Execution checkpoint primitives.

This module is a storage-neutral foundation for durable execution. Core loops
can persist round, tool, POR, or simulation snapshots through CheckpointStore
without coupling to a database implementation.
中文：此文档说明相关引擎组件的行为。"""
from __future__ import annotations

import asyncio
import time
import uuid
from collections import OrderedDict
from copy import deepcopy
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable


class CheckpointStatus(StrEnum):
	"""Checkpoint lifecycle states.
中文：此文档说明相关引擎组件的行为。"""
	RUNNING = "running"
	COMPLETED = "completed"
	FAILED = "failed"
	INTERRUPTED = "interrupted"


@dataclass(frozen=True)
class Checkpoint:
	"""A durable execution snapshot.
中文：此文档说明相关引擎组件的行为。"""
	id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
	run_id: str = ""
	sequence: int = 0
	status: str = CheckpointStatus.RUNNING
	kind: str = "round"
	state: dict[str, Any] = field(default_factory=dict)
	metadata: dict[str, Any] = field(default_factory=dict)
	created_at: float = field(default_factory=time.time)

	def __post_init__(self) -> None:
		object.__setattr__(self, "state", deepcopy(self.state))
		object.__setattr__(self, "metadata", deepcopy(self.metadata))

	def to_dict(self) -> dict[str, Any]:
		return {
			"id": self.id,
			"run_id": self.run_id,
			"sequence": self.sequence,
			"status": self.status,
			"kind": self.kind,
			"state": deepcopy(self.state),
			"metadata": deepcopy(self.metadata),
			"created_at": self.created_at,
		}


@runtime_checkable
class CheckpointStore(Protocol):
	"""Stores durable execution checkpoints.
中文：此文档说明相关引擎组件的行为。"""
	async def save(self, checkpoint: Checkpoint) -> None: ...
	async def latest(self, run_id: str) -> Checkpoint | None: ...
	async def list(self, run_id: str) -> list[Checkpoint]: ...
	async def list_runs(self, status: str | None = None, kind: str | None = None) -> list[str]: ...
	async def delete_run(self, run_id: str) -> None: ...


class InMemoryCheckpointStore:
	"""Bounded in-memory checkpoint store for local development and tests.
中文：此文档说明相关引擎组件的行为。"""

	def __init__(self, max_runs: int = 1000, max_checkpoints_per_run: int = 1000) -> None:
		self._runs: OrderedDict[str, list[Checkpoint]] = OrderedDict()
		self._max_runs = max_runs
		self._max_checkpoints_per_run = max_checkpoints_per_run
		self._lock = asyncio.Lock()

	async def save(self, checkpoint: Checkpoint) -> None:
		if not checkpoint.run_id:
			raise ValueError("checkpoint.run_id is required")
		async with self._lock:
			items = self._runs.setdefault(checkpoint.run_id, [])
			items.append(checkpoint)
			items.sort(key=lambda item: item.sequence)
			if self._max_checkpoints_per_run > 0 and len(items) > self._max_checkpoints_per_run:
				del items[:len(items) - self._max_checkpoints_per_run]
			self._runs.move_to_end(checkpoint.run_id)
			while self._max_runs > 0 and len(self._runs) > self._max_runs:
				self._runs.popitem(last=False)

	async def latest(self, run_id: str) -> Checkpoint | None:
		async with self._lock:
			items = self._runs.get(run_id)
			if not items:
				return None
			self._runs.move_to_end(run_id)
			return items[-1]

	async def list(self, run_id: str) -> list[Checkpoint]:
		async with self._lock:
			items = self._runs.get(run_id, [])
			if items:
				self._runs.move_to_end(run_id)
			return list(items)

	async def list_runs(self, status: str | None = None, kind: str | None = None) -> list[str]:
		async with self._lock:
			matches: list[str] = []
			for run_id, items in self._runs.items():
				if not items:
					continue
				latest = items[-1]
				if status is not None and str(latest.status) != str(status):
					continue
				if kind is not None and latest.kind != kind:
					continue
				matches.append(run_id)
			return matches

	async def delete_run(self, run_id: str) -> None:
		async with self._lock:
			self._runs.pop(run_id, None)

	def stats(self) -> dict[str, int]:
		return {
			"runs": len(self._runs),
			"checkpoints": sum(len(items) for items in self._runs.values()),
			"max_runs": self._max_runs,
			"max_checkpoints_per_run": self._max_checkpoints_per_run,
		}
