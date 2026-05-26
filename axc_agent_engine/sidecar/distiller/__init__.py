"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
从 Agent 执行轨迹中蒸馏可复用指导。
Distill reusable guidance from Agent execution traces."""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class TraceStep:
	"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
用于蒸馏的最小可回放步骤。
	Minimal replayable step for distillation.
	"""
	role: str
	content: str = ""
	tool_name: str = ""
	success: bool = True
	error: str = ""
	metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ExecutionTrace:
	"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
宿主提供的执行轨迹。
	Host-provided execution trace.
	"""
	trace_id: str
	task: str = ""
	agent_name: str = ""
	steps: list[TraceStep] = field(default_factory=list)
	score: float = 0.0
	success: bool = False
	metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DistilledRule:
	"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
一条蒸馏出的行为规则。
	One distilled behavior rule.
	"""
	text: str
	confidence: float
	evidence: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class DistillationReport:
	"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
适用于 prompt、skill 或 policy 更新的蒸馏结果。
	Distillation result suitable for prompt, skill, or policy updates.
	"""
	rules: list[DistilledRule] = field(default_factory=list)
	tool_preferences: list[str] = field(default_factory=list)
	anti_patterns: list[DistilledRule] = field(default_factory=list)
	skill_candidates: list[str] = field(default_factory=list)
	metadata: dict[str, Any] = field(default_factory=dict)


class DistillationReportBuilder:
	"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
从轨迹聚合中构建确定性的蒸馏报告。
	Builds deterministic distillation reports from trace aggregates.
	"""

	def build(self, traces: list[ExecutionTrace], min_success_score: float = 0.7) -> DistillationReport:
		successes = [trace for trace in traces if trace.success and trace.score >= min_success_score]
		failures = [trace for trace in traces if not trace.success or trace.score < min_success_score]
		tool_counts: Counter[str] = Counter()
		tool_success: Counter[str] = Counter()
		task_patterns: Counter[str] = Counter()
		failure_errors: Counter[str] = Counter()
		evidence_by_tool: dict[str, list[str]] = defaultdict(list)
		for trace in traces:
			task_key = _task_family(trace.task)
			if task_key:
				task_patterns[task_key] += 1
			for step in trace.steps:
				if step.tool_name:
					tool_counts[step.tool_name] += 1
					if trace in successes and step.success:
						tool_success[step.tool_name] += 1
						evidence_by_tool[step.tool_name].append(trace.trace_id)
				if step.error:
					failure_errors[_compact(step.error)] += 1
		rules = self._rules(traces, tool_counts, tool_success, task_patterns, evidence_by_tool)
		anti_patterns = [
			DistilledRule(
				text=f"Avoid repeating failure pattern: {error}",
				confidence=round(count / max(1, len(failures)), 3),
				evidence=[trace.trace_id for trace in failures if any(_compact(step.error) == error for step in trace.steps)],
			)
			for error, count in failure_errors.most_common(5)
			if error
		]
		tool_preferences = [tool for tool, _ in tool_success.most_common()]
		skill_candidates = [
			f"Create a skill for recurring '{task}' tasks"
			for task, count in task_patterns.most_common(5)
			if count >= 3
		]
		return DistillationReport(
			rules=rules,
			tool_preferences=tool_preferences,
			anti_patterns=anti_patterns,
			skill_candidates=skill_candidates,
			metadata={"traces": len(traces), "successes": len(successes), "failures": len(failures)},
		)

	def _rules(
		self,
		traces: list[ExecutionTrace],
		tool_counts: Counter[str],
		tool_success: Counter[str],
		task_patterns: Counter[str],
		evidence_by_tool: dict[str, list[str]],
	) -> list[DistilledRule]:
		rules: list[DistilledRule] = []
		for tool, count in tool_counts.most_common():
			success_count = tool_success[tool]
			if count >= 2 and success_count > 0:
				confidence = success_count / count
				rules.append(DistilledRule(
					text=f"Prefer tool '{tool}' for similar tasks when its inputs are available.",
					confidence=round(confidence, 3),
					evidence=evidence_by_tool[tool][:5],
				))
		for task, count in task_patterns.most_common(5):
			if count >= 2:
				rules.append(DistilledRule(
					text=f"Treat '{task}' tasks as a recurring task family and route them through a repeatable checklist.",
					confidence=round(min(1.0, count / max(1, len(traces))), 3),
					evidence=[trace.trace_id for trace in traces if _task_family(trace.task) == task][:5],
				))
		return rules


def _compact(text: str, limit: int = 120) -> str:
	value = " ".join((text or "").split())
	return value[:limit]


def _task_family(task: str) -> str:
	words = [word.lower() for word in task.split() if len(word) > 2]
	return " ".join(words[:4])
