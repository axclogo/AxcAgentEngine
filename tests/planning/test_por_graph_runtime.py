from __future__ import annotations

from axc_agent_engine.core.events import Event, EventType
from axc_agent_engine.planning.graph_runtime import PORGraphRuntime
from axc_agent_engine.planning.graph_state import PORGraphResult, PORGraphState
from axc_agent_engine.planning.planner import Plan, PlanStep


class RecordingPORService:
	def __init__(self) -> None:
		self.calls: list[str] = []

	async def announce_plan(self, state: PORGraphState) -> None:
		self.calls.append("announce")
		state.events.append(Event(type=EventType.PLAN_CREATED, content=state.plan.goal))

	async def select_steps(self, state: PORGraphState) -> None:
		self.calls.append("select")
		state.current_step = state.plan.steps[0]

	async def execute_step(self, state: PORGraphState) -> None:
		self.calls.append("execute_step")
		state.step_result = "step-result"
		state.events.append(Event(type=EventType.STEP_START, step_id=state.current_step.step_id))

	async def observe_step(self, state: PORGraphState) -> None:
		self.calls.append("observe")
		state.events.append(Event(type=EventType.STEP_COMPLETED, step_id=state.current_step.step_id, content=state.step_result))

	async def replan_step(self, state: PORGraphState) -> None:
		self.calls.append("replan")
		state.events.append(Event.done("done"))

	async def finalize_plan(self, state: PORGraphState) -> PORGraphResult:
		self.calls.append("finalize")
		return PORGraphResult(events=state.events)


async def test_por_graph_runtime_runs_explicit_plan_nodes():
	service = RecordingPORService()
	runtime = PORGraphRuntime(service)
	plan = Plan(goal="goal", steps=[PlanStep(step_id=1, description="step")])

	events = []
	async for event in runtime.run(plan, "goal"):
		events.append(event)

	assert service.calls == ["announce", "select", "execute_step", "observe", "replan", "finalize"]
	assert [event.type for event in events] == [
		EventType.PLAN_CREATED,
		EventType.STEP_START,
		EventType.STEP_COMPLETED,
		EventType.DONE,
	]
