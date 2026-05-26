"""Graph audit event recording.
中文：此文档说明相关引擎组件的行为。"""
import time
from typing import TYPE_CHECKING, Any

from axc_agent_engine.core.errors import ErrorEnvelope

from .config import GraphConfig

if TYPE_CHECKING:
	from axc_agent_engine.core.context import ExecutionContext


class GraphAuditRecorder:
	def __init__(self, config: GraphConfig) -> None:
		self._config = config

	async def record(self, exec_ctx: "ExecutionContext | None", event_type: str, tool_name: str,
					 capability: str, risk_level: str, started: float, allowed: bool,
					 metadata: dict[str, Any], error: ErrorEnvelope | None = None) -> None:
		if not self._config.audit_enabled or not exec_ctx or not exec_ctx.services.audit_sink:
			return
		from axc_agent_engine.observability.audit import AuditEvent
		state_metadata = exec_ctx.state.metadata
		await exec_ctx.services.audit_sink.record(AuditEvent(
			type=event_type,
			actor=str(state_metadata.get("user_id") or state_metadata.get("agent_name") or ""),
			session_id=str(state_metadata.get("session_id") or ""),
			tool_name=tool_name,
			capability=capability,
			risk_level=risk_level,
			allowed=allowed,
			duration_ms=int((time.time() - started) * 1000),
			error=error.to_dict() if error else {},
			metadata={**metadata, "namespace": self._config.namespace},
		))
