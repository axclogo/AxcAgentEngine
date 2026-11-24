"""HumanInTheLoop 插件 — 通过 asyncio.Future 审批危险工具调用。"""

import asyncio
import logging
import uuid
from typing import TYPE_CHECKING

from axc_agent_engine.core.schema import ToolDefinition
from axc_agent_engine.plugins.base import BasePlugin, PreToolCallDecision
from axc_agent_engine.plugins.builtin.config_schemas import HUMAN_IN_THE_LOOP_CONFIG_SCHEMA
from axc_agent_engine.plugins.builtin.risk_guard.plugin import classify_risk
from axc_agent_engine.runtime.tasks import cancel_and_wait
from axc_agent_engine.tools.tool_output import ToolOutput

if TYPE_CHECKING:
	from axc_agent_engine.core.context import ExecutionContext
	from axc_agent_engine.plugins.context import PluginContext

logger = logging.getLogger(__name__)


class HumanInTheLoopPlugin(BasePlugin):
	"""Pause high-risk tool calls for external approval. 拦截危险工具调用并等待外部审批。"""

	name = "human_in_the_loop"
	display_name = "人工审批"
	priority = 8
	version = "1.0.0"
	config_schema = HUMAN_IN_THE_LOOP_CONFIG_SCHEMA

	def initialize(self, config: dict, plugin_ctx: "PluginContext") -> None:
		self._risk_threshold = config.get("risk_threshold", "dangerous")
		self._timeout = config.get("timeout", 300)
		self._auto_approve_tools = set(config.get("auto_approve", []))
		self._enable_ask_human = config.get("ask_human", True)
		self._pending_approvals: dict[str, asyncio.Future] = {}

	def get_tools(self) -> list[ToolDefinition]:
		"""Expose human interaction tools owned by this plugin. 提供此插件管理的人工交互工具。"""
		if not self._enable_ask_human:
			return []
		return [
			ToolDefinition(
				name="ask_human",
				description="向用户提问并等待回复",
				parameters={
					"type": "object",
					"properties": {
						"question": {"type": "string", "description": "要询问的问题"},
						"options": {"type": "array", "items": {"type": "string"}, "description": "可选项"},
					},
					"required": ["question"],
				},
				execute=self._ask_human,
				capability="human_approval",
			)
		]

	async def pre_tool_call(
		self, exec_ctx: "ExecutionContext", tool_name: str, arguments: dict
	) -> tuple[bool, dict] | PreToolCallDecision:
		"""Check risk and await approval when required. 检查风险等级，必要时等待审批。"""
		if tool_name in self._auto_approve_tools:
			return True, arguments
		risk = classify_risk(tool_name, arguments)
		if _risk_level(risk) < _risk_level(self._risk_threshold):
			return True, arguments
		approval_queue = exec_ctx.runtime.approval_queue
		# Deny when no approval queue is configured. 中文：需要审批但没有 approval_queue 时直接拒绝
		if not approval_queue:
			logger.warning(f"[hitl] Tool {tool_name} risk={risk}, no approval queue, rejecting")
			return PreToolCallDecision(
				False,
				arguments,
				self.name,
				f"工具风险等级为 {risk}，但当前运行环境没有配置人工审批队列",
				"tool.rejected_by_human_in_the_loop",
			)
		# Await the approval future. 中文：通过 Future 等待审批结果
		request_id = uuid.uuid4().hex
		loop = asyncio.get_running_loop()
		future: asyncio.Future = loop.create_future()
		self._pending_approvals[request_id] = future
		approval_request = {
			"type": "request",
			"request_id": request_id,
			"tool_name": tool_name,
			"arguments": arguments,
			"risk_level": risk,
		}
		logger.info(f"[hitl] Awaiting approval: {tool_name} (risk={risk})")
		listener = asyncio.create_task(self._listen_approval(approval_queue, request_id, future))
		try:
			await approval_queue.put(approval_request)
			response = await asyncio.wait_for(future, timeout=self._timeout)
			if type(response.get("approved")) is not bool:
				raise TypeError("approval response requires a boolean approved flag")
			if response["approved"]:
				logger.info(f"[hitl] Approved: {tool_name}")
				return True, arguments
			else:
				logger.info(f"[hitl] Rejected: {tool_name}")
				reason = response.get("reason") or "人工审批拒绝"
				return PreToolCallDecision(False, arguments, self.name, reason, "tool.rejected_by_human_in_the_loop")
		except TimeoutError:
			logger.warning(f"[hitl] Approval timeout: {tool_name}")
			return PreToolCallDecision(
				False, arguments, self.name, f"等待人工审批超时({self._timeout}s)", "tool.rejected_by_human_in_the_loop"
			)
		finally:
			del self._pending_approvals[request_id]
			await cancel_and_wait(listener)

	async def _listen_approval(self, queue: asyncio.Queue, request_id: str, future: asyncio.Future) -> None:
		"""Match approval replies to waiting requests.
		监听审批队列并完成匹配的 Future。"""
		while not future.done():
			item = await queue.get()
			if item.get("type") == "response" and item.get("request_id") == request_id:
				if not future.done():
					future.set_result(item)
				return
			await queue.put(item)
			await asyncio.sleep(0.01)

	def resolve_approval(self, request_id: str, approved: bool) -> None:
		"""Resolve an approval from the host API.
		通过程序接口完成审批（外部 API）。"""
		future = self._pending_approvals.get(request_id)
		if future and not future.done():
			future.set_result({"approved": approved, "request_id": request_id})

	async def _ask_human(self, args: dict, context: dict) -> ToolOutput:
		question = args.get("question", "")
		options = args.get("options", [])
		if not question:
			return ToolOutput.error("question cannot be empty")
		request_queue = context.get("request_queue")
		response_queue = context.get("response_queue")
		if not request_queue or not response_queue:
			return ToolOutput.error("Human interaction not supported in current environment")
		request = {"type": "ask_human", "question": question, "options": options}
		await request_queue.put(request)
		try:
			response = await asyncio.wait_for(response_queue.get(), timeout=self._timeout)
			return ToolOutput.json_output({"answer": response}, summary=f"用户回复：{response}")
		except TimeoutError:
			return ToolOutput.error(f"Waiting for user reply timeout ({self._timeout}s)")


def _risk_level(risk_name: str) -> int:
	levels = {"safe": 0, "moderate": 1, "dangerous": 2, "blocked": 3}
	return levels.get(risk_name, 0)
