"""Tool risk classification and shell safety checks.
中文：此文档说明相关引擎组件的行为。"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from axc_agent_engine.core.schema import RiskLevel

RISK_LEVELS = {RiskLevel.SAFE: 0, RiskLevel.MODERATE: 1, RiskLevel.DANGEROUS: 2, RiskLevel.BLOCKED: 3}
RISK_NAMES = {0: RiskLevel.SAFE, 1: RiskLevel.MODERATE, 2: RiskLevel.DANGEROUS, 3: RiskLevel.BLOCKED}

BLOCKED_SHELL_PATTERNS = [
	(r"\brm\s+-rf\s+/", "rm -rf /"),
	(r"\bmkfs\b", "format filesystem"),
	(r"\bdd\s+if=", "raw disk write"),
	(r">\s*/etc/", "write into /etc"),
	(r"\bcurl\b.*\|\s*\b(bash|sh)\b", "curl pipe shell"),
	(r"\bwget\b.*\|\s*\b(bash|sh)\b", "wget pipe shell"),
	(r"\bchmod\s+777\b", "chmod 777"),
	(r"\bsudo\b", "sudo privilege escalation"),
	(r"\bsu\s+-", "switch user"),
	(r";\s*rm\b", "command injection rm"),
	(r"\$\([^)]*rm\b", "command substitution rm"),
]

DANGEROUS_SHELL_PATTERNS = [
	(r"\brm\b", "delete files"),
	(r"\bgit\s+push\b", "git push"),
	(r"\bgit\s+reset\s+--hard\b", "hard reset"),
	(r"\bkill\s+-9\b", "force kill"),
	(r"\bpip\s+install\b", "install packages"),
	(r"\bnpm\s+install\b", "install packages"),
	(r"\bdocker\s+rm\b", "remove containers"),
]

SAFE_COMMAND_PREFIXES = {
	"ls", "cat", "head", "tail", "wc", "find", "grep", "egrep", "fgrep",
	"awk", "sed", "sort", "uniq", "diff", "file", "stat", "echo", "printf",
	"pwd", "whoami", "date", "env", "which", "type", "git status", "git log",
	"git diff", "git branch", "git show", "python --version", "python3 --version",
	"node --version", "pip list", "pip show", "npm list", "df", "du", "free",
	"top", "ps", "uptime", "uname",
}

DEFAULT_RULES: list[dict[str, Any]] = [
	{"name": "sensitive path write", "tool_pattern": r"file_(write|edit|delete)",
	 "arg_name": "path", "arg_pattern": r"(config|\.env|password|secret|key|credentials)",
	 "escalate_to": RiskLevel.DANGEROUS},
	{"name": "dangerous sql", "tool_pattern": r"(query|sql|execute)",
	 "arg_name": "sql", "arg_pattern": r"(DROP|DELETE|TRUNCATE|ALTER|UPDATE)\s",
	 "escalate_to": RiskLevel.DANGEROUS},
	{"name": "bulk operation", "tool_pattern": r".*", "arg_name": "ids",
	 "arg_check": "list_gt_20", "escalate_to": RiskLevel.MODERATE},
	{"name": "external url", "tool_pattern": r"(http_request|api_call|fetch)",
	 "arg_name": "url", "arg_pattern": r"^https?://", "escalate_to": RiskLevel.MODERATE},
]

ARG_CHECKS = {
	"list_gt_20": lambda v: isinstance(v, list) and len(v) > 20,
	"list_gt_10": lambda v: isinstance(v, list) and len(v) > 10,
	"str_gt_5000": lambda v: isinstance(v, str) and len(v) > 5000,
}


@dataclass(frozen=True)
class RiskAssessment:
	level: RiskLevel
	reason: str = ""
	matched_rule: str = ""
	allowed: bool = True


class RiskRuleEngine:
	def __init__(self, rules: list[dict] | None = None) -> None:
		self.rules = DEFAULT_RULES + (rules or [])
		for rule in self.rules:
			_validate_rule(rule)

	def classify(self, tool_name: str, arguments: dict[str, Any],
				 static_risk: str | RiskLevel = RiskLevel.SAFE) -> RiskAssessment:
		static_value = static_risk.value if isinstance(static_risk, RiskLevel) else str(static_risk)
		static = RiskLevel(static_value) if static_value in {level.value for level in RISK_LEVELS} else RiskLevel.SAFE
		current_level = RISK_LEVELS.get(static, 0)
		reason = ""
		matched_rule = ""
		if re.search(r"(shell|bash|command|terminal|exec)", tool_name, re.IGNORECASE):
			command = arguments.get("command") or arguments.get("cmd") or arguments.get("script") or ""
			if command:
				shell_assessment = check_shell_command(str(command))
				if shell_assessment.level == RiskLevel.BLOCKED:
					return shell_assessment
				if RISK_LEVELS[shell_assessment.level] > current_level:
					current_level = RISK_LEVELS[shell_assessment.level]
					reason = shell_assessment.reason
					matched_rule = shell_assessment.matched_rule
		for rule in self.rules:
			if not re.search(rule.get("tool_pattern", ".*"), tool_name, re.IGNORECASE):
				continue
			arg_name = rule.get("arg_name", "")
			if not arg_name or arg_name not in arguments:
				continue
			value = arguments[arg_name]
			matched = False
			if rule.get("arg_pattern") and isinstance(value, str):
				matched = bool(re.search(rule["arg_pattern"], value, re.IGNORECASE))
			if rule.get("arg_check"):
				check = ARG_CHECKS[str(rule["arg_check"])]
				matched = matched or bool(check(value))
			if matched:
				raw_target = rule.get("escalate_to", RiskLevel.MODERATE)
				target_value = raw_target.value if isinstance(raw_target, RiskLevel) else str(raw_target)
				target = RiskLevel(target_value)
				if RISK_LEVELS[target] > current_level:
					current_level = RISK_LEVELS[target]
					reason = rule.get("name", "")
					matched_rule = rule.get("arg_pattern", rule.get("arg_check", ""))
		level = RISK_NAMES[current_level]
		return RiskAssessment(level=level, reason=reason, matched_rule=str(matched_rule), allowed=level != RiskLevel.BLOCKED)


def check_shell_command(command: str) -> RiskAssessment:
	if not command or not command.strip():
		return RiskAssessment(RiskLevel.SAFE, "empty command")
	cmd = command.strip()
	for pattern, reason in BLOCKED_SHELL_PATTERNS:
		if re.search(pattern, cmd, re.IGNORECASE):
			return RiskAssessment(RiskLevel.BLOCKED, reason, matched_rule=pattern, allowed=False)
	for pattern, reason in DANGEROUS_SHELL_PATTERNS:
		if re.search(pattern, cmd, re.IGNORECASE):
			return RiskAssessment(RiskLevel.DANGEROUS, reason, matched_rule=pattern)
	base_cmd = cmd.split("|")[0].strip()
	parts = base_cmd.split()
	first_word = parts[0] if parts else ""
	first_two = " ".join(parts[:2]) if len(parts) >= 2 else ""
	if first_word in SAFE_COMMAND_PREFIXES or first_two in SAFE_COMMAND_PREFIXES:
		return RiskAssessment(RiskLevel.SAFE, "read-only command")
	return RiskAssessment(RiskLevel.MODERATE, f"unknown command: {first_word}")


def classify_tool_risk(tool_name: str, arguments: dict[str, Any],
					   static_risk: str | RiskLevel = RiskLevel.SAFE,
					   custom_rules: list[dict] | None = None) -> RiskAssessment:
	return RiskRuleEngine(custom_rules).classify(tool_name, arguments, static_risk)


def _validate_rule(rule: dict[str, Any]) -> None:
	if not isinstance(rule, dict):
		raise ValueError("Risk rule must be an object")
	re.compile(str(rule.get("tool_pattern", ".*")))
	if rule.get("arg_pattern"):
		re.compile(str(rule["arg_pattern"]))
	if rule.get("arg_check") and str(rule["arg_check"]) not in ARG_CHECKS:
		raise ValueError(f"Unknown risk arg_check: {rule['arg_check']}")
	raw_target = rule.get("escalate_to", RiskLevel.MODERATE)
	target_value = raw_target.value if isinstance(raw_target, RiskLevel) else str(raw_target)
	RiskLevel(target_value)
