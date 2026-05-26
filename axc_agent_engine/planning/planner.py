"""POR 计划创建"""
import logging
from dataclasses import dataclass, field

from axc_agent_engine.core.errors import SchemaError
from axc_agent_engine.core.schema import StepStatus

logger = logging.getLogger(__name__)


@dataclass
class PlanStep:
	"""English: This documentation describes the related engine component behavior.
中文：计划步骤"""
	step_id: int
	description: str
	depends_on: list[int] = field(default_factory=list)
	tools_needed: list[str] = field(default_factory=list)
	status: StepStatus = StepStatus.PENDING
	result: str = ""
	error: str = ""


@dataclass
class Plan:
	"""English: This documentation describes the related engine component behavior.
中文：执行计划"""
	goal: str
	steps: list[PlanStep] = field(default_factory=list)
	replan_count: int = 0


def create_plan(goal: str, steps_raw: list[dict]) -> Plan:
	"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
从结构化规划数据创建 Plan。"""
	steps = []
	for s in steps_raw:
		steps.append(PlanStep(
			step_id=s.get("step_id", 0),
			description=s.get("description", ""),
			depends_on=s.get("depends_on", []) or [],
			tools_needed=s.get("tools_needed", []) or [],
		))
	return Plan(goal=goal, steps=steps)


def validate_plan(plan: Plan) -> None:
	"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
校验计划是否为可调度 DAG。"""
	if not plan.goal:
		raise SchemaError("Plan goal cannot be empty")
	seen: set[int] = set()
	for step in plan.steps:
		if step.step_id <= 0:
			raise SchemaError(f"Plan step_id must be positive: {step.step_id}")
		if step.step_id in seen:
			raise SchemaError(f"Duplicate plan step_id: {step.step_id}")
		if not step.description:
			raise SchemaError(f"Plan step {step.step_id} description cannot be empty")
		seen.add(step.step_id)
	for step in plan.steps:
		for dep_id in step.depends_on:
			if dep_id == step.step_id:
				raise SchemaError(f"Plan step {step.step_id} cannot depend on itself")
			if dep_id not in seen:
				raise SchemaError(f"Plan step {step.step_id} depends on missing step {dep_id}")
	_validate_acyclic(plan)


def _validate_acyclic(plan: Plan) -> None:
	steps_by_id = {step.step_id: step for step in plan.steps}
	visiting: set[int] = set()
	visited: set[int] = set()

	def visit(step_id: int) -> None:
		if step_id in visited:
			return
		if step_id in visiting:
			raise SchemaError(f"Plan dependency cycle detected at step {step_id}")
		visiting.add(step_id)
		for dep_id in steps_by_id[step_id].depends_on:
			visit(dep_id)
		visiting.remove(step_id)
		visited.add(step_id)

	for step in plan.steps:
		visit(step.step_id)
