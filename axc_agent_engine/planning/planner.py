"""POR 计划创建"""

from dataclasses import dataclass, field
from graphlib import CycleError, TopologicalSorter

from axc_agent_engine.core.errors import SchemaError
from axc_agent_engine.core.schema import StepStatus


@dataclass
class PlanStep:
	"""Plan step. 计划步骤"""

	step_id: int
	description: str
	depends_on: list[int] = field(default_factory=list)
	tools_needed: list[str] = field(default_factory=list)
	status: StepStatus = StepStatus.PENDING
	result: str = ""
	error: str = ""


@dataclass
class Plan:
	"""Execution plan. 执行计划"""

	goal: str
	steps: list[PlanStep] = field(default_factory=list)
	replan_count: int = 0


def create_plan(goal: str, steps_raw: list[dict]) -> Plan:
	"""Build a plan from structured data.
	从结构化规划数据创建 Plan。"""
	steps = []
	for s in steps_raw:
		if not isinstance(s, dict):
			raise SchemaError("Plan steps must be objects")
		steps.append(
			PlanStep(
				step_id=s.get("step_id", 0),
				description=s.get("description", ""),
				depends_on=s.get("depends_on", []),
				tools_needed=s.get("tools_needed", []),
			)
		)
	return Plan(goal=goal, steps=steps)


def validate_plan(plan: Plan) -> None:
	"""Check that the plan is a schedulable DAG.
	校验计划是否为可调度 DAG。"""
	if not isinstance(plan.goal, str) or not plan.goal.strip():
		raise SchemaError("Plan goal cannot be empty")
	seen: set[int] = set()
	for step in plan.steps:
		if type(step.step_id) is not int or step.step_id <= 0:
			raise SchemaError(f"Plan step_id must be positive: {step.step_id}")
		if step.step_id in seen:
			raise SchemaError(f"Duplicate plan step_id: {step.step_id}")
		if not isinstance(step.description, str) or not step.description.strip():
			raise SchemaError(f"Plan step {step.step_id} description cannot be empty")
		if not isinstance(step.depends_on, list) or any(type(dep) is not int for dep in step.depends_on):
			raise SchemaError(f"Plan step {step.step_id} requires integer dependency IDs")
		if not isinstance(step.tools_needed, list) or any(not isinstance(tool, str) for tool in step.tools_needed):
			raise SchemaError(f"Plan step {step.step_id} requires a list of tool names")
		seen.add(step.step_id)
	for step in plan.steps:
		for dep_id in step.depends_on:
			if dep_id == step.step_id:
				raise SchemaError(f"Plan step {step.step_id} cannot depend on itself")
			if dep_id not in seen:
				raise SchemaError(f"Plan step {step.step_id} depends on missing step {dep_id}")
	try:
		TopologicalSorter({step.step_id: step.depends_on for step in plan.steps}).prepare()
	except CycleError as exc:
		raise SchemaError(f"Plan dependency cycle detected: {exc.args[1]}") from exc
