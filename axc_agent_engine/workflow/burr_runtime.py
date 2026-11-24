"""Track workflow transitions through optional Burr actions. 通过可选的 Burr 动作记录工作流状态转移。"""

from __future__ import annotations

from typing import Any

from axc_agent_engine.workflow.memory_runtime import MemoryWorkflowRuntime, WorkflowRecord
from axc_agent_engine.workflow.protocols import WorkflowResumePlan, WorkflowResumeRequest, WorkflowStatus


class BurrWorkflowRuntime(MemoryWorkflowRuntime):
	"""Record workflow transitions with Burr. 使用 Burr 记录工作流状态转移。"""

	def __init__(self) -> None:
		super().__init__()
		self._burr = self._load_burr()

	def _start_resume(self, request: WorkflowResumeRequest, plan: WorkflowResumePlan) -> None:
		metadata = {**request.metadata, **plan.metadata, "message": request.message, "kind": plan.kind}
		self._set_state(request.run_id, WorkflowStatus.RUNNING, metadata)
		self._run_burr_action("resume", request.run_id, metadata)

	def _set_state(self, run_id: str, status: WorkflowStatus, values: dict[str, Any] | None = None) -> None:
		State = self._burr["State"]
		state = State(values or {}).update(status=status.value)
		self._records[run_id] = WorkflowRecord(status=status, state=dict(state.get_all()))

	def _run_burr_action(self, action_name: str, run_id: str, values: dict[str, Any]) -> None:
		from burr.core import State

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
		self._records[run_id] = WorkflowRecord(
			status=self._records[run_id].status,
			state=dict(state.get_all()),
		)

	def _load_burr(self) -> dict[str, Any]:
		try:
			from burr.core import ApplicationBuilder, State, action
		except ImportError as e:
			raise RuntimeError("BurrWorkflowRuntime requires installing axc-agent-engine[workflow]") from e
		return {
			"ApplicationBuilder": ApplicationBuilder,
			"State": State,
			"action": action,
		}
