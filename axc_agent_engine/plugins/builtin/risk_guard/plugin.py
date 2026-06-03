"""RiskGuard 插件 — 工具风险动态分级"""
import logging
from typing import Any, TYPE_CHECKING

from axc_agent_engine.plugins.base import BasePlugin
from axc_agent_engine.plugins.builtin.config_schemas import RISK_GUARD_CONFIG_SCHEMA
from axc_agent_engine.core.schema import RiskLevel
from axc_agent_engine.runtime.risk import classify_tool_risk

if TYPE_CHECKING:
	from axc_agent_engine.core.context import ExecutionContext
	from axc_agent_engine.plugins import PluginContext

logger = logging.getLogger(__name__)

class RiskGuardPlugin(BasePlugin):
	"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
工具风险动态分级 — blocked 直接拒绝，dangerous 通过 exec_ctx.metadata 标记"""
	name = "risk_guard"
	display_name = "风险分级"
	priority = 7
	version = "1.0.0"
	config_schema = RISK_GUARD_CONFIG_SCHEMA

	def initialize(self, config: dict, plugin_ctx: "PluginContext" = None) -> None:
		self._custom_rules = config.get("rules", [])

	async def pre_tool_call(self, exec_ctx: "ExecutionContext" = None, tool_name: str = "",
					  arguments: dict = None) -> tuple[bool, dict]:
		arguments = arguments or {}
		risk = classify_risk(tool_name, arguments, custom_rules=self._custom_rules)
		if risk == RiskLevel.BLOCKED:
			logger.warning(f"[risk_guard] Blocked tool: {tool_name}")
			self._last_rejection_reason = f"工具 {tool_name} 命中 blocked 风险规则，参数不允许执行"
			self._last_rejection_code = "tool.rejected_by_risk_guard"
			return False, arguments
		if risk != RiskLevel.SAFE and exec_ctx:
			exec_ctx.runtime.risk_level = risk.value
		return True, arguments


def classify_risk(tool_name: str, arguments: dict[str, Any],
				  static_risk: str = "safe",
				  custom_rules: list[dict] | None = None) -> RiskLevel:
	"""English: This documentation describes the related engine component behavior.
中文：动态评估工具风险等级"""
	return classify_tool_risk(tool_name, arguments, static_risk=static_risk, custom_rules=custom_rules).level
