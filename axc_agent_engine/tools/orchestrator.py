"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
工具执行编排器。

把工具调用划分为批次：连续只读工具并发执行，写工具串行执行。
每次调用流程：pre_hooks → execute → post_hooks。

ToolOutput.context_view() 负责上下文安全序列化。"""
import asyncio
from typing import Any

from axc_agent_engine.observability.audit import AuditEventType
from axc_agent_engine.core.context import ExecutionContext
from axc_agent_engine.core.errors import ErrorCategory, ErrorEnvelope
from axc_agent_engine.core.plugin_manager import PluginManager
from axc_agent_engine.tools.audit import audit_runtime
from axc_agent_engine.tools.batches import partition_tool_calls
from axc_agent_engine.tools.executor import ToolResult, execute_tool
from axc_agent_engine.tools.policy import evaluate_tool_policy
from axc_agent_engine.tools.registry import ToolRegistry
from axc_agent_engine.tools.runtime import (
	ToolCallRuntime,
	pop_current_tool_runtime,
	push_current_tool_runtime,
	step_timeout,
	tool_context,
	tool_runtime,
)


class ToolExecutionPipeline:
	def __init__(self, registry: ToolRegistry, plugin_manager: PluginManager, ctx: ExecutionContext) -> None:
		self.registry = registry
		self.plugin_manager = plugin_manager
		self.ctx = ctx

	async def execute(self, tool_calls: list[dict]) -> list[ToolResult]:
		self.ctx.check_cancelled()
		batches = partition_tool_calls(tool_calls, self.registry)
		results: list[ToolResult] = []
		for batch in batches:
			self.ctx.check_cancelled()
			if batch["concurrent"] and len(batch["calls"]) > 1:
				tasks = [self.execute_single(tc) for tc in batch["calls"]]
				results.extend(await asyncio.gather(*tasks))
			else:
				for tc in batch["calls"]:
					self.ctx.check_cancelled()
					results.append(await self.execute_single(tc))
		return results

	async def execute_single(self, tc: dict) -> ToolResult:
		return await _execute_single_with_timeout(tc, self.registry, self.plugin_manager, self.ctx)


async def execute_tool_calls(
	tool_calls: list[dict], registry: ToolRegistry,
	plugins: list[Any] | PluginManager, ctx: ExecutionContext,
) -> list[ToolResult]:
	"""English: This documentation describes the related engine component behavior.
中文：按编排策略执行工具调用。"""
	plugin_manager = plugins if isinstance(plugins, PluginManager) else PluginManager(plugins)
	return await ToolExecutionPipeline(registry, plugin_manager, ctx).execute(tool_calls)


async def _execute_single_with_timeout(
	tc: dict, registry: ToolRegistry, plugin_manager: PluginManager, ctx: ExecutionContext,
) -> ToolResult:
	"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
执行单个工具调用，并用 step_timeout 约束完整编排耗时。"""
	runtime = tool_runtime(tc, ctx, registry)
	timeout = _effective_orchestration_timeout(ctx, runtime)
	if timeout <= 0:
		return await _execute_single_runtime(runtime, plugin_manager, ctx)
	try:
		return await asyncio.wait_for(_execute_single_runtime(runtime, plugin_manager, ctx), timeout=timeout)
	except asyncio.TimeoutError:
		return await _fail_tool_call(
			ctx, runtime, AuditEventType.TOOL_CALL_FAILED, plugin_manager,
			code="tool.call_timeout",
			message=f"Tool call timeout ({timeout}s)",
			category=ErrorCategory.TIMEOUT,
			retryable=True,
			details={"tool_name": runtime.name, "timeout": timeout},
		)


async def _execute_single(
	tc: dict, registry: ToolRegistry, plugin_manager: PluginManager, ctx: ExecutionContext,
) -> ToolResult:
	return await _execute_single_runtime(tool_runtime(tc, ctx, registry), plugin_manager, ctx)


async def _execute_single_runtime(
	runtime: ToolCallRuntime, plugin_manager: PluginManager, ctx: ExecutionContext,
) -> ToolResult:
	"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
