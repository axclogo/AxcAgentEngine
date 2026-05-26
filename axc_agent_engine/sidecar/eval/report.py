"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
评估报告生成。
Evaluation report generation."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
	from axc_agent_engine.sidecar.eval.runner import EvalResult


@dataclass
class EvalReport:
	"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
评估报告。
	Evaluation report.
	"""
	total_cases: int = 0
	passed: int = 0
	failed: int = 0
	avg_score: float = 0.0
	total_input_tokens: int = 0
	total_output_tokens: int = 0
	total_duration_ms: int = 0
	results: list["EvalResult"] = field(default_factory=list)

	def summary(self) -> str:
		lines = [
			f"评估报告: {self.total_cases} 用例",
			f"通过: {self.passed}, 失败: {self.failed}",
			f"平均分: {self.avg_score:.2f}",
			f"总 Token: {self.total_input_tokens}+{self.total_output_tokens}",
			f"总耗时: {self.total_duration_ms}ms",
		]
		return "\n".join(lines)


def generate_report(results: list["EvalResult"], pass_threshold: float = 0.6) -> EvalReport:
	"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
从评估结果生成报告。
	Generate a report from evaluation results.
	"""
	if not results:
		return EvalReport()
	total = len(results)
	passed = sum(1 for r in results if r.score >= pass_threshold)
	failed = total - passed
	avg_score = sum(r.score for r in results) / total
	total_input = sum(r.input_tokens for r in results)
	total_output = sum(r.output_tokens for r in results)
	total_duration = sum(r.duration_ms for r in results)
	return EvalReport(
		total_cases=total, passed=passed, failed=failed,
		avg_score=avg_score, total_input_tokens=total_input,
		total_output_tokens=total_output, total_duration_ms=total_duration,
		results=results,
	)
