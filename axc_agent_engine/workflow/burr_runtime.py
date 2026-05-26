"""Apache Burr workflow runtime adapter.

Burr remains optional and isolated in this module. Importing the main engine
does not import Burr; deployers choose this runtime when they want durable
workflow state around pause/resume.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from axc_agent_engine.core.events import Event, EventType
from axc_agent_engine.workflow.protocols import (
	WorkflowResumeRequest,
	WorkflowRunRequest,
	WorkflowStatus,
)
from axc_agent_engine.workflow.state import prepare_resume_plan


@dataclass
class _BurrRecord:
	status: WorkflowStatus
	state: dict[str, Any]


class BurrWorkflowRuntime:
	"""WorkflowRuntime implementation backed by Apache Burr state objects."""

	def __init__(self) -> None:
		self._records: dict[str, _BurrRecord] = {}
		self._burr = self._load_burr()

	async def run(self, request: WorkflowRunRequest) -> AsyncIterator[Event]:
		self._set_state(request.run_id, WorkflowStatus.RUNNING, request.metadata)
		async for event in request.handler():
			self._record_event(request.run_id, event)
			yield event

	async def resume(self, request: WorkflowResumeRequest) -> AsyncIterator[Event]:
		plan = await prepare_resume_plan(request)
		if plan is None:
			yield Event.error(f"No checkpoint found for run_id={request.run_id}")
			return
		metadata = dict(request.metadata)
		metadata.update(plan.metadata)
		metadata["message"] = request.message
		metadata["kind"] = plan.kind
		self._set_state(request.run_id, WorkflowStatus.RUNNING, metadata)
		self._run_burr_action("resume", request.run_id, metadata)
		async for event in request.handler(plan):
			self._record_event(request.run_id, event)
			yield event

	async def pause(self, run_id: str, reason: str = "") -> None:
		self._set_state(run_id, WorkflowStatus.PAUSED, {"reason": reason})

	async def status(self, run_id: str) -> WorkflowStatus:
		record = self._records.get(run_id)
		return record.status if record else WorkflowStatus.MISSING

	def _set_state(self, run_id: str, status: WorkflowStatus, values: dict[str, Any] | None = None) -> None:
		State = self._burr["State"]
		state = State(values or {}).update(status=status.value)
		self._records[run_id] = _BurrRecord(status=status, state=dict(state.get_all()))

	def _record_event(self, run_id: str, event: Event) -> None:
		if event.type == EventType.DONE:
			self._set_state(run_id, WorkflowStatus.COMPLETED, {"result": event.content})
		elif event.type == EventType.ERROR:
			self._set_state(run_id, WorkflowStatus.FAILED, {"error": event.content})

	def _run_burr_action(self, action_name: str, run_id: str, values: dict[str, Any]) -> None:
		State = self._burr["State"]
		ApplicationBuilder = self._burr["ApplicationBuilder"]
		action = self._burr["action"]

		@action(reads=[], writes=["action_name", "run_id"])
		def workflow_action(state: State) -> State:
			return state.update(action_name=action_name, run_id=run_id)

		app = (
			ApplicationBuilder()
			.with_state(**values)
			.with_actions(workflow_action)
			.with_entrypoint("workflow_action")
			.build()
		)
		_, _, state = app.run(halt_after=["workflow_action"])
		record = self._records.get(run_id)
		self._records[run_id] = _BurrRecord(
			status=record.status if record else WorkflowStatus.RUNNING,
			state=dict(state.get_all()),
		)

	def _load_burr(self) -> dict[str, Any]:
		try:
			from burr.core import ApplicationBuilder, State, action, expr
		except ImportError as e:
			raise RuntimeError("BurrWorkflowRuntime requires installing axc-agent-engine[workflow]") from e
		return {
			"ApplicationBuilder": ApplicationBuilder,
			"State": State,
			"action": action,
			"expr": expr,
		}
