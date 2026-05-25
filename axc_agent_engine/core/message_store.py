"""MessageStore — 消息列表的唯一持有者。

所有权：由 Executor 创建，在计划执行期间与 PORRunner 共享。
线程安全：不保证线程安全。所有访问必须来自同一个 async task，或由外部串行化。
PORRunner 有意共享同一个实例，以便步骤上下文可累积给后续步骤使用。
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from axc_agent_engine.core.constants import PLUGIN_CONTEXT_TAG


class MessageStore:
	"""封装消息列表的所有读写操作。"""

	def __init__(self) -> None:
		self._messages: list[dict[str, Any]] = []

	@property
	def count(self) -> int:
		return len(self._messages)

	def append(self, message: dict[str, Any]) -> None:
		"""追加一条消息"""
		self._messages.append(deepcopy(message))

	def extend(self, messages: list[dict[str, Any]]) -> None:
		"""批量追加消息"""
		self._messages.extend(deepcopy(messages))

	def insert(self, index: int, message: dict[str, Any]) -> None:
		"""在指定位置插入消息"""
		self._messages.insert(index, deepcopy(message))

	def get_all(self) -> list[dict[str, Any]]:
		"""获取所有消息的副本"""
		return deepcopy(self._messages)

	def get_recent(self, n: int) -> list[dict[str, Any]]:
		"""获取最近 n 条消息"""
		return deepcopy(self._messages[-n:]) if n > 0 else []

	def set_all(self, messages: list[dict[str, Any]]) -> None:
		"""替换全部消息"""
		self._messages = deepcopy(messages)

	def clear(self) -> None:
		"""清空所有消息"""
		self._messages.clear()

	def get_first(self) -> dict[str, Any] | None:
		"""获取第一条消息"""
		return deepcopy(self._messages[0]) if self._messages else None

	def set_at(self, index: int, message: dict[str, Any]) -> None:
		"""替换指定位置的消息"""
		if 0 <= index < len(self._messages):
			self._messages[index] = deepcopy(message)

	def append_tool_results(self, results: list) -> None:
		"""把工具执行结果追加到消息列表。

		成功结果使用 ToolOutput.compact_view()，确保大内容不会撑爆上下文。
		"""
		for r in results:
			if r.success and r.output:
				content = r.output.compact_view()
			else:
				content = f"[Error] {r.error}"
			self._messages.append({
				"role": "tool",
				"tool_call_id": r.tool_call_id,
				"content": content,
			})

	def snapshot(self) -> int:
		"""返回当前消息数量作为 snapshot 点。"""
		return len(self._messages)

	def rollback(self, snapshot: int) -> None:
		"""回滚到指定 snapshot 点。"""
		self._messages = self._messages[:snapshot]

	def init_system_prompt(self, system_prompt: str) -> None:
		"""初始化或更新 system prompt（第一条消息）"""
		if not system_prompt:
			return
		if not self._messages or self._messages[0].get("role") != "system":
			self._messages.insert(0, {"role": "system", "content": system_prompt})
		elif self._messages[0].get("content") != system_prompt:
			self._messages[0] = {"role": "system", "content": system_prompt}

	def upsert_plugin_context(self, context: str) -> None:
		"""插入或更新插件上下文（第二条 system 消息）。"""
		if not context or not self._messages or self._messages[0].get("role") != "system":
			return
		tagged = f"{PLUGIN_CONTEXT_TAG}\n{context}"
		if len(self._messages) > 1 and self._messages[1].get("role") == "system" and \
			self._messages[1].get("content", "").startswith(PLUGIN_CONTEXT_TAG):
			self._messages[1] = {"role": "system", "content": tagged}
		else:
			self._messages.insert(1, {"role": "system", "content": tagged})
