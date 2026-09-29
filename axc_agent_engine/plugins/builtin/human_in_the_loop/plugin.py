"""HumanInTheLoop 插件 — 通过 asyncio.Future 审批危险工具调用。"""
import asyncio
import logging
import time
import uuid
from typing import TYPE_CHECKING, Any

from axc_agent_engine.plugins.base import BasePlugin
from axc_agent_engine.plugins.builtin.config_schemas import HUMAN_IN_THE_LOOP_CONFIG_SCHEMA
from axc_agent_engine.plugins.builtin.risk_guard.plugin import classify_risk
from axc_agent_engine.core.schema import ToolDefinition
from axc_agent_engine.tools.tool_output import ToolOutput

if TYPE_CHECKING:
	from axc_agent_engine.core.context import ExecutionContext
	from axc_agent_engine.plugins.context import PluginContext

logger = logging.getLogger(__name__)

_ASK_HUMAN_TOOL_TIMEOUT_GRACE = 1.0


class HumanInTheLoopPlugin(BasePlugin):
	"""English: This documentation describes the related engine component behavior.
中文：拦截危险工具调用并等待外部审批。"""
	name = "human_in_the_loop"
	display_name = "人工审批"
	priority = 8
	version = "1.0.0"
	config_schema = HUMAN_IN_THE_LOOP_CONFIG_SCHEMA

	def initialize(self, config: dict, plugin_ctx: "PluginContext") -> None:
		self._risk_threshold = config.get("risk_threshold", "dangerous")
		self._approval_timeout = _positive_timeout(config.get("approval_timeout", 300), "approval_timeout")
		self._ask_human_timeout = _positive_timeout(config.get("ask_human_timeout", 60), "ask_human_timeout")
		self._auto_approve_tools = set(config.get("auto_approve", []))
		self._enable_ask_human = bool(config.get("ask_human", True))
		self._pending_approvals: dict[str, asyncio.Future] = {}

	def get_tools(self) -> list[ToolDefinition]:
		"""Expose human interaction tools owned by this plugin.
中文：此文档说明相关引擎组件的行为。"""
		if not self._enable_ask_human:
			return []
		return [ToolDefinition(
			name="ask_human",
			description=(
				"向用户提问并等待回复。等待超时后会返回 skipped；此时跳过依赖该回复的事项，"
				"继续处理其他工作，并在最终汇报中列出待人工确认项。"
			),
			parameters={
				"type": "object",
				"properties": {
					"question": {"type": "string", "description": "要询问的问题"},
					"options": {"type": "array", "items": {"type": "string"}, "description": "可选项"},
				},
				"required": ["question"],
			},
			execute=self._ask_human,
			timeout=self._ask_human_timeout + _ASK_HUMAN_TOOL_TIMEOUT_GRACE,
			capability="human_approval",
		)]

	async def pre_tool_call(self, exec_ctx: "ExecutionContext", tool_name: str,
							arguments: dict) -> tuple[bool, dict]:
		"""English: This documentation describes the related engine component behavior.
中文：检查风险等级，必要时等待审批。"""
		if tool_name in self._auto_approve_tools:
			return True, arguments
		risk = classify_risk(tool_name, arguments)
		if _risk_level(risk) < _risk_level(self._risk_threshold):
			return True, arguments
		approval_queue = exec_ctx.runtime.approval_queue
		#English: Bilingual note. 中文：需要审批但没有 approval_queue 时直接拒绝
		if not approval_queue:
			logger.warning(f"[hitl] Tool {tool_name} risk={risk}, no approval queue, rejecting")
			self._last_rejection_reason = f"工具风险等级为 {risk}，但当前运行环境没有配置人工审批队列"
			self._last_rejection_code = "tool.rejected_by_human_in_the_loop"
			return False, arguments
		#English: Bilingual note. 中文：通过 Future 等待审批结果
		request_id = f"{tool_name}_{id(arguments)}"
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
		try:
			await approval_queue.put(approval_request)
			asyncio.ensure_future(self._listen_approval(exec_ctx, request_id, initial_delay=0.01))
			response = await asyncio.wait_for(future, timeout=self._approval_timeout)
			if response.get("approved"):
				logger.info(f"[hitl] Approved: {tool_name}")
				return True, arguments
			else:
				logger.info(f"[hitl] Rejected: {tool_name}")
				reason = response.get("reason") or "人工审批拒绝"
				self._last_rejection_reason = reason
				self._last_rejection_code = "tool.rejected_by_human_in_the_loop"
				return False, arguments
		except asyncio.TimeoutError:
			logger.warning(f"[hitl] Approval timeout: {tool_name}")
			self._last_rejection_reason = f"等待人工审批超时({_seconds_label(self._approval_timeout)}s)"
			self._last_rejection_code = "tool.rejected_by_human_in_the_loop"
			return False, arguments
		finally:
			self._pending_approvals.pop(request_id, None)

	async def _listen_approval(self, exec_ctx: "ExecutionContext", request_id: str, initial_delay: float = 0.0) -> None:
		"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
