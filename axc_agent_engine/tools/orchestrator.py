"""工具执行编排器。

把工具调用划分为批次：连续只读工具并发执行，写工具串行执行。
每次调用流程：pre_hooks → execute → post_hooks。

ToolOutput.compact_view() 负责上下文安全序列化。
"""
import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from axc_agent_engine.observability.audit import AuditEvent, AuditEventType
from axc_agent_engine.core.context import ExecutionContext
from axc_agent_engine.core.errors import ErrorCategory, ErrorEnvelope
from axc_agent_engine.core.plugin_manager import PluginManager
from axc_agent_engine.runtime.policy import CapabilityPolicyEvaluator, PolicyDecision, PolicyRequest
from axc_agent_engine.tools.context import ToolContext
from axc_agent_engine.tools.executor import ToolResult, execute_tool
from axc_agent_engine.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


@dataclass
class ToolCallRuntime:
	"""单次工具调用的运行时上下文。"""
	name: str
	arguments: dict
	tool_call_id: str
	actor: str
	session_id: str
	tool_def: Any = None


def partition_tool_calls(tool_calls: list[dict], registry: ToolRegistry) -> list[dict]:
	"""把工具调用划分为批次：连续只读调用并发，其他调用串行。"""
	batches: list[dict] = []
	for tc in tool_calls:
		name = tc.get("name", "")
		tool_def = registry.get(name)
		is_read_only = tool_def.is_read_only if tool_def else False
		if is_read_only:
			if batches and batches[-1]["concurrent"]:
				batches[-1]["calls"].append(tc)
			else:
				batches.append({"concurrent": True, "calls": [tc]})
		else:
			batches.append({"concurrent": False, "calls": [tc]})
	return batches


class ToolBatchPlanner:
	def plan(self, tool_calls: list[dict], registry: ToolRegistry) -> list[dict]:
		return partition_tool_calls(tool_calls, registry)


class ToolExecutionPipeline:
	def __init__(self, registry: ToolRegistry, plugin_manager: PluginManager, ctx: ExecutionContext) -> None:
		self.registry = registry
		self.plugin_manager = plugin_manager
		self.ctx = ctx
		self.planner = ToolBatchPlanner()

	async def execute(self, tool_calls: list[dict]) -> list[ToolResult]:
		batches = self.planner.plan(tool_calls, self.registry)
		results: list[ToolResult] = []
		for batch in batches:
			if batch["concurrent"] and len(batch["calls"]) > 1:
				tasks = [self.execute_single(tc) for tc in batch["calls"]]
				results.extend(await asyncio.gather(*tasks))
			else:
				for tc in batch["calls"]:
					results.append(await self.execute_single(tc))
		return results

	async def execute_single(self, tc: dict) -> ToolResult:
		return await _execute_single_with_timeout(tc, self.registry, self.plugin_manager, self.ctx)


async def execute_tool_calls(
	tool_calls: list[dict], registry: ToolRegistry,
	plugins: list[Any] | PluginManager, ctx: ExecutionContext,
) -> list[ToolResult]:
	"""按编排策略执行工具调用。"""
	plugin_manager = plugins if isinstance(plugins, PluginManager) else PluginManager(plugins)
	return await ToolExecutionPipeline(registry, plugin_manager, ctx).execute(tool_calls)


async def _execute_single_with_timeout(
	tc: dict, registry: ToolRegistry, plugin_manager: PluginManager, ctx: ExecutionContext,
) -> ToolResult:
	"""执行单个工具调用，并用 step_timeout 约束完整编排耗时。"""
	timeout = _step_timeout(ctx)
	if timeout <= 0:
		return await _execute_single(tc, registry, plugin_manager, ctx)
	try:
		return await asyncio.wait_for(_execute_single(tc, registry, plugin_manager, ctx), timeout=timeout)
	except asyncio.TimeoutError:
		runtime = _tool_runtime(tc, ctx, registry)
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
	"""执行单个工具调用：pre_hooks → resolve → execute → post_hooks。"""
	runtime = _tool_runtime(tc, ctx, registry)
	tool_context_key = 0
	try:
		tool_context_key = _push_current_tool_runtime(ctx, runtime)
		allowed, runtime.arguments = await plugin_manager.apply_pre_tool_call(ctx, runtime.name, runtime.arguments)
		if not allowed:
			return await _fail_tool_call(
				ctx, runtime, AuditEventType.TOOL_CALL_REJECTED, plugin_manager,
				code="tool.rejected_by_plugin",
				message="Operation rejected by plugin",
				category=ErrorCategory.POLICY,
			)
		if not runtime.tool_def:
			return await _fail_tool_call(
				ctx, runtime, AuditEventType.TOOL_CALL_REJECTED, plugin_manager,
				code="tool.unknown",
				message=f"Unknown tool: {runtime.name}",
				category=ErrorCategory.TOOL,
			)
		decision = _evaluate_tool_policy(ctx, runtime)
		if not decision.allowed:
			return await _fail_tool_call(
				ctx, runtime, AuditEventType.TOOL_CALL_REJECTED, plugin_manager, error=decision.to_error())
		await _audit_runtime(
			ctx, runtime, AuditEventType.TOOL_CALL_STARTED,
			metadata={"arguments_keys": sorted(runtime.arguments.keys())},
		)
		try:
			result = await execute_tool(runtime.tool_def, runtime.arguments, runtime.tool_call_id, _tool_context(ctx))
		except TypeError as e:
			return await _fail_tool_call(
				ctx, runtime, AuditEventType.TOOL_CALL_FAILED, plugin_manager,
				code="tool.contract_error",
				message=str(e),
				category=ErrorCategory.CONTRACT,
			)
		if result.output:
			result.output = await plugin_manager.apply_post_tool_call(
				ctx, runtime.name, runtime.arguments, result.output, result.duration_ms)
		if result.success:
			await _audit_runtime(ctx, runtime, AuditEventType.TOOL_CALL_COMPLETED, duration_ms=result.duration_ms)
			return result
		return await _fail_tool_call(
			ctx, runtime, AuditEventType.TOOL_CALL_FAILED, plugin_manager,
			code="tool.execution_failed",
			message=result.error,
			category=ErrorCategory.TOOL,
			duration_ms=result.duration_ms,
		)
	finally:
		_pop_current_tool_runtime(ctx, tool_context_key)


