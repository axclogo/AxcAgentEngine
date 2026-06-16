"""Hooks 插件 — 声明式规则拦截/转换/日志"""
import ast
import logging
import operator
from typing import Any, TYPE_CHECKING

from axc_agent_engine.plugins.base import BasePlugin
from axc_agent_engine.plugins.builtin.config_schemas import HOOKS_CONFIG_SCHEMA

if TYPE_CHECKING:
	from axc_agent_engine.core.context import ExecutionContext
	from axc_agent_engine.plugins.context import PluginContext
	from axc_agent_engine.tools.tool_output import ToolOutput

logger = logging.getLogger(__name__)


class HooksPlugin(BasePlugin):
	name = "hooks"
	display_name = "声明式钩子"
	priority = 5
	version = "1.0.0"
	config_schema = HOOKS_CONFIG_SCHEMA

	def initialize(self, config: dict, plugin_ctx: "PluginContext") -> None:
		self._rules: list[dict] = config.get("rules", [])
		for rule in self._rules:
			_validate_rule(rule)

	async def pre_tool_call(self, exec_ctx: "ExecutionContext", tool_name: str,
					  arguments: dict) -> tuple[bool, dict]:
		for rule in self._rules:
			if rule.get("event") != "pre_tool_call":
				continue
			condition = rule.get("condition", "")
			eval_ctx = {"tool_name": tool_name, "arguments": arguments}
			if condition and not _safe_eval_condition(condition, eval_ctx):
				continue
			action = rule.get("action", "")
			if action == "reject":
				msg = rule.get("params", {}).get("message", "Operation rejected by rule")
				logger.info(f"[hooks] REJECT: {msg}")
				self._last_rejection_reason = msg
				self._last_rejection_code = "tool.rejected_by_hooks"
				return False, arguments
			if action == "transform":
				set_fields = rule.get("params", {}).get("set", {})
				if set_fields:
					arguments = {**arguments, **set_fields}
		return True, arguments

	def pre_llm_call(self, exec_ctx: "ExecutionContext", messages: list[dict],
					 tools: list[dict] | None = None) -> tuple[list[dict], list[dict] | None]:
		for rule in self._rules:
			if rule.get("event") != "pre_llm_call":
				continue
			condition = rule.get("condition", "")
			if condition and not _safe_eval_condition(condition, {}):
				continue
			action = rule.get("action", "")
			if action == "inject":
				content = rule.get("params", {}).get("content", "")
				if content:
					messages = list(messages) + [{"role": "system", "content": content}]
			if action == "filter_tools":
				allowed = rule.get("params", {}).get("allowed", [])
				if allowed and tools:
					tools = [t for t in tools if t.get("function", {}).get("name", t.get("name", "")) in allowed]
		return messages, tools

	async def on_error(self, exec_ctx: "ExecutionContext", error: Exception) -> None:
		for rule in self._rules:
			if rule.get("event") != "on_error":
				continue
			action = rule.get("action", "")
			if action == "log":
				logger.info(f"[hooks:error] {error}")
			elif action == "notify":
				self._fire_notify(rule, {"event": "on_error", "error": str(error)})

	async def on_plan_created(self, exec_ctx: "ExecutionContext", plan_info: dict) -> None:
		"""English: This documentation describes the related engine component behavior.
中文：计划创建事件"""
		for rule in self._rules:
			if rule.get("event") != "on_plan_created":
				continue
			action = rule.get("action", "")
			if action == "log":
				logger.info(f"[hooks:plan_created] {plan_info}")
			elif action == "notify":
				self._fire_notify(rule, {"event": "on_plan_created", **(plan_info or {})})

	async def on_step_completed(self, exec_ctx: "ExecutionContext", step_info: dict) -> None:
		"""English: This documentation describes the related engine component behavior.
中文：步骤完成事件"""
		for rule in self._rules:
			if rule.get("event") != "on_step_completed":
				continue
			action = rule.get("action", "")
			if action == "log":
				logger.info(f"[hooks:step_completed] {step_info}")
			elif action == "notify":
				self._fire_notify(rule, {"event": "on_step_completed", **(step_info or {})})

	def _fire_notify(self, rule: dict, payload: dict) -> None:
		"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
