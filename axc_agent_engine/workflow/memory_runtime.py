"""In-process workflow runtime.

This keeps the default engine lightweight while the public pause/resume path is
already routed through the workflow boundary.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass

from axc_agent_engine.core.events import Event, EventType
from axc_agent_engine.workflow.protocols import (
	WorkflowResumeRequest,
	WorkflowRunRequest,
	WorkflowStatus,
)
from axc_agent_engine.workflow.state import prepare_resume_plan


@dataclass
class _WorkflowRecord:
	status: WorkflowStatus
	reason: str = ""


class MemoryWorkflowRuntime:
	"""Minimal workflow runtime used when no durable adapter is configured."""

	def __init__(self) -> None:
		self._runs: dict[str, _WorkflowRecord] = {}

	async def run(self, request: WorkflowRunRequest) -> AsyncIterator[Event]:
		self._runs[request.run_id] = _WorkflowRecord(status=WorkflowStatus.RUNNING)
		async for event in request.handler():
			self._update_status(request.run_id, event)
			yield event

	async def resume(self, request: WorkflowResumeRequest) -> AsyncIterator[Event]:
		plan = await prepare_resume_plan(request)
		if plan is None:
			yield Event.error(f"No checkpoint found for run_id={request.run_id}")
			return
		self._runs[request.run_id] = _WorkflowRecord(status=WorkflowStatus.RUNNING)
		async for event in request.handler(plan):
			self._update_status(request.run_id, event)
			yield event

	async def pause(self, run_id: str, reason: str = "") -> None:
		self._runs[run_id] = _WorkflowRecord(status=WorkflowStatus.PAUSED, reason=reason)

	async def status(self, run_id: str) -> WorkflowStatus:
		record = self._runs.get(run_id)
		return record.status if record else WorkflowStatus.MISSING

	def _update_status(self, run_id: str, event: Event) -> None:
		if event.type == EventType.DONE:
			self._runs[run_id] = _WorkflowRecord(status=WorkflowStatus.COMPLETED)
		elif event.type == EventType.ERROR:
			self._runs[run_id] = _WorkflowRecord(status=WorkflowStatus.FAILED)
