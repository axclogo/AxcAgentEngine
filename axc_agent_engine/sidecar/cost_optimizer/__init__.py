"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
宿主侧成本分析和优化建议。
Host-side cost analysis and optimization suggestions."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class CostSample:
	"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
一条观测到的执行成本样本。
	One observed execution cost sample.
	"""
	sample_id: str
	agent_name: str = ""
	model: str = ""
	input_tokens: int = 0
	output_tokens: int = 0
	duration_ms: int = 0
	tool_calls: int = 0
	compressed: bool = False
	success: bool = True
	metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ModelPrice:
	"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
按 token 计费的模型价格。
	Per-token model price.
	"""
	input_per_1k: float = 0.0
	output_per_1k: float = 0.0


@dataclass(frozen=True)
class CostFinding:
	"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
一条可执行的优化发现。
	One actionable optimization finding.
	"""
	title: str
	impact: float
	recommendation: str
	evidence: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class CostOptimizationReport:
	"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
聚合后的成本分析结果。
	Aggregated cost analysis.
	"""
	total_cost: float
	total_tokens: int
	by_agent: dict[str, float] = field(default_factory=dict)
	by_model: dict[str, float] = field(default_factory=dict)
	findings: list[CostFinding] = field(default_factory=list)
	metadata: dict[str, Any] = field(default_factory=dict)


class CostReportBuilder:
	"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
把成本样本聚合成报告字段和优化发现。
	Aggregates cost samples into report fields and findings.
	"""

	def __init__(self, estimator: "CostEstimator") -> None:
		self._estimator = estimator

	def build(self, samples: list[CostSample]) -> CostOptimizationReport:
		total_cost = 0.0
		total_tokens = 0
		by_agent: dict[str, float] = defaultdict(float)
		by_model: dict[str, float] = defaultdict(float)
		tokens_by_agent: dict[str, int] = defaultdict(int)
		duration_by_agent: dict[str, list[int]] = defaultdict(list)
		failures_by_agent: dict[str, int] = defaultdict(int)
		uncompressed_large: list[CostSample] = []
		for sample in samples:
			cost = self._estimator.estimate(sample)
			tokens = sample.input_tokens + sample.output_tokens
			total_cost += cost
			total_tokens += tokens
			agent = sample.agent_name or "<unknown>"
			model = sample.model or "<unknown>"
			by_agent[agent] += cost
			by_model[model] += cost
			tokens_by_agent[agent] += tokens
			duration_by_agent[agent].append(sample.duration_ms)
			if not sample.success:
				failures_by_agent[agent] += 1
			if tokens >= 8000 and not sample.compressed:
				uncompressed_large.append(sample)
		findings = self.findings(samples, by_agent, by_model, tokens_by_agent, duration_by_agent, failures_by_agent, uncompressed_large)
		return CostOptimizationReport(
			total_cost=round(total_cost, 6),
			total_tokens=total_tokens,
			by_agent={k: round(v, 6) for k, v in sorted(by_agent.items())},
			by_model={k: round(v, 6) for k, v in sorted(by_model.items())},
			findings=findings,
			metadata={"samples": len(samples)},
		)

	def findings(
		self,
		samples: list[CostSample],
		by_agent: dict[str, float],
		by_model: dict[str, float],
		tokens_by_agent: dict[str, int],
		duration_by_agent: dict[str, list[int]],
		failures_by_agent: dict[str, int],
		uncompressed_large: list[CostSample],
	) -> list[CostFinding]:
		findings: list[CostFinding] = []
		if by_agent:
			agent, cost = max(by_agent.items(), key=lambda item: item[1])
			findings.append(CostFinding(
				title=f"Highest spend Agent: {agent}",
				impact=round(cost, 6),
				recommendation="Review model choice, context size, and tool strategy for this Agent.",
				evidence=[sample.sample_id for sample in samples if (sample.agent_name or "<unknown>") == agent][:5],
			))
		if by_model:
			model, cost = max(by_model.items(), key=lambda item: item[1])
			findings.append(CostFinding(
				title=f"Highest spend model: {model}",
				impact=round(cost, 6),
				recommendation="Consider routing simple or utility tasks to a cheaper provider.",
				evidence=[sample.sample_id for sample in samples if (sample.model or "<unknown>") == model][:5],
			))
		if uncompressed_large:
			findings.append(CostFinding(
				title="Large contexts without compression",
				impact=float(sum(sample.input_tokens + sample.output_tokens for sample in uncompressed_large)),
				recommendation="Enable or tighten compression boundaries for large-context runs.",
				evidence=[sample.sample_id for sample in uncompressed_large[:5]],
			))
		for agent, durations in duration_by_agent.items():
			if durations and sum(durations) / len(durations) >= 30000:
				findings.append(CostFinding(
					title=f"Slow Agent: {agent}",
					impact=round(sum(durations) / len(durations), 3),
					recommendation="Inspect tool latency, provider routing, and step decomposition.",
					evidence=[sample.sample_id for sample in samples if (sample.agent_name or "<unknown>") == agent][:5],
				))
		for agent, failures in failures_by_agent.items():
			if failures:
				findings.append(CostFinding(
					title=f"Failed runs still consume budget: {agent}",
					impact=float(failures),
					recommendation="Mine failures into eval cases and add earlier validation or guardrails.",
					evidence=[sample.sample_id for sample in samples if (sample.agent_name or "<unknown>") == agent and not sample.success][:5],
				))
		findings.sort(key=lambda item: item.impact, reverse=True)
		return findings


class CostEstimator:
	"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
估算单条样本的模型花费。
	Estimates one sample's model spend.
	"""

	def __init__(self, prices: dict[str, ModelPrice] | None = None, default_price: ModelPrice | None = None) -> None:
		self._prices = prices or {}
		self._default_price = default_price or ModelPrice()

	def estimate(self, sample: CostSample) -> float:
		price = self._prices.get(sample.model, self._default_price)
		return (sample.input_tokens / 1000.0) * price.input_per_1k + (sample.output_tokens / 1000.0) * price.output_per_1k
