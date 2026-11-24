"""In-process workflow driver. 进程内工作流驱动。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import Any

from axc_agent_engine.core.events import TERMINAL_EVENTS, Event, EventType
from axc_agent_engine.runtime.tasks import managed_stream
from axc_agent_engine.workflow.protocols import (
	WorkflowResumePlan,
	WorkflowResumeRequest,
	WorkflowRunRequest,
	WorkflowStatus,
)
from axc_agent_engine.workflow.state import prepare_resume_plan


@dataclass
class WorkflowRecord:
	status: WorkflowStatus
	state: dict[str, Any]


class MemoryWorkflowRuntime:
	"""Track handler lifecycle in memory. 在内存中记录工作流状态。"""

	def __init__(self) -> None:
		self._records: dict[str, WorkflowRecord] = {}

	def run(self, request: WorkflowRunRequest) -> AsyncGenerator[Event, None]:
		return self._events(request)

	def resume(self, request: WorkflowResumeRequest) -> AsyncGenerator[Event, None]:
		return self._events(request)

	async def _events(self, request: WorkflowRunRequest | WorkflowResumeRequest) -> AsyncGenerator[Event, None]:
		run_id = request.run_id
		terminal = None
		try:
			if isinstance(request, WorkflowResumeRequest):
				plan = await prepare_resume_plan(request)
				if plan is None:
					terminal = Event.error(f"No checkpoint found for run_id={run_id}")
					yield terminal
					return
				self._start_resume(request, plan)
				events = request.handler(plan)
			else:
				self._set_state(run_id, WorkflowStatus.RUNNING, dict(request.metadata))
				events = request.handler()
			async with managed_stream(events):
				async for event in events:
					if event.type in TERMINAL_EVENTS:
						terminal = event
						break
					yield event
				else:
					raise RuntimeError("Workflow handler ended without a terminal event")
		except (GeneratorExit, asyncio.CancelledError):
			if terminal is None:
				self._set_state(run_id, WorkflowStatus.PAUSED, {"reason": "stream closed"})
			raise
		except Exception as exc:
			self._set_state(run_id, WorkflowStatus.FAILED, {"error": str(exc)})
			raise
		status, key = {
			EventType.DONE: (WorkflowStatus.COMPLETED, "result"),
			EventType.ERROR: (WorkflowStatus.FAILED, "error"),
			EventType.CANCELLED: (WorkflowStatus.PAUSED, "reason"),
		}[terminal.type]
		self._set_state(run_id, status, {key: terminal.content})
		yield terminal

	def _start_resume(self, request: WorkflowResumeRequest, plan: WorkflowResumePlan) -> None:
		self._set_state(request.run_id, WorkflowStatus.RUNNING, dict(request.metadata))

	def _set_state(self, run_id: str, status: WorkflowStatus, values: dict) -> None:
		self._records[run_id] = WorkflowRecord(status, values)

	async def pause(self, run_id: str, reason: str = "") -> None:
		self._set_state(run_id, WorkflowStatus.PAUSED, {"reason": reason})

	async def status(self, run_id: str) -> WorkflowStatus:
		record = self._records.get(run_id)
		return record.status if record else WorkflowStatus.MISSING
