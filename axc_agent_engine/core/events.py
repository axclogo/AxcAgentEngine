"""Event 类型定义。"""
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from axc_agent_engine.core.errors import ErrorEnvelope


class EventType(str, Enum):
	STREAM_START = "stream_start"
	STREAM_DELTA = "stream_delta"
	STREAM_END = "stream_end"
	THINKING_START = "thinking_start"
	THINKING_DELTA = "thinking_delta"
	THINKING_END = "thinking_end"
	TOOL_CALL = "tool_call"
	TOOL_RESULT = "tool_result"
	TOOL_ARGS_PREVIEW = "tool_args_preview"
	CACHE_HIT = "cache_hit"
	COST_UPDATE = "cost_update"
	STATE_CHANGE = "state_change"
	PLAN_CREATED = "plan_created"
	STEP_START = "step_start"
	STEP_COMPLETED = "step_completed"
	SUB_AGENT_START = "sub_agent_start"
	SUB_AGENT_STEP = "sub_agent_step"
	SUB_AGENT_COMPLETE = "sub_agent_complete"
	ERROR = "error"
	DONE = "done"


# Event 分类集合，方便过滤
STREAM_EVENTS = frozenset({EventType.STREAM_START, EventType.STREAM_DELTA, EventType.STREAM_END})
THINKING_EVENTS = frozenset({EventType.THINKING_START, EventType.THINKING_DELTA, EventType.THINKING_END})
TOOL_EVENTS = frozenset({EventType.TOOL_CALL, EventType.TOOL_RESULT, EventType.TOOL_ARGS_PREVIEW})
PLAN_EVENTS = frozenset({EventType.PLAN_CREATED, EventType.STEP_START, EventType.STEP_COMPLETED})
SYSTEM_EVENTS = frozenset({EventType.CACHE_HIT, EventType.COST_UPDATE, EventType.STATE_CHANGE})
SUB_AGENT_EVENTS = frozenset({EventType.SUB_AGENT_START, EventType.SUB_AGENT_STEP, EventType.SUB_AGENT_COMPLETE})
TERMINAL_EVENTS = frozenset({EventType.DONE, EventType.ERROR})


@dataclass
class Event:
	"""English: This documentation describes the related engine component behavior.
中文：执行事件，并提供常见模式的工厂方法。"""
	type: EventType
	content: str = ""
	tool_name: str = ""
	tool_call_id: str = ""
	arguments: dict[str, Any] = field(default_factory=dict)
	step_id: int = 0
	steps: list[dict[str, Any]] = field(default_factory=list)
	metadata: dict[str, Any] = field(default_factory=dict)

	#English: Source note. 中文：── 工厂方法 ──

	@classmethod
	def tool_call(cls, name: str, call_id: str, arguments: dict[str, Any]) -> "Event":
		"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
创建 TOOL_CALL 事件。"""
		return cls(type=EventType.TOOL_CALL, tool_name=name, tool_call_id=call_id, arguments=arguments)

	@classmethod
	def tool_args_preview(
		cls,
		name: str,
		call_id: str,
		arguments_delta: str = "",
		arguments_preview: str = "",
		index: int = 0,
	) -> "Event":
		"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
创建 TOOL_ARGS_PREVIEW 事件。"""
		return cls(
			type=EventType.TOOL_ARGS_PREVIEW,
			tool_name=name,
			tool_call_id=call_id,
			content=arguments_delta,
			metadata={"arguments_preview": arguments_preview, "index": index},
		)

	@classmethod
	def tool_result(
		cls,
		name: str,
		call_id: str,
		content: str,
		artifact_refs: list[dict[str, Any]] | None = None,
		metadata: dict[str, Any] | None = None,
	) -> "Event":
		"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
创建 TOOL_RESULT 事件。"""
		event_metadata = {"artifacts": artifact_refs or []}
		if metadata:
			event_metadata.update(metadata)
		return cls(type=EventType.TOOL_RESULT, tool_name=name, tool_call_id=call_id, content=content, metadata=event_metadata)

	@classmethod
	def error(cls, message: str | ErrorEnvelope, metadata: dict[str, Any] | None = None) -> "Event":
		"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
创建 ERROR 事件。"""
		if isinstance(message, ErrorEnvelope):
			error_metadata = {"error": message.to_dict()}
			if metadata:
				error_metadata.update(metadata)
			return cls(type=EventType.ERROR, content=message.message, metadata=error_metadata)
		return cls(type=EventType.ERROR, content=message, metadata=metadata or {})

	@classmethod
	def done(cls, content: str) -> "Event":
		"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
创建 DONE 事件。"""
		return cls(type=EventType.DONE, content=content)

	@classmethod
	def step_start(cls, step_id: int, description: str) -> "Event":
		"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
创建 STEP_START 事件。"""
		return cls(type=EventType.STEP_START, step_id=step_id, content=description)

	@classmethod
	def step_completed(cls, step_id: int, content: str) -> "Event":
		"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
创建 STEP_COMPLETED 事件。"""
		return cls(type=EventType.STEP_COMPLETED, step_id=step_id, content=content)

	@classmethod
	def delta(cls, content: str) -> "Event":
		"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
创建 STREAM_DELTA 事件。"""
		return cls(type=EventType.STREAM_DELTA, content=content)

	@classmethod
	def plan_created(cls, goal: str, steps: list[dict[str, Any]]) -> "Event":
		"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
创建 PLAN_CREATED 事件。"""
		return cls(type=EventType.PLAN_CREATED, content=goal, steps=steps)

	@classmethod
	def state_change(cls, content: str, metadata: dict[str, Any] | None = None) -> "Event":
		"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
创建 STATE_CHANGE 事件。"""
		return cls(type=EventType.STATE_CHANGE, content=content, metadata=metadata or {})

	@classmethod
	def cost_update(cls, input_tokens: int, output_tokens: int) -> "Event":
		"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
创建 COST_UPDATE 事件。"""
		return cls(type=EventType.COST_UPDATE, metadata={"input_tokens": input_tokens, "output_tokens": output_tokens})

	@classmethod
	def sub_agent_start(cls, agent_name: str, message: str, metadata: dict[str, Any]) -> "Event":
		"""Create SUB_AGENT_START.
中文：创建子 Agent 开始事件。"""
		return cls(type=EventType.SUB_AGENT_START, content=message, metadata={"agent_name": agent_name, **metadata})

	@classmethod
	def sub_agent_step(cls, agent_name: str, step: dict[str, Any], metadata: dict[str, Any]) -> "Event":
		"""Create SUB_AGENT_STEP.
中文：创建子 Agent 明细事件。"""
		return cls(type=EventType.SUB_AGENT_STEP, metadata={"agent_name": agent_name, "step": step, **metadata})

	@classmethod
	def sub_agent_complete(
		cls,
		agent_name: str,
		success: bool,
		duration_ms: int,
		error: str,
		result: str,
		metadata: dict[str, Any],
	) -> "Event":
		"""Create SUB_AGENT_COMPLETE.
中文：创建子 Agent 完成事件。"""
		return cls(
			type=EventType.SUB_AGENT_COMPLETE,
			content=result,
			metadata={
				"agent_name": agent_name,
				"success": success,
				"duration_ms": duration_ms,
				"error": error,
				**metadata,
			},
		)
