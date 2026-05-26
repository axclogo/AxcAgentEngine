"""标准化工具执行上下文。"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
	from axc_agent_engine.core.context import ExecutionContext
	from axc_agent_engine.storage.protocols import ResultStore


@dataclass
class ToolContext:
	"""传给所有工具 execute 函数的类型化上下文。"""
	workspace: str = ""
	exec_ctx: "ExecutionContext | None" = None
	session_id: str = ""
	agent_name: str = ""
	tool_name: str = ""
	tool_call_id: str = ""
	request_queue: asyncio.Queue | None = None
	response_queue: asyncio.Queue | None = None

	@property
	def result_store(self) -> "ResultStore | None":
		"""从 ExecutionContext.services 获取 result_store。"""
		if self.exec_ctx and self.exec_ctx.services:
			return self.exec_ctx.services.result_store
		return None

	def to_dict(self) -> dict[str, Any]:
		"""转换成工具 execute 函数使用的 dict。"""
		return {
			"workspace": self.workspace,
			"exec_ctx": self.exec_ctx,
			"session_id": self.session_id,
			"agent_name": self.agent_name,
			"tool_name": self.tool_name,
			"tool_call_id": self.tool_call_id,
			"request_queue": self.request_queue,
			"response_queue": self.response_queue,
			"result_store": self.result_store,
			"command_executor": self.exec_ctx.services.command_executor if self.exec_ctx and self.exec_ctx.services else None,
		}
