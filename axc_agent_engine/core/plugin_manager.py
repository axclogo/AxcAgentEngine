"""PluginManager — 统一分发插件 hook。"""

from copy import deepcopy
from dataclasses import replace
from typing import Any

from axc_agent_engine.core.context import ExecutionContext
from axc_agent_engine.plugins.base import BasePlugin, PreToolCallDecision


class PluginManager:
	"""Dispatch hooks in plugin order.
	封装插件列表，并提供统一的 hook 分发方法。"""

	def __init__(self, plugins: list[BasePlugin]) -> None:
		self._plugins = plugins

	@property
	def plugins(self) -> list[BasePlugin]:
		return self._plugins

	def fork_checkpoint_fields(self, ctx: ExecutionContext) -> dict:
		"""Collect portable state only when every plugin supports the cut. 仅在所有插件均支持该切点时采集状态。"""
		blocked = [plugin.name for plugin in self._plugins if not plugin.supports_session_fork]
		if blocked:
			return {"fork_blocker": f"Session state cannot be forked for plugins: {', '.join(blocked)}"}
		states = {}
		for plugin in self._plugins:
			state = plugin.capture_session(ctx)
			if state is not None and not isinstance(state, dict):
				raise TypeError(f"{plugin.name}.capture_session must return a dict or None")
			states[plugin.name] = state
		return {"fork_state": states}

	def fork_scopes(self, scopes: dict, source: dict, target: dict, states: dict | None) -> dict:
		names = {p.name for p in self._plugins}
		if scopes.keys() - names or (states is not None and states.keys() != names):
			raise ValueError("Fork requires the recorded session plugins")
		resolved = {}
		for plugin in self._plugins:
			if not plugin.supports_session_fork:
				raise ValueError(f"Plugin {plugin.name} cannot fork its session")
			state = deepcopy(states[plugin.name]) if states is not None else None
			scope = plugin.fork_scope(scopes.get(plugin.name), source, target, state)
			if scope is not None:
				if not isinstance(scope, str) or not scope.strip():
					raise TypeError(f"{plugin.name}.fork_scope must return a nonblank string or None")
				resolved[plugin.name] = scope
		return resolved

	async def restore_session(self, ctx: ExecutionContext, states: dict) -> None:
		for plugin in self._plugins:
			state = states[plugin.name]
			if state is not None:
				await plugin.restore_session(ctx, deepcopy(state))

	# Asynchronous hooks. 中文：── 异步 hooks ──

	async def on_execution_start(self, ctx: ExecutionContext) -> None:
		"""Notify plugins when execution begins. 通知所有插件执行已开始。"""
		for p in self._plugins:
			await p.on_execution_start(ctx)

	async def on_execution_end(self, ctx: ExecutionContext, result: str, error: str) -> None:
		"""Notify plugins when execution ends. 通知所有插件执行已结束。"""
		for p in self._plugins:
			await p.on_execution_end(ctx, result, error)

	async def on_execution_complete(self, ctx: ExecutionContext, result: str, trace: dict) -> str:
		"""Pass the final result through completion hooks. 执行完成后的流水线，插件可修改最终结果。"""
		for p in self._plugins:
			result = await p.on_execution_complete(ctx, result, trace)
		return result

	async def on_round_end(
		self,
		ctx: ExecutionContext,
		user_message: str,
		assistant_message: str,
		tool_calls: list,
	) -> None:
		"""Notify plugins at the end of a round. 通知所有插件一轮执行已结束。"""
		for p in self._plugins:
			await p.on_round_end(ctx, user_message, assistant_message, tool_calls)

	async def on_error(self, ctx: ExecutionContext, error: Exception) -> None:
		"""Notify plugins of an execution error. 通知所有插件发生错误。"""
		for p in self._plugins:
			await p.on_error(ctx, error)

	async def on_plan_created(self, ctx: ExecutionContext, plan_info: dict) -> None:
		"""Notify plugins of a new plan. 通知所有插件计划已创建。"""
		for p in self._plugins:
			await p.on_plan_created(ctx, plan_info)

	async def on_step_completed(self, ctx: ExecutionContext, step_info: dict) -> None:
		"""Notify plugins of a completed step. 通知所有插件步骤已完成。"""
		for p in self._plugins:
			await p.on_step_completed(ctx, step_info)

	async def post_llm_call(
		self, ctx: ExecutionContext, messages: list[dict], response: dict, duration_ms: int
	) -> None:
		"""LLM 调用后通知所有插件。"""
		for p in self._plugins:
			await p.post_llm_call(ctx, messages, response, duration_ms)

	async def apply_pre_tool_call(self, ctx: ExecutionContext, tool_name: str, arguments: dict) -> PreToolCallDecision:
		"""Run pre-tool hooks and collect their decision.
		执行所有插件的 pre_tool_call hooks，返回带拒绝原因的决策。"""
		for plugin in self._plugins:
			raw = await plugin.pre_tool_call(ctx, tool_name, arguments)
			decision = _pre_tool_decision(raw, plugin.name)
			if not decision.allowed:
				return decision
			arguments = decision.arguments
		return PreToolCallDecision(True, arguments)

	async def apply_post_tool_call(
		self,
		ctx: ExecutionContext,
		tool_name: str,
		arguments: dict,
		output: Any,
		duration_ms: int,
	) -> Any:
		"""Apply tool output transformations.
		执行所有插件的 post_tool_call hooks，返回修改后的 ToolOutput。"""
		for plugin in self._plugins:
			output = await plugin.post_tool_call(ctx, tool_name, arguments, output, duration_ms)
		return output

	async def on_tool_call_failed(
		self,
		ctx: ExecutionContext,
		tool_name: str,
		arguments: dict,
		error: dict,
		duration_ms: int,
	) -> None:
		"""Notify observers of a failed tool call. 通知工具调用失败；用于观测类插件补齐失败链路。"""
		for p in self._plugins:
			await p.on_tool_call_failed(ctx, tool_name, arguments, error, duration_ms)

	# Synchronous hooks. 中文：── 同步 hooks ──

	def collect_context(self, ctx: ExecutionContext) -> str:
		"""Collect context contributed by all plugins. 收集所有插件要注入的上下文。"""
		parts = []
		for p in self._plugins:
			result = p.inject_context(ctx)
			if result:
				parts.append(result)
		return "\n\n".join(parts)

	def transform_messages(self, messages: list[dict], ctx: ExecutionContext, current_message: str) -> list[dict]:
		"""Apply plugin transformations in registration order. 按顺序应用所有插件的消息转换。"""
		for p in self._plugins:
			messages = p.transform_messages(messages, ctx, current_message)
			if not isinstance(messages, list):
				raise TypeError(f"{p.name}.transform_messages must return a list")
		return messages

	def check_should_stop(self, ctx: ExecutionContext) -> tuple[bool, str]:
		"""Check whether a plugin requested a stop. 检查是否有插件要求停止执行。"""
		for p in self._plugins:
			stop, reason = p.should_stop(ctx)
			if not isinstance(stop, bool):
				raise TypeError(f"{p.name}.should_stop must return a boolean stop flag")
			if stop:
				return True, reason
		return False, ""

	def apply_pre_llm_call(
		self,
		ctx: ExecutionContext,
		messages: list[dict],
		tools: list[dict] | None,
	) -> tuple[list[dict], list[dict] | None]:
		"""Apply model request hooks.
		应用所有插件的 pre-LLM-call hooks。"""
		for p in self._plugins:
			messages, tools = p.pre_llm_call(ctx, messages, tools)
		return messages, tools


def _pre_tool_decision(raw: tuple[bool, dict] | PreToolCallDecision, plugin_name: str) -> PreToolCallDecision:
	if isinstance(raw, PreToolCallDecision):
		decision = raw
	elif isinstance(raw, tuple) and len(raw) == 2:
		decision = PreToolCallDecision(*raw)
	else:
		raise TypeError(f"{plugin_name}.pre_tool_call must return (allowed, arguments) or PreToolCallDecision")
	if not isinstance(decision.allowed, bool):
		raise TypeError(f"{plugin_name}.pre_tool_call allowed must be a boolean")
	if not isinstance(decision.arguments, dict):
		raise TypeError(f"{plugin_name}.pre_tool_call arguments must be a dict")
	if not decision.allowed and not decision.plugin_name:
		return replace(decision, plugin_name=plugin_name)
	return decision
