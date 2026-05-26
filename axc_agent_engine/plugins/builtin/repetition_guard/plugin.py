"""RepetitionGuard 插件 — 多维度重复检测"""
import hashlib
import json
import logging
import re
from typing import TYPE_CHECKING

from axc_agent_engine.plugins.base import BasePlugin

if TYPE_CHECKING:
	from axc_agent_engine.core.context import ExecutionContext
	from axc_agent_engine.plugins import PluginContext
	from axc_agent_engine.tools.tool_output import ToolOutput

logger = logging.getLogger(__name__)

DEFAULT_RULES = [
	{"type": "same_call", "limit": 3},
	{"type": "same_tool", "limit": 20},
	{"type": "total_tool", "limit": 100},
]


class RepetitionGuardPlugin(BasePlugin):
	"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
多维重复检测：工具调用、LLM 响应和工具结果。"""
	name = "repetition_guard"
	display_name = "重复防护"
	priority = 6
	version = "1.0.0"

	def initialize(self, config: dict, plugin_ctx: "PluginContext") -> None:
		self._rules = config.get("rules", DEFAULT_RULES)
		self._tool_history: list[tuple[str, str]] = []
		self._response_history: list[str] = []
		self._result_history: list[str] = []
		self._should_stop = False
		self._stop_reason = ""

	async def pre_tool_call(self, exec_ctx: "ExecutionContext", tool_name: str,
					  arguments: dict) -> tuple[bool, dict]:
		args_hash = _hash_args(arguments)
		reason = self._check_tool_rules(tool_name, args_hash)
		if reason:
			logger.warning(f"[repetition_guard] Blocked: {reason}")
			self._should_stop = True
			self._stop_reason = reason
			return False, arguments
		self._tool_history.append((tool_name, args_hash))
		return True, arguments

	async def post_tool_call(self, exec_ctx: "ExecutionContext", tool_name: str,
					   arguments: dict, result: "ToolOutput", duration_ms: int) -> "ToolOutput":
		result_str = result.compact_view() if result else ""
		self._result_history.append(result_str)
		reason = self._check_result_rules(result_str)
		if reason:
			logger.warning(f"[repetition_guard] Result repetition: {reason}")
			self._should_stop = True
			self._stop_reason = reason
		return result

	async def on_round_end(self, exec_ctx: "ExecutionContext", user_message: str,
						   assistant_message: str, tool_calls: list[dict]) -> None:
		if assistant_message:
			self._response_history.append(assistant_message)
		reason = self._check_response_rules(assistant_message)
		if reason:
			logger.warning(f"[repetition_guard] Response repetition: {reason}")
			self._should_stop = True
			self._stop_reason = reason

	def should_stop(self, exec_ctx: "ExecutionContext") -> tuple[bool, str]:
		if self._should_stop:
			return True, self._stop_reason
		return False, ""

	def _check_tool_rules(self, tool_name: str, args_hash: str) -> str | None:
		for rule in self._rules:
			rtype = rule.get("type", "")
			limit = rule.get("limit", 999)
			if rtype == "same_call":
				count = _count_consecutive_tail(
					self._tool_history, lambda t: t[0] == tool_name and t[1] == args_hash
				)
				if count >= limit:
					return f"Repetition detected: same tool+args called {count} consecutive times ({tool_name})"
			elif rtype == "same_tool":
				count = _count_consecutive_tail(
					self._tool_history, lambda t: t[0] == tool_name
				)
				if count >= limit:
					return f"Repetition detected: same tool called {count} consecutive times ({tool_name})"
			elif rtype == "total_tool":
				count = sum(1 for t in self._tool_history if t[0] == tool_name)
				if count >= limit:
					return f"Repetition detected: tool {tool_name} called {count} times total"
		return None

	def _check_response_rules(self, response: str) -> str | None:
		if not response:
			return None
		for rule in self._rules:
			if rule.get("type") != "response_pattern":
				continue
			pattern = rule.get("pattern", "")
			limit = rule.get("limit", 3)
			if not pattern:
				continue
			count = _count_consecutive_tail(
				self._response_history, lambda r: bool(re.search(pattern, r, re.IGNORECASE))
			)
			if count >= limit:
				return f"Repetition detected: LLM response matched '{pattern}' {count} consecutive times"
		return None

	def _check_result_rules(self, result: str) -> str | None:
		if not result:
			return None
		for rule in self._rules:
			if rule.get("type") != "result_pattern":
				continue
			pattern = rule.get("pattern", "")
			limit = rule.get("limit", 5)
			if not pattern:
				continue
			count = _count_consecutive_tail(
				self._result_history, lambda r: bool(re.search(pattern, r, re.IGNORECASE))
			)
			if count >= limit:
				return f"Repetition detected: tool result matched '{pattern}' {count} consecutive times"
		return None


def _hash_args(arguments: dict) -> str:
	raw = json.dumps(arguments, sort_keys=True, ensure_ascii=False)
	return hashlib.md5(raw.encode()).hexdigest()[:16]


def _count_consecutive_tail(history: list, predicate) -> int:
	"""English: This documentation describes the related engine component behavior.
中文：从尾部开始计数连续匹配的数量"""
	count = 0
	for item in reversed(history):
		if predicate(item):
			count += 1
		else:
			break
	return count
