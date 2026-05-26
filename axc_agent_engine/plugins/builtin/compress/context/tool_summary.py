"""LLM-backed tool-use summaries for compressed context.
中文：此文档说明相关引擎组件的行为。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from axc_agent_engine.tools.tool_output import ToolOutput


TOOL_SUMMARY_PROMPT = """请总结下面的工具活动，供后续上下文使用。

保留长期有效的事实、文件路径、ID、错误和决策。省略噪音和重复模板内容。
请返回简洁的项目符号列表，总长度不超过 {max_chars} 个字符。

工具活动：
{activity}
"""


@dataclass
class ToolObservation:
	"""One completed tool invocation ready for summarization.
中文：此文档说明相关引擎组件的行为。"""

	name: str
	arguments: dict[str, Any]
	result: str
	duration_ms: int = 0
	is_error: bool = False

	def compact(self, max_chars: int = 1200) -> str:
		args = _truncate(str(self.arguments), 400)
		result = _truncate(self.result, max_chars)
		status = "error" if self.is_error else "ok"
		return f"- {self.name}({args}) [{status}, {self.duration_ms}ms]\n  {result}"


class ToolSummaryService:
	"""Summarizes completed tools with utility_llm, with deterministic fallback.
中文：此文档说明相关引擎组件的行为。"""

	def __init__(self, max_chars: int = 1200, max_observations: int = 20) -> None:
		self.max_chars = max(200, int(max_chars))
		self.max_observations = max(1, int(max_observations))

	async def summarize(self, utility_llm: Any, observations: list[ToolObservation]) -> str:
		items = observations[-self.max_observations:]
		if not items:
			return ""
		activity = "\n".join(item.compact() for item in items)
		if utility_llm:
			try:
				summary = await utility_llm.ask(TOOL_SUMMARY_PROMPT.format(activity=activity, max_chars=self.max_chars))
				return _truncate(str(summary).strip(), self.max_chars)
			except Exception:
				pass
		return _truncate(activity, self.max_chars)


def observation_from_output(
	tool_name: str,
	arguments: dict[str, Any],
	output: ToolOutput,
	duration_ms: int,
	max_result_chars: int = 1600,
) -> ToolObservation:
	return ToolObservation(
		name=tool_name,
		arguments=dict(arguments),
		result=output.compact_view(max_result_chars),
		duration_ms=int(duration_ms),
		is_error=output.is_error,
	)


def tool_summaries_message(summaries: list[str]) -> dict[str, str] | None:
	lines = [item.strip() for item in summaries if item and item.strip()]
	if not lines:
		return None
	return {"role": "system", "content": "[工具摘要]\n" + "\n".join(f"- {item}" for item in lines)}


def _truncate(text: str, max_chars: int) -> str:
	if len(text) <= max_chars:
		return text
	head_len = max_chars * 3 // 4
	tail_len = max_chars - head_len
	omitted = len(text) - head_len - tail_len
	return f"{text[:head_len]}\n...[省略 {omitted} 个字符]...\n{text[-tail_len:]}"