def _step_timeout(ctx: ExecutionContext) -> float:
	timeout = getattr(ctx.config, "step_timeout", 0)
	return float(timeout) if timeout and timeout > 0 else 0.0


def _tool_runtime(tc: dict, ctx: ExecutionContext, registry: ToolRegistry) -> ToolCallRuntime:
	name = tc.get("name", "")
	return ToolCallRuntime(
		name=name,
		arguments=tc.get("arguments", {}),
		tool_call_id=tc.get("id", ""),
		actor=ctx.state.metadata.get("agent_name", ""),
		session_id=ctx.state.metadata.get("session_id", ""),
		tool_def=registry.get(name),
	)


def _push_current_tool_runtime(ctx: ExecutionContext, runtime: ToolCallRuntime) -> int:
	"""Store per-task tool metadata so observability plugins can correlate hooks safely."""
	task = asyncio.current_task()
	key = id(task) if task else id(runtime)
	contexts = ctx.runtime.plugin_states.setdefault("_tool_runtime_contexts", {})
	contexts[key] = {
		"tool_name": runtime.name,
		"tool_call_id": runtime.tool_call_id,
		"actor": runtime.actor,
		"session_id": runtime.session_id,
		"capability": getattr(runtime.tool_def, "capability", "") if runtime.tool_def else "",
		"risk_level": getattr(runtime.tool_def, "risk_level", "") if runtime.tool_def else "",
		"is_read_only": bool(getattr(runtime.tool_def, "is_read_only", False)) if runtime.tool_def else False,
	}
	return key


def _pop_current_tool_runtime(ctx: ExecutionContext, key: int) -> None:
	contexts = ctx.runtime.plugin_states.get("_tool_runtime_contexts")
	if isinstance(contexts, dict):
		contexts.pop(key, None)


def _tool_context(ctx: ExecutionContext) -> ToolContext:
	return ToolContext(
		workspace=ctx.config.workspace if hasattr(ctx.config, "workspace") else "",
		exec_ctx=ctx,
		session_id=ctx.state.metadata.get("session_id", ""),
		agent_name=ctx.state.metadata.get("agent_name", ""),
		request_queue=ctx.runtime.approval_queue,
		response_queue=ctx.runtime.response_queue,
	)


def _evaluate_tool_policy(
	ctx: ExecutionContext,
	runtime: ToolCallRuntime,
) -> PolicyDecision:
	evaluator = ctx.services.policy_evaluator or CapabilityPolicyEvaluator(ctx.config.allowed_capabilities)
	return evaluator.evaluate(PolicyRequest(
		agent_name=runtime.actor,
		session_id=runtime.session_id,
		tool_name=runtime.name,
		capability=runtime.tool_def.capability,
		risk_level=runtime.tool_def.risk_level,
		workspace=ctx.config.workspace,
		arguments=runtime.arguments,
	))


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
	await _audit_runtime(
		ctx, runtime, audit_type,
		allowed=False, duration_ms=duration_ms, error=error,
	)
	if plugin_manager.plugins:
		tool_context_key = _push_current_tool_runtime(ctx, runtime)
		try:
			await plugin_manager.on_tool_call_failed(
				ctx,
				runtime.name,
				runtime.arguments,
				error.to_dict(),
				duration_ms,
			)
		finally:
			_pop_current_tool_runtime(ctx, tool_context_key)
	return ToolResult(
		tool_call_id=runtime.tool_call_id,
		tool_name=runtime.name,
		arguments=runtime.arguments,
		error=error.message,
		success=False,
	)


async def _audit_runtime(
	ctx: ExecutionContext,
	runtime: ToolCallRuntime,
	event_type: AuditEventType,
	allowed: bool = True,
	duration_ms: int = 0,
	error: ErrorEnvelope | None = None,
	metadata: dict | None = None,
) -> None:
	await _audit_tool_event(
		ctx, event_type, runtime.name, runtime.tool_call_id,
		actor=runtime.actor,
		session_id=runtime.session_id,
		capability=getattr(runtime.tool_def, "capability", ""),
		risk_level=getattr(runtime.tool_def, "risk_level", ""),
		allowed=allowed,
		duration_ms=duration_ms,
		error=error,
		metadata=metadata,
	)


async def _audit_tool_event(
	ctx: ExecutionContext,
	event_type: AuditEventType,
	tool_name: str,
	tool_call_id: str,
	actor: str = "",
	session_id: str = "",
	capability: str = "",
	risk_level: str = "",
	allowed: bool = True,
	duration_ms: int = 0,
	error: ErrorEnvelope | None = None,
	metadata: dict | None = None,
) -> None:
	sink = ctx.services.audit_sink
	if not sink:
		return
	try:
		await sink.record(AuditEvent(
			type=event_type,
			actor=actor,
			session_id=session_id,
			tool_name=tool_name,
			tool_call_id=tool_call_id,
			capability=capability,
			risk_level=risk_level,
			allowed=allowed,
			duration_ms=duration_ms,
			error=error.to_dict() if error else {},
			metadata=metadata or {},
		))
	except Exception as e:
		logger.warning(f"Audit sink record error: {e}")
