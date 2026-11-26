"""Execution checkpoint primitives.

This module is a storage-neutral foundation for durable execution. Core loops
can persist round, tool, POR, or simulation snapshots through CheckpointStore
without coupling to a database implementation.
持久执行的检查点接口。"""

from __future__ import annotations

import asyncio
import builtins
import time
import uuid
from collections import OrderedDict
from copy import deepcopy
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable


class CheckpointStatus(StrEnum):
	"""Checkpoint lifecycle states. 检查点生命周期状态。"""

	RUNNING = "running"
	COMPLETED = "completed"
	FAILED = "failed"
	INTERRUPTED = "interrupted"


@dataclass(frozen=True)
class Checkpoint:
	"""A durable execution snapshot. 可持久化的执行快照。"""

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

	@property
	def forkable(self) -> bool:
		"""An exact cut with portable plugin state and settled writes. 插件状态可移植且写入已确定的切点。"""
		return self.fork_blocker is None

	@property
	def fork_blocker(self) -> str | None:
		if pending := self.state.get("unsettled_writes"):
			return f"Fork requires reconciliation of unsettled writes: {list(pending)}"
		if reason := self.state.get("fork_blocker"):
			return reason
		if not isinstance(self.state.get("fork_state"), dict):
			return f"Checkpoint is not a forkable cut (phase={self.state.get('phase')})"
		return None

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


@dataclass(frozen=True)
class ForkedRun:
	"""New identities for an independently resumable branch. 可独立恢复的分支标识。"""

	run_id: str
	session_id: str


@dataclass(frozen=True)
class ForkFailure:
	"""Structured identifiers for a failed fork. 失败分叉的结构化定位信息。"""

	branch_run_id: str
	branch_session_id: str
	source_run_id: str
	source_checkpoint_id: str
	cause: str
	rollback_error: str = ""
	checkpoint_written: bool | None = None
	session_written: bool | None = None
	cleaned: bool = False


@dataclass(frozen=True)
class ForkCleanupResult:
	"""Outcome of cleaning a failed fork. 清理失败分叉的结果。"""

	branch_run_id: str
	branch_session_id: str
	cleaned: bool
	checkpoint_removed: bool
	session_removed: bool
	errors: tuple[str, ...] = ()


def attach_fork_failure(error: BaseException, failure: ForkFailure) -> None:
	"""Attach structured fork context to the original exception. 把结构化分叉信息附到原始异常上。"""
	error.fork_failure = failure  # type: ignore[attr-defined]


def fork_failure_of(error: BaseException) -> ForkFailure | None:
	"""Read structured fork context from an exception. 从异常读取结构化分叉信息。"""
	value = getattr(error, "fork_failure", None)
	return value if isinstance(value, ForkFailure) else None


@runtime_checkable
class CheckpointStore(Protocol):
	"""Stores durable execution checkpoints. 持久化执行检查点的存储接口。"""

	async def save(self, checkpoint: Checkpoint) -> None: ...
	async def latest(self, run_id: str) -> Checkpoint | None: ...
	async def list(self, run_id: str) -> list[Checkpoint]: ...
	async def list_runs(self, status: str | None = None, kind: str | None = None) -> builtins.list[str]: ...
	async def delete_run(self, run_id: str) -> None: ...


class InMemoryCheckpointStore:
	"""Bounded in-memory checkpoint store for local development and tests. 供本地开发和测试使用的有界内存检查点存储。"""

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
			items.append(deepcopy(checkpoint))
			items.sort(key=lambda item: item.sequence)
			if self._max_checkpoints_per_run > 0 and len(items) > self._max_checkpoints_per_run:
				del items[: len(items) - self._max_checkpoints_per_run]
			self._runs.move_to_end(checkpoint.run_id)
			while self._max_runs > 0 and len(self._runs) > self._max_runs:
				self._runs.popitem(last=False)

	async def latest(self, run_id: str) -> Checkpoint | None:
		async with self._lock:
			items = self._runs.get(run_id)
			if not items:
				return None
			self._runs.move_to_end(run_id)
			return deepcopy(items[-1])

	async def list(self, run_id: str) -> list[Checkpoint]:
		async with self._lock:
			items = self._runs.get(run_id, [])
			if items:
				self._runs.move_to_end(run_id)
			return deepcopy(items)

	async def list_runs(self, status: str | None = None, kind: str | None = None) -> builtins.list[str]:
		async with self._lock:
			matches: list[str] = []
			for run_id, items in self._runs.items():
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
