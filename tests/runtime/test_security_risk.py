from axc_agent_engine.core.schema import RiskLevel
import pytest

from axc_agent_engine.runtime.risk import RiskRuleEngine, check_shell_command, classify_tool_risk


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


def test_shell_security_classifies_empty_safe_and_unknown_commands():
	assert check_shell_command("").level == RiskLevel.SAFE
	assert check_shell_command("ls -la").reason == "read-only command"
	result = check_shell_command("custom_cmd --flag")
	assert result.level == RiskLevel.MODERATE
	assert result.reason == "unknown command: custom_cmd"


def test_risk_rule_engine_custom_rules_and_invalid_rule_rejection():
	result = classify_tool_risk(
		"file_write",
		{"path": "/tmp/x"},
		custom_rules=[{
			"name": "tmp write",
			"tool_pattern": "file_.*",
			"arg_name": "path",
			"arg_pattern": r"^/tmp",
			"escalate_to": "dangerous",
		}],
	)
	assert result.level == RiskLevel.DANGEROUS
	assert result.reason == "tmp write"

	with pytest.raises(ValueError, match="Risk rule must be an object"):
		RiskRuleEngine(["bad"])
	with pytest.raises(ValueError, match="Unknown risk arg_check"):
		RiskRuleEngine([{"arg_check": "missing"}])
