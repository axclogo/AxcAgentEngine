"""PluginManager — 统一分发插件 hook。"""
import logging
from dataclasses import dataclass
from typing import Any

from axc_agent_engine.core.context import ExecutionContext
from axc_agent_engine.plugins.base import BasePlugin

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PreToolCallDecision:
	allowed: bool
	arguments: dict
	plugin_name: str = ""
	reason: str = ""
	code: str = "tool.rejected_by_plugin"
	details: dict | None = None


class PluginHookRunner:
	"""Executes plugin hooks and propagates plugin errors.
中文：此文档说明相关引擎组件的行为。"""

	async def safe_call_async(self, plugin: BasePlugin, method: str, *args: Any) -> None:
		await getattr(plugin, method)(*args)

	def safe_call_sync(self, plugin: BasePlugin, method: str, *args: Any) -> Any:
		return getattr(plugin, method)(*args)

	async def apply_pre_tool_call(
		self,
		plugins: list[BasePlugin],
		ctx: ExecutionContext,
		tool_name: str,
		arguments: dict,
	) -> PreToolCallDecision:
		for p in plugins:
			raw = await p.pre_tool_call(ctx, tool_name, arguments)
			decision = _normalize_pre_tool_decision(raw, p, arguments)
			arguments = decision.arguments
			if not decision.allowed:
				return decision
		return PreToolCallDecision(True, arguments)

	async def apply_post_tool_call(
		self,
		plugins: list[BasePlugin],
		ctx: ExecutionContext,
		tool_name: str,
		arguments: dict,
		output: Any,
		duration_ms: int,
	) -> Any:
		for p in plugins:
			output = await p.post_tool_call(ctx, tool_name, arguments, output, duration_ms)
		return output


class PluginManager:
	"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
封装插件列表，并提供统一的 hook 分发方法。"""

	def __init__(self, plugins: list[BasePlugin]) -> None:
		self._plugins = plugins
		self._hook_runner = PluginHookRunner()

	@property
	def plugins(self) -> list[BasePlugin]:
		return self._plugins

	#English: Bilingual note. 中文：── 异步 hooks ──

	async def on_execution_start(self, ctx: ExecutionContext) -> None:
		"""English: This documentation describes the related engine component behavior.
中文：通知所有插件执行已开始。"""
		for p in self._plugins:
			await self._safe_call_async(p, "on_execution_start", ctx)

	async def on_execution_end(self, ctx: ExecutionContext, result: str, error: str) -> None:
		"""English: This documentation describes the related engine component behavior.
中文：通知所有插件执行已结束。"""
		for p in self._plugins:
			await self._safe_call_async(p, "on_execution_end", ctx, result, error)

	async def on_execution_complete(self, ctx: ExecutionContext, result: str, trace: dict) -> str:
		"""English: This documentation describes the related engine component behavior.
中文：执行完成后的流水线，插件可修改最终结果。"""
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
		"""English: This documentation describes the related engine component behavior.
中文：通知所有插件一轮执行已结束。"""
		for p in self._plugins:
			await self._safe_call_async(p, "on_round_end", ctx, user_message, assistant_message, tool_calls)

	async def on_error(self, ctx: ExecutionContext, error: Exception) -> None:
		"""English: This documentation describes the related engine component behavior.
中文：通知所有插件发生错误。"""
		for p in self._plugins:
			await self._safe_call_async(p, "on_error", ctx, error)

	async def on_plan_created(self, ctx: ExecutionContext, plan_info: dict) -> None:
		"""English: This documentation describes the related engine component behavior.
中文：通知所有插件计划已创建。"""
		for p in self._plugins:
			await self._safe_call_async(p, "on_plan_created", ctx, plan_info)

	async def on_step_completed(self, ctx: ExecutionContext, step_info: dict) -> None:
		"""English: This documentation describes the related engine component behavior.
中文：通知所有插件步骤已完成。"""
		for p in self._plugins:
			await self._safe_call_async(p, "on_step_completed", ctx, step_info)

	async def post_llm_call(self, ctx: ExecutionContext, messages: list[dict], response: dict, duration_ms: int) -> None:
		"""LLM 调用后通知所有插件。"""
		for p in self._plugins:
			await self._safe_call_async(p, "post_llm_call", ctx, messages, response, duration_ms)

	async def apply_pre_tool_call(self, ctx: ExecutionContext, tool_name: str,
								  arguments: dict) -> PreToolCallDecision:
		"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
执行所有插件的 pre_tool_call hooks，返回带拒绝原因的决策。"""
		return await self._hook_runner.apply_pre_tool_call(self._plugins, ctx, tool_name, arguments)

	async def apply_post_tool_call(
		self,
		ctx: ExecutionContext,
		tool_name: str,
		arguments: dict,
		output: Any,
		duration_ms: int,
	) -> Any:
		"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
