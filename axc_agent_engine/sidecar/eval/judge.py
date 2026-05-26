"""LLM-as-Judge — 使用 LLM 评估输出质量"""
from __future__ import annotations

import logging
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
	from axc_agent_engine.sidecar.eval.runner import EvalCase, EvalResult

logger = logging.getLogger(__name__)

JUDGE_PROMPT = """你是一个评估助手。请评估 AI 的实际输出与期望输出的匹配程度。

用户输入：{input}
期望输出：{expected}
实际输出：{actual}

请给出 0.0-1.0 的评分和简短理由。
格式：score|理由
示例：0.8|基本正确但缺少部分细节"""


class LLMJudge:
	"""LLM 评估器"""

	def __init__(self, llm_client: Any) -> None:
		self._llm = llm_client

	async def judge(self, case: "EvalCase", result: "EvalResult") -> tuple[float, str]:
		"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
评估单个结果，返回 (score, reason)"""
		prompt = JUDGE_PROMPT.format(
			input=case.input,
			expected=case.expected_output or "(无期望输出)",
			actual=result.actual_output or "(无输出)",
		)
		try:
			response = await self._llm.ask(prompt)
			return self._parse_response(response)
		except Exception as e:
			logger.warning(f"[judge] LLM evaluation failed: {e}")
			return 0.0, f"评估失败: {e}"

	@staticmethod
	def _parse_response(response: str) -> tuple[float, str]:
		"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
解析 LLM 评估响应"""
		response = response.strip()
		if "|" in response:
			parts = response.split("|", 1)
			try:
				score = float(parts[0].strip())
				reason = parts[1].strip()
				return min(max(score, 0.0), 1.0), reason
			except (ValueError, IndexError):
				pass
		try:
			score = float(response[:4])
			return min(max(score, 0.0), 1.0), response
		except ValueError:
			return 0.5, response
