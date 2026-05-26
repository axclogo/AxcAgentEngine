from axc_agent_engine.core.schema import RiskLevel
from axc_agent_engine.runtime.risk import check_shell_command, classify_tool_risk


def test_shell_security_blocks_privilege_escalation():
	result = check_shell_command("sudo rm -rf /tmp/x")
	assert result.level == RiskLevel.BLOCKED
	assert not result.allowed


def test_tool_risk_uses_shell_security():
	result = classify_tool_risk("shell", {"command": "pip install requests"})
	assert result.level == RiskLevel.DANGEROUS


def test_tool_risk_flags_bulk_operation():
	result = classify_tool_risk("delete_items", {"ids": list(range(21))})
	assert result.level == RiskLevel.MODERATE