执行所有插件的 post_tool_call hooks，返回修改后的 ToolOutput。"""
		return await self._hook_runner.apply_post_tool_call(
			self._plugins,
			ctx,
			tool_name,
			arguments,
			output,
			duration_ms,
		)

	async def on_tool_call_failed(
		self,
		ctx: ExecutionContext,
		tool_name: str,
		arguments: dict,
		error: dict,
		duration_ms: int,
	) -> None:
		"""English: This documentation describes the related engine component behavior.
中文：通知工具调用失败；用于观测类插件补齐失败链路。"""
		for p in self._plugins:
			await p.on_tool_call_failed(ctx, tool_name, arguments, error, duration_ms)

	#English: Bilingual note. 中文：── 同步 hooks ──

	def collect_context(self, ctx: ExecutionContext) -> str:
		"""English: This documentation describes the related engine component behavior.
中文：收集所有插件要注入的上下文。"""
		parts = []
		for p in self._plugins:
			result = self._safe_call_sync(p, "inject_context", ctx)
			if result:
				parts.append(result)
		return "\n\n".join(parts)

	def transform_messages(self, messages: list[dict], ctx: ExecutionContext, current_message: str) -> list[dict]:
		"""English: This documentation describes the related engine component behavior.
中文：按顺序应用所有插件的消息转换。"""
		for p in self._plugins:
			result = self._safe_call_sync(p, "transform_messages", messages, ctx, current_message)
			if result is not None:
				messages = result
		return messages

	def check_should_stop(self, ctx: ExecutionContext) -> tuple[bool, str]:
		"""English: This documentation describes the related engine component behavior.
中文：检查是否有插件要求停止执行。"""
		for p in self._plugins:
			result = self._safe_call_sync(p, "should_stop", ctx)
			if result is not None:
				stop, reason = result
				if stop:
					return True, reason
		return False, ""

	def apply_pre_llm_call(
		self,
		ctx: ExecutionContext,
		messages: list[dict],
		tools: list[dict] | None,
	) -> tuple[list[dict], list[dict] | None]:
		"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
应用所有插件的 pre-LLM-call hooks。"""
		for p in self._plugins:
			result = self._safe_call_sync(p, "pre_llm_call", ctx, messages, tools)
			if result is not None:
				messages, tools = result
		return messages, tools

	#English: Source note. 中文：── 内部辅助方法 ──

	def _safe_call_sync(self, plugin: BasePlugin, method: str, *args: Any) -> Any:
		"""English: Bilingual documentation follows.
	中文：以下为双语文档说明。
	调用同步插件方法；插件异常直接抛出。"""
		return self._hook_runner.safe_call_sync(plugin, method, *args)

	async def _safe_call_async(self, plugin: BasePlugin, method: str, *args: Any) -> None:
		"""English: Bilingual documentation follows.
	中文：以下为双语文档说明。
	调用异步插件方法；插件异常直接抛出。"""
		await self._hook_runner.safe_call_async(plugin, method, *args)


def _normalize_pre_tool_decision(raw: Any, plugin: BasePlugin,
								 previous_arguments: dict) -> PreToolCallDecision:
	plugin_name = str(getattr(plugin, "name", "") or plugin.__class__.__name__)
	if isinstance(raw, PreToolCallDecision):
		return raw
	if isinstance(raw, dict):
		return PreToolCallDecision(
			allowed=bool(raw.get("allowed", True)),
			arguments=dict(raw.get("arguments") or previous_arguments),
			plugin_name=str(raw.get("plugin_name") or plugin_name),
			reason=str(raw.get("reason") or raw.get("message") or ""),
			code=str(raw.get("code") or "tool.rejected_by_plugin"),
			details=dict(raw.get("details") or {}),
		)
	if isinstance(raw, tuple):
		allowed = bool(raw[0]) if len(raw) > 0 else True
		arguments = dict(raw[1] if len(raw) > 1 and raw[1] is not None else previous_arguments)
		reason = str(raw[2]) if len(raw) > 2 and raw[2] else _plugin_rejection_reason(plugin)
		code = str(raw[3]) if len(raw) > 3 and raw[3] else _plugin_rejection_code(plugin)
		return PreToolCallDecision(
			allowed=allowed,
			arguments=arguments,
			plugin_name=plugin_name if not allowed else "",
			reason=reason,
			code=code,
		)
	return PreToolCallDecision(bool(raw), previous_arguments, plugin_name=plugin_name)


def _plugin_rejection_reason(plugin: BasePlugin) -> str:
	return str(
		getattr(plugin, "last_rejection_reason", "")
		or getattr(plugin, "_last_rejection_reason", "")
		or getattr(plugin, "_stop_reason", "")
		or ""
	)


def _plugin_rejection_code(plugin: BasePlugin) -> str:
	return str(
		getattr(plugin, "last_rejection_code", "")
		or getattr(plugin, "_last_rejection_code", "")
		or "tool.rejected_by_plugin"
	)
