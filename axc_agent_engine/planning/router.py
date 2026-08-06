"""TransactionRouter — 按运行时策略在 ReAct 与 POR 之间路由。

路由模式：
- react_only：禁止进入 POR，只走 ReAct 循环。
- por_first：首轮 ReAct 前强制进入 PlanningService。
- auto：final_answer → DONE，tool_calls → ReAct，结构化计划 JSON → POR。
"""
from dataclasses import dataclass
from typing import Any

from axc_agent_engine.planning.planner import Plan
from axc_agent_engine.planning.planning_service import PlanningService


@dataclass(frozen=True)
class RoutingDecision:
	"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
单轮 LLM 输出后的运行时路由决策。"""
	action: str  # English: allowed values are final_answer | tool_calls | por_plan. 中文：可选值为 final_answer | tool_calls | por_plan。
	plan: Plan | None = None


class TransactionRouter:
	"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
根据路由策略决定进入 ReAct 还是 POR。"""

	def __init__(self, mode: str = "auto") -> None:
		self._mode = mode

	@property
	def mode(self) -> str:
		return self._mode

	def route(self, message: dict[str, Any]) -> RoutingDecision:
		"""English: Bilingual documentation follows.
	中文：以下为双语文档说明。
	把标准化 assistant message 分类成运行时 action。"""
		tool_calls = message.get("tool_calls", []) or []
		# English: Explicit tool requests always belong to ReAct; 中文：显式工具请求始终归属 ReAct。
		# English: Plan-like companion text must not trigger POR; 中文：伴随的类计划文本不得触发 POR。
		if tool_calls:
			return RoutingDecision(action="tool_calls")
		plan = None if self._mode == "react_only" else PlanningService.detect_plan(message)
		return RoutingDecision(action="por_plan", plan=plan) if plan else RoutingDecision(action="final_answer")
