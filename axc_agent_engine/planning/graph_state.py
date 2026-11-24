"""State objects for pydantic-graph POR execution. POR 图执行状态。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from axc_agent_engine.core.events import Event
from axc_agent_engine.planning.planner import Plan, PlanStep


@dataclass
class PORGraphState:
	plan: Plan
	emit: Callable[[Event], None]
	user_message: str = ""
	next_steps: list[PlanStep] = field(default_factory=list)
	current_step: PlanStep | None = None
	step_result: str = ""
	goal_achieved: bool = False
	finalized: bool = False
	should_continue: bool = False
	resume_state: dict | None = None
	final_content: str = ""
	error: str = ""


@dataclass
class PORGraphResult:
	final_content: str = ""
	error: str = ""
