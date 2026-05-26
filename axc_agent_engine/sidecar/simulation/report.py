"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
仿真报告生成辅助。
Simulation report generation helpers."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from axc_agent_engine.sidecar.simulation.models import SimulationReport

logger = logging.getLogger(__name__)


@dataclass
class GeneratedSimulationReport:
	"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
附加到结构化仿真报告上的 LLM 叙述内容。
	LLM-generated narrative attached to a structured simulation report.
	"""

	summary: str = ""
	key_findings: list[str] = field(default_factory=list)
	risks: list[str] = field(default_factory=list)
	recommendations: list[str] = field(default_factory=list)


@runtime_checkable
class SimulationReportGenerator(Protocol):
	"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
仿真报告生成器协议。
	Simulation report generator protocol.
	"""

	async def generate(self, report: SimulationReport) -> GeneratedSimulationReport: ...


class LLMSimulationReportGenerator:
	"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
使用 utility LLM 生成结构化叙述报告字段。
	Generate structured narrative report fields with a utility LLM.
	"""

	def __init__(self, utility_llm: Any, max_timeline_steps: int = 20) -> None:
		self.utility_llm = utility_llm
		self.max_timeline_steps = max_timeline_steps

	async def generate(self, report: SimulationReport) -> GeneratedSimulationReport:
		if not self.utility_llm:
			return GeneratedSimulationReport(summary=report.summary)
		prompt = _build_prompt(report, self.max_timeline_steps)
		try:
			content = await self.utility_llm.ask(prompt)
			return _parse_generated_report(content, fallback=report.summary)
		except Exception as exc:
			logger.warning("[simulation] report generation failed: %s", exc)
			return GeneratedSimulationReport(summary=report.summary)


def apply_generated_report(report: SimulationReport, generated: GeneratedSimulationReport) -> SimulationReport:
	"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
把生成的叙述字段附加到现有结构化报告。
	Attach generated narrative fields to the existing structured report.
	"""
	if generated.summary:
		report.summary = generated.summary
	report.metrics = {
		**report.metrics,
		"key_findings": list(generated.key_findings),
		"risks": list(generated.risks),
		"recommendations": list(generated.recommendations),
	}
	return report


def _build_prompt(report: SimulationReport, max_steps: int) -> str:
	steps = []
	for step in report.timeline[:max_steps]:
		steps.append({
			"step_id": step.step_id,
			"actor": step.actor,
			"action": step.action.intent,
			"action_type": str(step.action.type),
			"delta": step.delta.notes,
			"goal_progress": step.scorecard.goal_progress,
			"confidence": step.scorecard.confidence,
			"notes": step.scorecard.notes,
		})
	payload = {
		"title": report.title,
		"scenario_id": report.scenario_id,
		"summary": report.summary,
		"success": report.success,
		"error": report.error,
		"metrics": report.metrics,
		"timeline": steps,
	}
	return (
		"请根据以下 JSON 生成结构化仿真报告。"
		"只返回 JSON，字段必须包含：summary、key_findings、risks、recommendations。"
		"每个列表字段都使用简洁字符串。\n"
		f"{json.dumps(payload, ensure_ascii=False)}"
	)


def _parse_generated_report(content: str, fallback: str = "") -> GeneratedSimulationReport:
	if not content:
		return GeneratedSimulationReport(summary=fallback)
	text = content.strip()
	if text.startswith("```"):
		lines = text.splitlines()
		text = "\n".join(lines[1:-1] if lines and lines[-1].strip() == "```" else lines[1:])
	start = text.find("{")
	end = text.rfind("}")
	if start >= 0 and end > start:
		text = text[start:end + 1]
	try:
		raw = json.loads(text)
	except (json.JSONDecodeError, TypeError):
		return GeneratedSimulationReport(summary=fallback or content.strip())
	if not isinstance(raw, dict):
		return GeneratedSimulationReport(summary=fallback)
	return GeneratedSimulationReport(
		summary=str(raw.get("summary") or fallback),
		key_findings=_string_list(raw.get("key_findings")),
		risks=_string_list(raw.get("risks")),
		recommendations=_string_list(raw.get("recommendations")),
	)


def _string_list(value: Any) -> list[str]:
	if not isinstance(value, list):
		return []
	return [str(item).strip() for item in value if str(item).strip()]
