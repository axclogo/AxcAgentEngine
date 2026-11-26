"""Shared run history for Executor and POR. 执行器与 POR 共用消息历史，调用方负责串行访问。"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from axc_agent_engine.core.constants import PLUGIN_CONTEXT_TAG


class MessageStore:
	"""Own the message history for a run. 封装消息列表的所有读写操作。"""

	def __init__(self) -> None:
		self._messages: list[dict[str, Any]] = []

	@property
	def count(self) -> int:
		return len(self._messages)

	def append(self, message: dict[str, Any]) -> None:
		"""Append a copy of one message. 追加一条消息"""
		self._messages.append(deepcopy(message))

	def extend(self, messages: list[dict[str, Any]]) -> None:
		"""Append copies of multiple messages. 批量追加消息"""
		self._messages.extend(deepcopy(messages))

	def insert(self, index: int, message: dict[str, Any]) -> None:
		"""Insert a copy at the given index. 在指定位置插入消息"""
		self._messages.insert(index, deepcopy(message))

	def get_all(self) -> list[dict[str, Any]]:
		"""Return a detached copy of the history. 获取所有消息的副本"""
		return deepcopy(self._messages)

	def get_recent(self, n: int) -> list[dict[str, Any]]:
		"""Copy the most recent messages. 获取最近 n 条消息"""
		return deepcopy(self._messages[-n:]) if n > 0 else []

	def set_all(self, messages: list[dict[str, Any]]) -> None:
		"""Replace the entire history with a copy. 替换全部消息"""
		self._messages = deepcopy(messages)

	def restore_history(self, messages: list[dict]) -> None:
		"""Require recorded results for outstanding tool requests. 恢复前核对工具请求与结果。"""
		self.validate_history(messages)
		self.set_all(messages)

	@staticmethod
	def validate_history(messages: list[dict]) -> None:
		if not isinstance(messages, list) or not all(isinstance(message, dict) for message in messages):
			raise ValueError("Invalid checkpoint message history")
		pending: set[str] = set()
		for message in messages:
			if message.get("role") == "tool":
				call_id = message.get("tool_call_id", "")
				if not isinstance(call_id, str):
					raise ValueError("Invalid checkpoint tool_call_id")
				pending.discard(call_id)
				continue
			if pending:
				break
			if message.get("role") == "assistant":
				calls = message.get("tool_calls")
				if calls is None:
					calls = []
				if not isinstance(calls, list) or not all(
					isinstance(call, dict) and isinstance(call.get("id"), str) and call["id"] for call in calls
				):
					raise ValueError("Invalid checkpoint assistant tool_calls")
				pending = {call["id"] for call in calls}
		if pending:
			raise ValueError(f"Missing tool results in checkpoint. Reconcile before resume: {sorted(pending)}")

	def clear(self) -> None:
		"""Clear the history. 清空所有消息"""
		self._messages.clear()

	def get_first(self) -> dict[str, Any] | None:
		"""Read a copy of the first message. 获取第一条消息"""
		return deepcopy(self._messages[0]) if self._messages else None

	def set_at(self, index: int, message: dict[str, Any]) -> None:
		"""Replace a message at the given index. 替换指定位置的消息"""
		if 0 <= index < len(self._messages):
			self._messages[index] = deepcopy(message)

	def append_tool_results(self, results: list) -> None:
		"""Append tool results to the conversation.
		把工具执行结果追加到消息列表。

		                成功结果使用 ToolOutput.context_view()，避免 UI 展示内容污染 LLM 上下文。"""
		for r in results:
			if r.success and r.output:
				content = r.output.context_view()
			else:
				content = f"[错误] {r.error}"
			self._messages.append(
				{
					"role": "tool",
					"tool_call_id": r.tool_call_id,
					"name": r.tool_name,
					"content": content,
					"metadata": dict(r.output.metadata) if r.output else {},
				}
			)

	def snapshot(self) -> int:
		"""Remember the current history length.
		返回当前消息数量作为 snapshot 点。"""
		return len(self._messages)

	def rollback(self, snapshot: int) -> None:
		"""Restore the history to its saved length.
		回滚到指定 snapshot 点。"""
		self._messages = self._messages[:snapshot]

	def init_system_prompt(self, system_prompt: str) -> None:
		"""Insert or update the first system message.
		初始化或更新 system prompt（第一条消息）"""
		if not system_prompt:
			return
		if not self._messages or self._messages[0].get("role") != "system":
			self._messages.insert(0, {"role": "system", "content": system_prompt})
		elif self._messages[0].get("content") != system_prompt:
			self._messages[0] = {"role": "system", "content": system_prompt}

	def upsert_plugin_context(self, context: str) -> None:
		"""Insert or update the plugin context after the system prompt.
		插入或更新插件上下文（第二条 system 消息）。"""
		index = 0
		for msg in self._messages:
			if msg.get("role") != "system":
				break
			content = msg.get("content")
			if isinstance(content, str) and content.startswith(PLUGIN_CONTEXT_TAG + "\n"):
				del self._messages[index]
				break
			index += 1
		if context:
			self._messages.insert(index, {"role": "system", "content": f"{PLUGIN_CONTEXT_TAG}\n{context}"})
