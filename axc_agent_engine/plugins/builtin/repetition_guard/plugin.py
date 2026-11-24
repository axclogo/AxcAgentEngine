"""RepetitionGuard 插件 — 多维度重复检测"""

import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from axc_agent_engine.plugins.base import BasePlugin, PreToolCallDecision
from axc_agent_engine.plugins.builtin.config_schemas import REPETITION_GUARD_CONFIG_SCHEMA

if TYPE_CHECKING:
	from axc_agent_engine.core.context import ExecutionContext
	from axc_agent_engine.plugins.context import PluginContext
	from axc_agent_engine.tools.tool_output import ToolOutput

logger = logging.getLogger(__name__)

DEFAULT_RULES = [
	{"type": "same_call", "limit": 3},
	{"type": "same_tool", "limit": 20},
	{"type": "total_tool", "limit": 100},
]


@dataclass
class RepetitionState:
	tools: list[tuple[str, str]] = field(default_factory=list)
	matches: dict[int, int] = field(default_factory=dict)
	reason: str = ""


class RepetitionGuardPlugin(BasePlugin):
	"""Detect repeated tool calls, responses and results.
	多维重复检测：工具调用、LLM 响应和工具结果。"""

	name = "repetition_guard"
	display_name = "重复防护"
	priority = 6
	version = "1.0.0"
	config_schema = REPETITION_GUARD_CONFIG_SCHEMA

	def initialize(self, config: dict, plugin_ctx: "PluginContext") -> None:
		self._rules = config.get("rules", DEFAULT_RULES)
		self._patterns: dict[str, list[tuple[int, re.Pattern[str], int]]] = {
			"response_pattern": [],
			"result_pattern": [],
		}
		if config.get("enable_text_repetition", True):
			for index, rule in enumerate(self._rules):
				kind = rule.get("type")
				if kind in self._patterns and rule.get("pattern"):
					limit = rule.get("limit", 3 if kind == "response_pattern" else 5)
					self._patterns[kind].append((index, re.compile(rule["pattern"], re.IGNORECASE), limit))
		self._tool_allowlist = set(config.get("tool_allowlist", []))
		self._tool_overrides = config.get("tool_overrides", {})
		for tool_name, limits in self._tool_overrides.items():
			if not isinstance(limits, dict) or not all(
				kind in {"same_call", "same_tool", "total_tool"} and type(limit) is int
				for kind, limit in limits.items()
			):
				raise ValueError(f"tool_overrides.{tool_name} must map tool rule names to integer limits")

	async def pre_tool_call(
		self, exec_ctx: "ExecutionContext", tool_name: str, arguments: dict
	) -> tuple[bool, dict] | PreToolCallDecision:
		if self._tool_allowlist and tool_name not in self._tool_allowlist:
			return True, arguments
		state = exec_ctx.get_plugin_state(self.name, RepetitionState)
		args_hash = _hash_args(arguments)
		rejection = self._check_tool_rules(state, tool_name, args_hash)
		if rejection:
			reason = rejection["reason"]
			logger.warning(f"[repetition_guard] Blocked: {reason}")
			state.reason = reason
			return PreToolCallDecision(
				False, arguments, self.name, reason, "tool.rejected_by_repetition_guard", details=rejection
			)
		state.tools.append((tool_name, args_hash))
		return True, arguments

	async def post_tool_call(
		self, exec_ctx: "ExecutionContext", tool_name: str, arguments: dict, result: "ToolOutput", duration_ms: int
	) -> "ToolOutput":
		if self._patterns["result_pattern"]:
			self._observe_text(exec_ctx, "result_pattern", result.context_view())
		return result

	async def on_round_end(
		self, exec_ctx: "ExecutionContext", user_message: str, assistant_message: str, tool_calls: list[dict]
	) -> None:
		if assistant_message and self._patterns["response_pattern"]:
			self._observe_text(exec_ctx, "response_pattern", assistant_message)

	def _observe_text(self, ctx: "ExecutionContext", kind: str, text: str) -> None:
		state = ctx.get_plugin_state(self.name, RepetitionState)
		for index, pattern, limit in self._patterns[kind]:
			count = state.matches.get(index, 0) + 1 if pattern.search(text) else 0
			state.matches[index] = count
			if text and count >= limit:
				label = "LLM response" if kind == "response_pattern" else "tool result"
				state.reason = f"Repetition detected: {label} matched '{pattern.pattern}' {count} consecutive times"
				logger.warning("[repetition_guard] %s", state.reason)
				break

	def should_stop(self, exec_ctx: "ExecutionContext") -> tuple[bool, str]:
		state = exec_ctx.get_plugin_state(self.name, RepetitionState)
		return bool(state.reason), state.reason

	def _check_tool_rules(self, state: RepetitionState, tool_name: str, args_hash: str) -> dict | None:
		for rule in self._rules:
			rtype = rule.get("type", "")
			limit = self._rule_limit(tool_name, rtype, rule.get("limit", 999))
			if rtype == "same_call":
				count = _count_consecutive_tail(state.tools, lambda t: t[0] == tool_name and t[1] == args_hash)
				if count >= limit:
					return _rejection("same_call", tool_name, count, limit)
			elif rtype == "same_tool":
				count = _count_consecutive_tail(state.tools, lambda t: t[0] == tool_name)
				if count >= limit:
					return _rejection("same_tool", tool_name, count, limit)
			elif rtype == "total_tool":
				count = sum(1 for t in state.tools if t[0] == tool_name)
				if count >= limit:
					return _rejection("total_tool", tool_name, count, limit)
		return None

	def _rule_limit(self, tool_name: str, rule_type: str, default: int) -> int:
		return self._tool_overrides.get(tool_name, {}).get(rule_type, default)


def _hash_args(arguments: dict) -> str:
	raw = json.dumps(arguments, sort_keys=True, ensure_ascii=False)
	return hashlib.md5(raw.encode()).hexdigest()[:16]


def _count_consecutive_tail(history: list, predicate) -> int:
	"""Count consecutive matches from the end. 从尾部开始计数连续匹配的数量"""
	count = 0
	for item in reversed(history):
		if predicate(item):
			count += 1
		else:
			break
	return count


def _rejection(rule_type: str, tool_name: str, observed: int, threshold: int) -> dict:
	return {
		"guard_name": "repetition_guard",
		"rule_type": rule_type,
		"tool_name": tool_name,
		"observed": observed,
		"threshold": threshold,
		"reason": f"Repetition detected: {rule_type} observed={observed} threshold={threshold} tool={tool_name}",
	}