触发 NOTIFY 动作"""
		callback = rule.get("params", {}).get("callback")
		if not callable(callback):
			raise ValueError("hooks notify action requires params.callback")
		callback(payload)

	async def post_tool_call(self, exec_ctx: "ExecutionContext", tool_name: str,
					   arguments: dict, result: "ToolOutput", duration_ms: int = 0) -> "ToolOutput":
		for rule in self._rules:
			if rule.get("event") != "post_tool_call":
				continue
			condition = rule.get("condition", "")
			result_str = result.context_view() if result else ""
			eval_ctx = {"tool_name": tool_name, "arguments": arguments, "result": result_str}
			if condition and not _safe_eval_condition(condition, eval_ctx):
				continue
			action = rule.get("action", "")
			if action == "log":
				logger.info(f"[hooks:audit] tool={tool_name} duration={duration_ms}ms")
		return result


_SAFE_OPS = {
	ast.Eq: operator.eq, ast.NotEq: operator.ne,
	ast.Lt: operator.lt, ast.LtE: operator.le,
	ast.Gt: operator.gt, ast.GtE: operator.ge,
	ast.In: lambda a, b: a in b,
	ast.NotIn: lambda a, b: a not in b,
}

_MAX_EVAL_DEPTH = 10
_ALLOWED_TYPES = (str, int, float, bool, list, dict, type(None))


def _safe_eval_condition(condition: str, context: dict) -> bool:
	"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
安全求值条件表达式。

	支持语法：比较（==, !=, <, >, in, not in）、布尔运算（and, or, not）、
	字符串方法（startswith, endswith, get）、下标访问和常量。
	"""
	if len(condition) > 500:
		raise ValueError(f"hooks condition is too long: {len(condition)} chars")
	#English: Bilingual note. 中文：校验 context 只包含安全类型
	for v in context.values():
		if not isinstance(v, _ALLOWED_TYPES):
			return False
	try:
		tree = ast.parse(condition.strip(), mode="eval")
		return bool(_eval_node(tree.body, context, depth=0))
	except Exception as exc:
		raise ValueError(f"hooks condition evaluation failed: {condition}") from exc


def _eval_node(node: ast.AST, ctx: dict, depth: int = 0) -> Any:
	if depth > _MAX_EVAL_DEPTH:
		return None
	if isinstance(node, ast.Constant):
		return node.value
	if isinstance(node, ast.List):
		return [_eval_node(el, ctx, depth + 1) for el in node.elts]
	if isinstance(node, ast.Name):
		return ctx.get(node.id)
	if isinstance(node, ast.Compare):
		left = _eval_node(node.left, ctx, depth + 1)
		for op_node, comp in zip(node.ops, node.comparators):
			right = _eval_node(comp, ctx, depth + 1)
			fn = _SAFE_OPS.get(type(op_node))
			if not fn or not fn(left, right):
				return False
			left = right
		return True
	if isinstance(node, ast.BoolOp):
		values = [_eval_node(v, ctx, depth + 1) for v in node.values]
		if isinstance(node.op, ast.And):
			return all(values)
		return any(values)
	if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
		return not _eval_node(node.operand, ctx, depth + 1)
	if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
		obj = _eval_node(node.func.value, ctx, depth + 1)
		method = node.func.attr
		if method in ("startswith", "endswith", "get") and obj is not None:
			args = [_eval_node(a, ctx, depth + 1) for a in node.args]
			return getattr(obj, method)(*args)
	if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant):
		obj = _eval_node(node.value, ctx, depth + 1)
		if isinstance(obj, (dict, list)):
			return obj[node.slice.value]
	return None


def _validate_rule(rule: dict[str, Any]) -> None:
	if not isinstance(rule, dict):
		raise ValueError("hooks rule must be an object")
	condition = str(rule.get("condition", ""))
	if condition:
		_validate_condition_syntax(condition)
	if rule.get("action") == "notify":
		params = rule.get("params", {})
		if "callback" in params and not callable(params["callback"]):
			raise ValueError("hooks notify params.callback must be callable")


def _validate_condition_syntax(condition: str) -> None:
	if len(condition) > 500:
		raise ValueError(f"hooks condition is too long: {len(condition)} chars")
	try:
		tree = ast.parse(condition.strip(), mode="eval")
	except SyntaxError as exc:
		raise ValueError(f"hooks condition syntax is invalid: {condition}") from exc
	for node in ast.walk(tree):
		if isinstance(node, ast.Call):
			if not isinstance(node.func, ast.Attribute) or node.func.attr not in {"startswith", "endswith", "get"}:
				raise ValueError(f"hooks condition uses unsupported call: {condition}")
		elif isinstance(node, ast.Attribute) and not isinstance(getattr(node, "ctx", None), ast.Load):
			raise ValueError(f"hooks condition uses unsupported attribute write: {condition}")
