"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
从轨迹和事件中挖掘重复出现的 Agent 失败模式。
Mine recurring Agent failure patterns from traces and events."""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class FailureRecord:
	"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
一条来自日志、事件、轨迹或评测结果的失败观测。
	One failure observation from logs, events, traces, or eval results.
	"""
	record_id: str
	agent_name: str = ""
	category: str = ""
	message: str = ""
	tool_name: str = ""
	task: str = ""
	severity: float = 0.5
	metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class FailureCluster:
	"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
聚类后的失败模式。
	Clustered failure pattern.
	"""
	key: str
	category: str
	count: int
	severity: float
	examples: list[FailureRecord] = field(default_factory=list)
	affected_agents: set[str] = field(default_factory=set)
	suggested_action: str = ""


@dataclass(frozen=True)
class FailureMiningReport:
	"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
挖掘出的失败聚类摘要。
	Summary of mined failure clusters.
	"""
	clusters: list[FailureCluster] = field(default_factory=list)
	category_counts: dict[str, int] = field(default_factory=dict)
	metadata: dict[str, Any] = field(default_factory=dict)


class FailureReportBuilder:
	"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
把失败记录分组成确定性聚类和报告元数据。
	Groups failure records into deterministic clusters and report metadata.
	"""

	def build(self, records: list[FailureRecord], min_count: int = 1) -> FailureMiningReport:
		grouped: dict[tuple[str, str, str], list[FailureRecord]] = defaultdict(list)
		category_counts: Counter[str] = Counter()
		for record in records:
			category = record.category or infer_failure_category(record.message)
			category_counts[category] += 1
			key = (category, record.tool_name, _signature(record.message or record.task))
			grouped[key].append(record)
		clusters = self._clusters(grouped, min_count)
		return FailureMiningReport(
			clusters=clusters,
			category_counts=dict(category_counts),
			metadata={"records": len(records), "clusters": len(clusters)},
		)

	def _clusters(
		self,
		grouped: dict[tuple[str, str, str], list[FailureRecord]],
		min_count: int,
	) -> list[FailureCluster]:
		clusters: list[FailureCluster] = []
		for (category, tool_name, signature), items in grouped.items():
			if len(items) < min_count:
				continue
			avg_severity = sum(item.severity for item in items) / len(items)
			label = "|".join(part for part in (category, tool_name, signature) if part)
			clusters.append(FailureCluster(
				key=label,
				category=category,
				count=len(items),
				severity=round(avg_severity, 3),
				examples=items[:5],
				affected_agents={item.agent_name for item in items if item.agent_name},
				suggested_action=suggest_action(category, tool_name),
			))
		clusters.sort(key=lambda item: (item.count, item.severity), reverse=True)
		return clusters


def infer_failure_category(message: str) -> str:
	text = (message or "").lower()
	if any(term in text for term in ("timeout", "timed out", "deadline")):
		return "timeout"
	if any(term in text for term in ("permission", "capability", "not allowed", "denied", "blocked")):
		return "policy"
	if any(term in text for term in ("json", "schema", "parse", "invalid format")):
		return "format"
	if any(term in text for term in ("tool", "command", "exit code", "subprocess")):
		return "tool"
	if any(term in text for term in ("rate limit", "429", "quota")):
		return "provider_limit"
	if any(term in text for term in ("context", "token", "too long", "maximum")):
		return "context_budget"
	return "unknown"


def suggest_action(category: str, tool_name: str = "") -> str:
	if category == "timeout":
		return "Add timeout budget, progress checkpoints, or split the task into smaller steps."
	if category == "policy":
		return "Review capability policy and confirm the Agent has only the required permissions."
	if category == "format":
		return "Add output_format validation or stricter schema repair around this path."
	if category == "tool":
		return f"Add contract tests and better error mapping for tool '{tool_name}'." if tool_name else "Add tool contract tests and structured error mapping."
	if category == "provider_limit":
		return "Tune provider routing, fallback, rate limits, or retry backoff."
	if category == "context_budget":
		return "Reduce prompt/context size or adjust compression boundaries."
	return "Inspect examples and promote recurring failures into eval cases."


def _signature(text: str) -> str:
	words = [word.strip(".,:;()[]{}").lower() for word in (text or "").split()]
	words = [word for word in words if len(word) > 3]
	return " ".join(words[:8])
