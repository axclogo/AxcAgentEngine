"""State objects for pydantic-graph POR execution.
中文：此文档说明相关引擎组件的行为。"""
from __future__ import annotations

from dataclasses import dataclass, field

from axc_agent_engine.core.events import Event
from axc_agent_engine.planning.planner import Plan, PlanStep


@dataclass
class PORGraphState:
	plan: Plan
	user_message: str = ""
	next_steps: list[PlanStep] = field(default_factory=list)
	current_step: PlanStep | None = None
	step_result: str = ""
	goal_achieved: bool = False
	finalized: bool = False
	should_continue: bool = False
	resumed: bool = False
	events: list[Event] = field(default_factory=list)
	final_content: str = ""
	error: str = ""


@dataclass
class PORGraphResult:
	events: list[Event] = field(default_factory=list)
	final_content: str = ""
	error: str = ""