监听审批队列并完成匹配的 Future。"""
		queue = exec_ctx.runtime.approval_queue
		if not queue:
			return
		if initial_delay > 0:
			await asyncio.sleep(initial_delay)
		while request_id in self._pending_approvals:
			try:
				item = await asyncio.wait_for(queue.get(), timeout=1.0)
			except asyncio.TimeoutError:
				continue
			if item.get("type") == "response" and item.get("request_id") == request_id:
				future = self._pending_approvals.get(request_id)
				if future and not future.done():
					future.set_result(item)
				return
			await queue.put(item)
			await asyncio.sleep(0.01)

	def resolve_approval(self, request_id: str, approved: bool) -> None:
		"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
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
		request_id = uuid.uuid4().hex
		request = {
			"type": "ask_human",
			"request_id": request_id,
			"tool_call_id": context.get("tool_call_id", ""),
			"question": question,
			"options": options,
			"timeout_seconds": self._ask_human_timeout,
			"expires_at": time.time() + self._ask_human_timeout,
		}
		await request_queue.put(request)
		try:
			answer = await self._wait_for_human_response(response_queue, request_id)
			return ToolOutput.json_output(
				{"status": "answered", "request_id": request_id, "answer": answer},
				summary=f"用户回复：{answer}",
			)
		except asyncio.TimeoutError:
			return self._human_timeout_output(request_id, question)

	async def _wait_for_human_response(self, response_queue, request_id: str) -> Any:
		loop = asyncio.get_running_loop()
		deadline = loop.time() + self._ask_human_timeout
		while True:
			remaining = deadline - loop.time()
			if remaining <= 0:
				raise asyncio.TimeoutError
			response = await asyncio.wait_for(response_queue.get(), timeout=remaining)
			if not isinstance(response, dict) or response.get("type") != "ask_human_response":
				raise ValueError("ask_human response must be an ask_human_response object")
			if response.get("request_id") != request_id:
				logger.warning("[hitl] Discarding unmatched ask_human response: %s", response.get("request_id"))
				continue
			if "answer" not in response:
				raise ValueError("ask_human response must contain answer")
			return response["answer"]

	def _human_timeout_output(self, request_id: str, question: str) -> ToolOutput:
		timeout_label = _seconds_label(self._ask_human_timeout)
		guidance = (
			f"人工应答等待已超时（{timeout_label} 秒），待确认问题：{question}\n"
			"将依赖该回答的事项视为已跳过；不要猜测答案或立即重复询问。继续完成不依赖该回答的工作，"
			"并在最终汇报中单独列出该问题以及因此未处理的内容。"
		)
		return ToolOutput(
			content={
				"status": "skipped",
				"reason": "human_unavailable",
				"request_id": request_id,
				"question": question,
				"waited_seconds": self._ask_human_timeout,
			},
			content_type="json",
			summary=f"人工应答超时，已跳过待确认事项：{question}",
			llm_view=guidance,
			metadata={
				"durable": True,
				"durable_summary": guidance,
				"status": "skipped",
				"reason": "human_unavailable",
				"request_id": request_id,
			},
		)


def _risk_level(risk_name: str) -> int:
	levels = {"safe": 0, "moderate": 1, "dangerous": 2, "blocked": 3}
	return levels.get(risk_name, 0)


def _positive_timeout(value: Any, name: str) -> float:
	if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
		raise ValueError(f"{name} must be a positive number")
	return float(value)


def _seconds_label(value: float) -> str:
	return f"{value:g}"