执行单个工具调用：pre_hooks → resolve → execute → post_hooks。"""
	tool_context_key = 0
	try:
		ctx.check_cancelled()
		tool_context_key = push_current_tool_runtime(ctx, runtime)
		decision = await plugin_manager.apply_pre_tool_call(ctx, runtime.name, runtime.arguments)
		runtime.arguments = decision.arguments
		if not decision.allowed:
			reason = decision.reason or "工具调用被插件拒绝，但插件未提供具体原因"
			plugin_name = decision.plugin_name or "unknown"
			message = f"工具调用被插件 {plugin_name} 拒绝: {reason}"
			return await _fail_tool_call(
				ctx, runtime, AuditEventType.TOOL_CALL_REJECTED, plugin_manager,
				code=decision.code or "tool.rejected_by_plugin",
				message=message,
				category=ErrorCategory.POLICY,
				details={
					"tool_name": runtime.name,
					"plugin_name": plugin_name,
					"reason": reason,
					**(decision.details or {}),
				},
			)
		if not runtime.tool_def:
			return await _fail_tool_call(
				ctx, runtime, AuditEventType.TOOL_CALL_REJECTED, plugin_manager,
				code="tool.unknown",
				message=f"Unknown tool: {runtime.name}",
				category=ErrorCategory.TOOL,
			)
		decision = evaluate_tool_policy(ctx, runtime)
		if not decision.allowed:
			return await _fail_tool_call(
				ctx, runtime, AuditEventType.TOOL_CALL_REJECTED, plugin_manager, error=decision.to_error())
		await audit_runtime(
			ctx, runtime, AuditEventType.TOOL_CALL_STARTED,
			metadata={"arguments_keys": sorted(runtime.arguments.keys())},
		)
		try:
			result = await execute_tool(runtime.tool_def, runtime.arguments, runtime.tool_call_id, tool_context(ctx, runtime))
		except asyncio.CancelledError:
			ctx.cancel("cancelled")
			raise
		except TypeError as e:
			return await _fail_tool_call(
				ctx, runtime, AuditEventType.TOOL_CALL_FAILED, plugin_manager,
				code="tool.contract_error",
				message=str(e),
				category=ErrorCategory.CONTRACT,
			)
		if result.output:
			ctx.check_cancelled()
			result.output = await plugin_manager.apply_post_tool_call(
				ctx, runtime.name, runtime.arguments, result.output, result.duration_ms)
		if result.success:
			await audit_runtime(ctx, runtime, AuditEventType.TOOL_CALL_COMPLETED, duration_ms=result.duration_ms)
			return result
		return await _fail_tool_call(
			ctx, runtime, AuditEventType.TOOL_CALL_FAILED, plugin_manager,
			code="tool.execution_failed",
			message=result.error,
			category=ErrorCategory.TOOL,
			duration_ms=result.duration_ms,
		)
	finally:
		pop_current_tool_runtime(ctx, tool_context_key)


def _effective_orchestration_timeout(ctx: ExecutionContext, runtime: ToolCallRuntime) -> float:
	timeout = step_timeout(ctx)
	if runtime.name not in {"agent_call", "swarm_dispatch"}:
		return timeout
	requested = _argument_timeout(runtime.arguments)
	return max(timeout, requested) if requested > 0 else timeout


def _argument_timeout(arguments: dict) -> float:
	try:
		return float(arguments.get("timeout", 0) or 0)
	except (TypeError, ValueError):
		return 0.0


async def _fail_tool_call(
	ctx: ExecutionContext,
	runtime: ToolCallRuntime,
	audit_type: AuditEventType,
	plugin_manager: PluginManager,
	code: str = "",
	message: str = "",
	category: ErrorCategory = ErrorCategory.TOOL,
	retryable: bool = False,
	details: dict | None = None,
	error: ErrorEnvelope | None = None,
	duration_ms: int = 0,
) -> ToolResult:
	error = error or ErrorEnvelope(
		code=code,
		message=message,
		category=category,
		retryable=retryable,
		details=details or {"tool_name": runtime.name},
	)
	await audit_runtime(
		ctx, runtime, audit_type,
		allowed=False, duration_ms=duration_ms, error=error,
	)
	if plugin_manager.plugins:
		tool_context_key = push_current_tool_runtime(ctx, runtime)
		try:
			await plugin_manager.on_tool_call_failed(
				ctx,
				runtime.name,
				runtime.arguments,
				error.to_dict(),
				duration_ms,
			)
		finally:
			pop_current_tool_runtime(ctx, tool_context_key)
	return ToolResult(
		tool_call_id=runtime.tool_call_id,
		tool_name=runtime.name,
		arguments=runtime.arguments,
		error=error.message,
		success=False,
	)
