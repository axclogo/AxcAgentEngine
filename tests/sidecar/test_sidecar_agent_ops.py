"""Tests for sidecar AgentOps utilities."""
from __future__ import annotations

from axc_agent_engine.sidecar import (
	AgentProfile,
	AgentSelector,
	CostEstimator,
	CostReportBuilder,
	CostSample,
	DistillationReportBuilder,
	ExecutionTrace,
	FailureRecord,
	FailureReportBuilder,
	ModelPrice,
	SelectionRequest,
	TraceStep,
)


def test_agent_selector_prefers_capable_high_quality_agent():
	selector = AgentSelector([
		AgentProfile(name="cheap", description="general assistant", capabilities={"chat"}, quality_score=0.6),
		AgentProfile(name="security", description="security analysis and red team", capabilities={"security", "audit"}, tags={"risk"}, quality_score=0.9),
	])

	best = selector.best(SelectionRequest(
		task="security audit for plugin sandbox",
		required_capabilities={"security"},
		preferred_tags={"risk"},
	))

	assert best is not None
	assert best.agent.name == "security"
	assert best.missing_capabilities == set()


def test_agent_distiller_extracts_tool_rules_and_failure_antipatterns():
	traces = [
		ExecutionTrace(
			trace_id="t1",
			task="analyze invoice pdf",
			success=True,
			score=0.9,
			steps=[TraceStep(role="tool", tool_name="file_read")],
		),
		ExecutionTrace(
			trace_id="t2",
			task="analyze invoice pdf",
			success=True,
			score=0.8,
			steps=[TraceStep(role="tool", tool_name="file_read")],
		),
		ExecutionTrace(
			trace_id="t3",
			task="analyze invoice pdf",
			success=False,
			score=0.2,
			steps=[TraceStep(role="tool", tool_name="ocr", success=False, error="parse failed: invalid json")],
		),
	]

	report = DistillationReportBuilder().build(traces)

	assert "file_read" in report.tool_preferences
	assert any("file_read" in rule.text for rule in report.rules)
	assert any("invalid json" in rule.text for rule in report.anti_patterns)


def test_failure_miner_clusters_and_suggests_actions():
	records = [
		FailureRecord(record_id="f1", agent_name="a", message="Tool timeout after 30s", tool_name="shell"),
		FailureRecord(record_id="f2", agent_name="b", message="Tool timeout after 30s", tool_name="shell"),
		FailureRecord(record_id="f3", agent_name="a", message="invalid json output"),
	]

	report = FailureReportBuilder().build(records, min_count=1)

	assert report.category_counts["timeout"] == 2
	assert report.clusters[0].count == 2
	assert "timeout" in report.clusters[0].suggested_action.lower()


def test_failure_miner_infers_categories_filters_min_count_and_limits_examples():
	from axc_agent_engine.sidecar.failure_miner import infer_failure_category, suggest_action

	messages = {
		"permission denied": "policy",
		"schema parse failed": "format",
		"subprocess exit code 1": "tool",
		"rate limit 429": "provider_limit",
		"context token maximum": "context_budget",
		"unknown": "unknown",
	}
	for message, category in messages.items():
		assert infer_failure_category(message) == category
	records = [
		FailureRecord(record_id=f"f{i}", agent_name=f"a{i}", message="permission denied", severity=i / 10)
		for i in range(7)
	]
	records.append(FailureRecord(record_id="single", message="schema bad"))
	report = FailureReportBuilder().build(records, min_count=2)
	assert len(report.clusters) == 1
	assert len(report.clusters[0].examples) == 5
	assert report.clusters[0].affected_agents == {f"a{i}" for i in range(7)}
	assert suggest_action("tool", "shell").endswith("tool 'shell'.")
	assert "Inspect" in suggest_action("other")


def test_cost_optimizer_estimates_and_flags_large_uncompressed_context():
	estimator = CostEstimator(prices={"big": ModelPrice(input_per_1k=0.01, output_per_1k=0.03)})
	builder = CostReportBuilder(estimator)
	samples = [
		CostSample(sample_id="c1", agent_name="agent", model="big", input_tokens=9000, output_tokens=1000, duration_ms=40000, compressed=False),
		CostSample(sample_id="c2", agent_name="agent", model="big", input_tokens=1000, output_tokens=500, success=False),
	]

	report = builder.build(samples)

	assert report.total_cost > 0
	assert report.by_agent["agent"] == report.total_cost
	assert any(finding.title == "Large contexts without compression" for finding in report.findings)
	assert any("Failed runs" in finding.title for finding in report.findings)
