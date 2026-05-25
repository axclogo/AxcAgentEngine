"""多 Agent 支撑层拥有的类型。
Types owned by the multi-agent plugin support layer.
"""
from __future__ import annotations

from enum import StrEnum


class SessionMode(StrEnum):
	"""多 Agent 会话模式。
	Multi-agent session mode.
	"""
	DISCUSSION = "discussion"
	GROUP_CHAT = "group_chat"
	SUPERVISOR = "supervisor"
	DEBATE = "debate"
	INTERVIEW = "interview"
	SIMULATION = "simulation"
	BACKCAST = "backcast"
	RETROSPECTIVE = "retrospective"
	REDBLUE = "redblue"
	SOCIAL = "social"
	SANDBOX = "sandbox"
	CUSTOM = "custom"


class MultiAgentEventType(StrEnum):
	"""多 Agent 事件类型。
	Multi-agent event type.
	"""
	MESSAGE = "message"
	ROUND_START = "round_start"
	ROUND_END = "round_end"
	DONE = "done"
	ERROR = "error"
	SYSTEM = "system"
	SPEAK_START = "speak_start"
	SPEAK_END = "speak_end"
	STREAM_DELTA = "stream_delta"
