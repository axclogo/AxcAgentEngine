"""PluginManager — 统一分发插件 hook。"""
import logging
from typing import Any

from axc_agent_engine.core.context import ExecutionContext
from axc_agent_engine.plugins.base import BasePlugin

logger = logging.getLogger(__name__)


class PluginHookRunner:
	"""Executes plugin hooks with the engine fail-open/fail-closed policy."""

	async def safe_call_async(self, plugin: BasePlugin, method: str, *args: Any) -> None:
		try:
			await getattr(plugin, method)(*args)
		except Exception as e:
			if getattr(plugin, "fail_closed", False):
				raise
			logger.warning(f"Plugin {plugin.name} {method} error: {e}")

	def safe_call_sync(self, plugin: BasePlugin, method: str, *args: Any) -> Any:
		try:
			return getattr(plugin, method)(*args)
		except Exception as e:
			if getattr(plugin, "fail_closed", False):
				raise
			logger.warning(f"Plugin {plugin.name} {method} error: {e}")
			return None

	async def apply_pre_tool_call(
		self,
		plugins: list[BasePlugin],
		ctx: ExecutionContext,
		tool_name: str,
		arguments: dict,
	) -> tuple[bool, dict]:
		for p in plugins:
			try:
				allowed, arguments = await p.pre_tool_call(ctx, tool_name, arguments)
				if not allowed:
					return False, arguments
			except Exception as e:
				logger.warning(f"Plugin {p.name} pre_tool_call error: {e}")
				if getattr(p, "fail_closed", False):
					return False, arguments
		return True, arguments

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
			try:
				output = await p.post_tool_call(ctx, tool_name, arguments, output, duration_ms)
			except Exception as e:
				logger.warning(f"Plugin {p.name} post_tool_call error: {e}")
				if getattr(p, "fail_closed", False):
					raise
		return output


class PluginManager:
	"""封装插件列表，并提供统一的 hook 分发方法。"""

	def __init__(self, plugins: list[BasePlugin]) -> None:
		self._plugins = plugins
		self._hook_runner = PluginHookRunner()

	@property
	def plugins(self) -> list[BasePlugin]:
		return self._plugins

	# ── 异步 hooks ──

	async def on_execution_start(self, ctx: ExecutionContext) -> None:
		"""通知所有插件执行已开始。"""
		for p in self._plugins:
			await self._safe_call_async(p, "on_execution_start", ctx)

	async def on_execution_end(self, ctx: ExecutionContext, result: str, error: str) -> None:
		"""通知所有插件执行已结束。"""
		for p in self._plugins:
			await self._safe_call_async(p, "on_execution_end", ctx, result, error)

	async def on_execution_complete(self, ctx: ExecutionContext, result: str, trace: dict) -> str:
		"""执行完成后的流水线，插件可修改最终结果。"""
		for p in self._plugins:
			try:
				result = await p.on_execution_complete(ctx, result, trace)
			except Exception as e:
				if getattr(p, "fail_closed", False):
					raise
				logger.warning(f"Plugin {p.name} on_execution_complete error: {e}")
		return result

	async def on_round_end(
		self,
		ctx: ExecutionContext,
		user_message: str,
		assistant_message: str,
		tool_calls: list,
	) -> None:
		"""通知所有插件一轮执行已结束。"""
		for p in self._plugins:
			await self._safe_call_async(p, "on_round_end", ctx, user_message, assistant_message, tool_calls)

	async def on_error(self, ctx: ExecutionContext, error: Exception) -> None:
		"""通知所有插件发生错误。"""
		for p in self._plugins:
			await self._safe_call_async(p, "on_error", ctx, error)

	async def on_plan_created(self, ctx: ExecutionContext, plan_info: dict) -> None:
		"""通知所有插件计划已创建。"""
		for p in self._plugins:
			await self._safe_call_async(p, "on_plan_created", ctx, plan_info)

	async def on_step_completed(self, ctx: ExecutionContext, step_info: dict) -> None:
		"""通知所有插件步骤已完成。"""
		for p in self._plugins:
			await self._safe_call_async(p, "on_step_completed", ctx, step_info)

	async def post_llm_call(self, ctx: ExecutionContext, messages: list[dict], response: dict, duration_ms: int) -> None:
		"""LLM 调用后通知所有插件。"""
		for p in self._plugins:
			await self._safe_call_async(p, "post_llm_call", ctx, messages, response, duration_ms)

	async def apply_pre_tool_call(self, ctx: ExecutionContext, tool_name: str, arguments: dict) -> tuple[bool, dict]:
		"""执行所有插件的 pre_tool_call hooks，返回 (是否允许, 修改后的参数)。"""
		return await self._hook_runner.apply_pre_tool_call(self._plugins, ctx, tool_name, arguments)

	async def apply_post_tool_call(
		self,
		ctx: ExecutionContext,
		tool_name: str,
		arguments: dict,
		output: Any,
		duration_ms: int,
	) -> Any:
		"""执行所有插件的 post_tool_call hooks，返回修改后的 ToolOutput。"""
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
		"""通知工具调用失败；用于观测类插件补齐失败链路。"""
		for p in self._plugins:
			try:
				await p.on_tool_call_failed(ctx, tool_name, arguments, error, duration_ms)
			except Exception as e:
				logger.warning(f"Plugin {p.name} on_tool_call_failed error: {e}")
				if getattr(p, "fail_closed", False):
					raise

	# ── 同步 hooks ──

	def collect_context(self, ctx: ExecutionContext) -> str:
		"""收集所有插件要注入的上下文。"""
		parts = []
		for p in self._plugins:
			result = self._safe_call_sync(p, "inject_context", ctx)
			if result:
				parts.append(result)
		return "\n\n".join(parts)

	def transform_messages(self, messages: list[dict], ctx: ExecutionContext, current_message: str) -> list[dict]:
		"""按顺序应用所有插件的消息转换。"""
		for p in self._plugins:
			result = self._safe_call_sync(p, "transform_messages", messages, ctx, current_message)
			if result is not None:
				messages = result
		return messages

	def check_should_stop(self, ctx: ExecutionContext) -> tuple[bool, str]:
		"""检查是否有插件要求停止执行。"""
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
		"""应用所有插件的 pre-LLM-call hooks。"""
		for p in self._plugins:
			result = self._safe_call_sync(p, "pre_llm_call", ctx, messages, tools)
			if result is not None:
				messages, tools = result
		return messages, tools

	# ── 内部辅助方法 ──

	def _safe_call_sync(self, plugin: BasePlugin, method: str, *args: Any) -> Any:
		"""安全调用同步插件方法；fail_closed 插件会继续抛出异常。"""
		return self._hook_runner.safe_call_sync(plugin, method, *args)

	async def _safe_call_async(self, plugin: BasePlugin, method: str, *args: Any) -> None:
		"""安全调用异步插件方法；fail_closed 插件会继续抛出异常。"""
		await self._hook_runner.safe_call_async(plugin, method, *args)
