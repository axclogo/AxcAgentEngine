"""RiskGuard 插件 — 工具风险动态分级"""

import logging
from typing import TYPE_CHECKING, Any

from axc_agent_engine.core.schema import RiskLevel
from axc_agent_engine.plugins.base import BasePlugin, PreToolCallDecision
from axc_agent_engine.plugins.builtin.config_schemas import RISK_GUARD_CONFIG_SCHEMA
from axc_agent_engine.runtime.risk import RiskRuleEngine, classify_tool_risk

if TYPE_CHECKING:
	from axc_agent_engine.core.context import ExecutionContext
	from axc_agent_engine.plugins.context import PluginContext

logger = logging.getLogger(__name__)


class RiskGuardPlugin(BasePlugin):
	"""Classify risk and enforce approval for tools.
	工具风险动态分级 — blocked 直接拒绝，dangerous 通过 exec_ctx.metadata 标记"""

	name = "risk_guard"
	display_name = "风险分级"
	priority = 7
	version = "1.0.0"
	config_schema = RISK_GUARD_CONFIG_SCHEMA

	def initialize(self, config: dict, plugin_ctx: "PluginContext" = None) -> None:
		self._custom_rules = config.get("rules", [])
		self._rule_engine = RiskRuleEngine(self._custom_rules)

	async def pre_tool_call(
		self, exec_ctx: "ExecutionContext", tool_name: str, arguments: dict
	) -> tuple[bool, dict] | PreToolCallDecision:
		risk = self._rule_engine.classify(tool_name, arguments).level
		if risk == RiskLevel.BLOCKED:
			logger.warning(f"[risk_guard] Blocked tool: {tool_name}")
			return PreToolCallDecision(
				False,
				arguments,
				self.name,
				f"工具 {tool_name} 命中 blocked 风险规则，参数不允许执行",
				"tool.rejected_by_risk_guard",
			)
		if risk != RiskLevel.SAFE:
			exec_ctx.runtime.risk_level = risk.value
		return True, arguments


def classify_risk(
	tool_name: str, arguments: dict[str, Any], static_risk: str = "safe", custom_rules: list[dict] | None = None
) -> RiskLevel:
	"""Evaluate tool risk at call time. 动态评估工具风险等级"""
	return classify_tool_risk(tool_name, arguments, static_risk=static_risk, custom_rules=custom_rules).level
