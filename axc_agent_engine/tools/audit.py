"""Tool audit event recording.
中文：此文档说明相关引擎组件的行为。"""
from __future__ import annotations

import logging

from axc_agent_engine.core.context import ExecutionContext
from axc_agent_engine.core.errors import ErrorEnvelope
from axc_agent_engine.observability.audit import AuditEvent, AuditEventType
from axc_agent_engine.tools.runtime import ToolCallRuntime

logger = logging.getLogger(__name__)


async def audit_runtime(
	ctx: ExecutionContext,
	runtime: ToolCallRuntime,
	event_type: AuditEventType,
	allowed: bool = True,
	duration_ms: int = 0,
	error: ErrorEnvelope | None = None,
	metadata: dict | None = None,
) -> None:
	await audit_tool_event(
		ctx,
		event_type,
		runtime.name,
		runtime.tool_call_id,
		actor=runtime.actor,
		session_id=runtime.session_id,
		capability=getattr(runtime.tool_def, "capability", ""),
		risk_level=getattr(runtime.tool_def, "risk_level", ""),
		allowed=allowed,
		duration_ms=duration_ms,
		error=error,
		metadata=metadata,
	)


async def audit_tool_event(
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
