"""Workflow runtime contracts.

The engine keeps ReAct and POR independent from workflow persistence. Pause and
resume entry points pass through this small protocol so Apache Burr can be used
as an adapter without becoming a core execution dependency.
"""
from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

from axc_agent_engine.core.events import Event
from axc_agent_engine.runtime.checkpoint import CheckpointStore

WorkflowRunHandler = Callable[[], AsyncIterator[Event]]
WorkflowResumeHandler = Callable[["WorkflowResumePlan"], AsyncIterator[Event]]


class WorkflowStatus(StrEnum):
	RUNNING = "running"
	PAUSED = "paused"
	COMPLETED = "completed"
	FAILED = "failed"
	MISSING = "missing"


@dataclass(frozen=True)
class WorkflowRunRequest:
	run_id: str
	handler: WorkflowRunHandler
	metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class WorkflowResumeRequest:
	run_id: str
	message: str
	handler: WorkflowResumeHandler
	checkpoint_store: CheckpointStore | None = None
	metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class WorkflowResumePlan:
	run_id: str
	kind: str
	session_id: str = ""
	snapshot: dict[str, Any] = field(default_factory=dict)
	metadata: dict[str, Any] = field(default_factory=dict)


class WorkflowRuntime(Protocol):
	async def run(self, request: WorkflowRunRequest) -> AsyncIterator[Event]: ...
	async def resume(self, request: WorkflowResumeRequest) -> AsyncIterator[Event]: ...
	async def pause(self, run_id: str, reason: str = "") -> None: ...
	async def status(self, run_id: str) -> WorkflowStatus: ...
