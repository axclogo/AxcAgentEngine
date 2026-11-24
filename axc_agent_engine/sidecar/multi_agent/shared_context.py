"""SharedContext — 多 Agent 共享上下文。
SharedContext — shared context for multi-agent sessions.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class SharedContext:
	"""Shared context for multi-agent sessions.
	多 Agent 共享上下文。"""

	topic: str = ""
	messages: list[dict] = field(default_factory=list)
	artifacts: dict[str, Any] = field(default_factory=dict)
	metadata: dict[str, Any] = field(default_factory=dict)

	def add_message(self, agent_name: str, content: str, round_num: int) -> None:
		"""Append one message.
		追加一条消息。"""
		self.messages.append(
			{
				"agent": agent_name,
				"content": content,
				"round": round_num,
				"timestamp": time.time(),
			}
		)

	def add_system_message(self, content: str, round_num: int) -> None:
		"""Append a system message, such as LLM guidance.
		追加系统消息（LLM 引导建议等）。"""
		self.messages.append(
			{
				"agent": "__system__",
				"content": content,
				"round": round_num,
				"timestamp": time.time(),
			}
		)

	def get_history(self, exclude_agent: str = "", limit: int = 0) -> list[dict]:
		"""Get message history, optionally excluding one Agent and limiting count.
		获取消息历史（可排除某个 Agent 的消息，可限制数量）。"""
		msgs = self.messages
		if exclude_agent:
			msgs = [m for m in msgs if m["agent"] != exclude_agent]
		if limit > 0:
			msgs = msgs[-limit:]
		return msgs

	def get_last_message(self, exclude_agent: str = "") -> dict | None:
		"""Get the last message not authored by the excluded Agent.
		获取最后一条非自己的消息。"""
		for msg in reversed(self.messages):
			if exclude_agent and msg["agent"] == exclude_agent:
				continue
			return msg
		return None

	def get_recent_contents(self, limit: int = 5) -> str:
		"""Get recent message text joined together for LLM judgment.
		获取最近几条消息的文本拼接（用于 LLM 判断）。"""
		recent = self.messages[-limit:] if limit > 0 else self.messages
		return "\n".join(f"[{m['agent']}] {m['content']}" for m in recent)
