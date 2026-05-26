"""Structured audit events for security-sensitive engine operations.
中文：此文档说明相关引擎组件的行为。"""
from __future__ import annotations

import asyncio
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable


class AuditEventType(StrEnum):
	"""Audit event categories.
中文：此文档说明相关引擎组件的行为。"""
	TOOL_CALL_STARTED = "tool_call_started"
	TOOL_CALL_COMPLETED = "tool_call_completed"
	TOOL_CALL_REJECTED = "tool_call_rejected"
	TOOL_CALL_FAILED = "tool_call_failed"


@dataclass(frozen=True)
class AuditEvent:
	"""Machine-readable audit event.
中文：此文档说明相关引擎组件的行为。"""
	event_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
	type: str = ""
	timestamp: float = field(default_factory=time.time)
	actor: str = ""
	session_id: str = ""
	tool_name: str = ""
	tool_call_id: str = ""
	capability: str = ""
	risk_level: str = ""
	allowed: bool = True
	duration_ms: int = 0
	error: dict[str, Any] = field(default_factory=dict)
	metadata: dict[str, Any] = field(default_factory=dict)

	def to_dict(self) -> dict[str, Any]:
		return {
			"event_id": self.event_id,
			"type": self.type,
			"timestamp": self.timestamp,
			"actor": self.actor,
			"session_id": self.session_id,
			"tool_name": self.tool_name,
			"tool_call_id": self.tool_call_id,
			"capability": self.capability,
			"risk_level": self.risk_level,
			"allowed": self.allowed,
			"duration_ms": self.duration_ms,
			"error": self.error,
			"metadata": self.metadata,
		}


@runtime_checkable
class AuditSink(Protocol):
	"""Stores audit events.
中文：此文档说明相关引擎组件的行为。"""
	async def record(self, event: AuditEvent) -> None: ...


class InMemoryAuditSink:
	"""Bounded in-memory audit sink for tests and local development.
中文：此文档说明相关引擎组件的行为。"""

	def __init__(self, max_events: int = 10000) -> None:
		self._events: deque[AuditEvent] = deque(maxlen=max_events)
		self._lock = asyncio.Lock()

	async def record(self, event: AuditEvent) -> None:
		async with self._lock:
			self._events.append(event)

	async def list_events(self, event_type: str = "") -> list[AuditEvent]:
		async with self._lock:
			if not event_type:
				return list(self._events)
			return [event for event in self._events if event.type == event_type]

	def count(self) -> int:
		return len(self._events)
