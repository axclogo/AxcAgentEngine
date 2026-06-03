"""Collaboration 插件 — 通过 AgentMessageDispatcher + MessageBus 做 Agent 间调用。"""
import inspect
import logging
import time
from typing import Any, TYPE_CHECKING

from axc_agent_engine.core.constants import MAX_CALL_DEPTH
from axc_agent_engine.core.schema import ToolDefinition
from axc_agent_engine.plugins.base import BasePlugin
from axc_agent_engine.plugins.builtin.common import agent_event_callback
from axc_agent_engine.plugins.builtin.config_schemas import COLLABORATION_CONFIG_SCHEMA

if TYPE_CHECKING:
	from axc_agent_engine.plugins import PluginContext

logger = logging.getLogger(__name__)


class AgentCallPolicy:
	def __init__(self, allowed_agents: set[str], denied_agents: set[str], allow_self_call: bool) -> None:
		self.allowed_agents = allowed_agents
		self.denied_agents = denied_agents
		self.allow_self_call = allow_self_call

	def allowed(self, agent_name: str, caller: str = "") -> bool:
		if not agent_name:
			return False
		if not self.allow_self_call and caller and agent_name == caller:
			return False
		if agent_name in self.denied_agents:
			return False
		return not self.allowed_agents or agent_name in self.allowed_agents


class AgentEnvelopeFactory:
	def create(self, caller: str, agent_name: str, message: str, context: dict, metadata: dict):
		from axc_agent_engine.core.dispatcher import AgentEnvelope
		return AgentEnvelope(
			sender=caller,
			recipient=agent_name,
			content=message,
			conversation_id=str(context.get("session_id", "")),
			trace_id=str(metadata.get("trace_id", "")),
			metadata=metadata,
		)


class OrchestrationHandlers:
	def __init__(self, plugin: "CollaborationPlugin") -> None:
		self.plugin = plugin

	async def create(self, args: dict, context: dict):
		from axc_agent_engine.tools.tool_output import ToolOutput
		service = self.plugin._orchestration_service()
		if not service:
			return ToolOutput.error("Orchestration service not configured")
		agent_names = args.get("agent_names", [])
		topic = str(args.get("topic", ""))
		if not isinstance(agent_names, list) or not agent_names or not topic:
			return ToolOutput.error("agent_names 和 topic 不能为空")
		caller = str(context.get("agent_name", ""))
		disallowed = [str(name) for name in agent_names if not self.plugin._is_agent_allowed(str(name), caller)]
		if disallowed:
			return ToolOutput.error(f"Agents not allowed: {', '.join(disallowed)}")
		try:
			task = await service.create_task(
				agent_names=[str(name) for name in agent_names],
				mode=str(args.get("mode", "group_chat")),
				topic=topic,
				max_rounds=int(args.get("max_rounds", 10)),
				supervisor=str(args.get("supervisor", "")),
				persona=args.get("persona", {}) if isinstance(args.get("persona", {}), dict) else {},
			)
			return ToolOutput.json_output(
				{"task_id": task.task_id, "status": str(task.status), "mode": task.mode, "topic": task.topic},
				summary=f"推演任务已创建: {task.task_id}",
			)
		except Exception as e:
			return ToolOutput.error(f"推演任务创建失败: {e}")


class CollaborationPlugin(BasePlugin):
	name = "collaboration"
	display_name = "Agent 协作"
	priority = 40
	version = "3.0.0"
	config_schema = COLLABORATION_CONFIG_SCHEMA

	def initialize(self, config: dict, plugin_ctx: "PluginContext") -> None:
		self._enabled = config.get("enabled", True)
		self._max_depth = config.get("max_depth", MAX_CALL_DEPTH)
		self._timeout = float(config.get("timeout", 60.0))
		self._allowed_agents = set(str(name) for name in config.get("allowed_agents", []))
		self._denied_agents = set(str(name) for name in config.get("denied_agents", []))
		self._expose_agent_list = bool(config.get("expose_agent_list", True))
		self._allow_self_call = bool(config.get("allow_self_call", False))
		self._orchestration_resource = config.get("orchestration_resource", "orchestration")
		self._plugin_ctx = plugin_ctx
		self._agent_policy = AgentCallPolicy(self._allowed_agents, self._denied_agents, self._allow_self_call)
		self._envelope_factory = AgentEnvelopeFactory()
		self._orchestration_handlers = OrchestrationHandlers(self)

	def get_tools(self) -> list[ToolDefinition]:
		if not self._enabled:
			return []
		tools: list[ToolDefinition] = []
		if self._expose_agent_list:
			tools.append(
				ToolDefinition(
					name="agent_list",
					description="列出当前可调用的 Agent",
					parameters={"type": "object", "properties": {}},
					is_read_only=True,
					capability="agent_call",
					risk_level="safe",
					execute=self._tool_agent_list,
				),
			)
		tools.extend([
			ToolDefinition(
				name="agent_call",
				description="调用另一个 Agent 执行任务并返回结果",
				parameters={"type": "object", "properties": {
				 "agent_name": {"type": "string", "description": "目标 Agent 名称"},
				 "message": {"type": "string", "description": "发送给 Agent 的消息"},
				 "timeout": {"type": "number", "description": "调用超时时间（秒）"}},
				 "required": ["agent_name", "message"]},
				is_read_only=False,
				timeout=0,
				capability="agent_call",
				risk_level="moderate",
				execute=self._tool_agent_call,
			),
		])
		if self._orchestration_service():
			tools.extend([
				ToolDefinition(
					name="orchestration_task_create",
					description="创建旁路多 Agent 推演任务",
					parameters={"type": "object", "properties": {
				 "agent_names": {"type": "array", "items": {"type": "string"}, "description": "参与的 Agent 名称列表"},
				 "mode": {"type": "string", "description": "推演模式"},
				 "topic": {"type": "string", "description": "推演主题"},
				 "max_rounds": {"type": "integer", "description": "最大轮次", "default": 10},
				 "supervisor": {"type": "string", "description": "监督者模式下的管理者 Agent 名称"},
				 "persona": {"type": "object", "description": "按 agent_name 配置的 persona/team 元数据"}},
				 "required": ["agent_names", "mode", "topic"]},
					is_read_only=False,
					capability="agent_call",
					risk_level="moderate",
					execute=self._tool_orchestration_task_create,
				),
				ToolDefinition(
					name="orchestration_task_get",
					description="查询旁路多 Agent 推演任务状态",
					parameters={"type": "object", "properties": {
				 "task_id": {"type": "string", "description": "任务 ID"}},
				 "required": ["task_id"]},
					is_read_only=True,
					capability="agent_call",
					risk_level="safe",
					execute=self._tool_orchestration_task_get,
				),
				ToolDefinition(
					name="orchestration_task_cancel",
					description="取消旁路多 Agent 推演任务",
					parameters={"type": "object", "properties": {
				 "task_id": {"type": "string", "description": "任务 ID"}},
				 "required": ["task_id"]},
					is_read_only=False,
					capability="agent_call",
					risk_level="moderate",
					execute=self._tool_orchestration_task_cancel,
				),
			])
		return tools

	def _get_dispatcher(self):
		"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
从插件上下文获取共享 dispatcher。"""
		return self._plugin_ctx.dispatcher

	def _orchestration_service(self) -> Any:
		return self._plugin_ctx.resources.get(self._orchestration_resource)

	async def _tool_agent_list(self, args: dict, context: dict):
		from axc_agent_engine.tools.tool_output import ToolOutput
		agents = self._plugin_ctx.list_agents()
		caller = str(context.get("agent_name", ""))
		result = [
			{"name": a.name, "description": a.description}
			for a in agents
			if self._is_agent_allowed(a.name, caller)
		]
		return ToolOutput.json_output({"agents": result}, summary=f"{len(result)} agents available")

	async def _tool_agent_call(self, args: dict, context: dict):
		from axc_agent_engine.tools.tool_output import ToolOutput
		agent_name = args.get("agent_name", "")
		message = args.get("message", "")
		if not agent_name or not message:
			return ToolOutput.error("agent_name 和 message 不能为空")
		caller = str(context.get("agent_name", ""))
		if not self._is_agent_allowed(agent_name, caller):
			return ToolOutput.error(f"Agent '{agent_name}' is not allowed")
		exec_ctx: Any = context.get("exec_ctx")
		current_depth: int = exec_ctx.runtime.agent_call_depth if exec_ctx else 0
		if current_depth >= self._max_depth:
			return ToolOutput.error(f"调用深度超过限制({self._max_depth}层)")
		dispatcher = self._get_dispatcher()
		if not dispatcher:
			return ToolOutput.error("MessageBus not configured, cannot dispatch agent call")
		timeout = _bounded_timeout(args.get("timeout", self._timeout), self._timeout)
		if exec_ctx:
			exec_ctx.runtime.agent_call_depth = current_depth + 1
		metadata = _collaboration_metadata(context, exec_ctx, current_depth + 1)
		metadata["parent_tool_call_id"] = str(context.get("tool_call_id", ""))
		envelope = self._envelope_factory.create(caller, agent_name, message, context, metadata)
		start = time.time()
		try:
			reply = await dispatcher.request(envelope, timeout=timeout, event_callback=agent_event_callback(exec_ctx))
			duration_ms = int((time.time() - start) * 1000)
			if reply.type == "error":
				return ToolOutput.error(reply.content)
			durable_summary = _agent_call_durable_summary(agent_name, reply.content)
			return ToolOutput.json_output(
				{"agent": agent_name, "result": reply.content, "duration_ms": duration_ms},
				summary=durable_summary,
			).with_metadata(
				{"durable": True, "durable_summary": durable_summary, "agent_name": agent_name},
			)
		except ValueError as e:
			return ToolOutput.error(str(e))
		except Exception as e:
			return ToolOutput.error(f"Agent 调用失败: {str(e)}")
		finally:
			if exec_ctx:
				exec_ctx.runtime.agent_call_depth = current_depth

	async def _tool_orchestration_task_create(self, args: dict, context: dict):
		return await self._orchestration_handlers.create(args, context)

	async def _tool_orchestration_task_get(self, args: dict, context: dict):
		from axc_agent_engine.tools.tool_output import ToolOutput
		service = self._orchestration_service()
		if not service:
			return ToolOutput.error("Orchestration service not configured")
		task_id = str(args.get("task_id", ""))
		if not task_id:
			return ToolOutput.error("task_id 不能为空")
		get_task = getattr(service, "get_task", None)
		if not callable(get_task):
			return ToolOutput.error("Orchestration service does not support get_task")
		task = get_task(task_id)
		if inspect.isawaitable(task):
			task = await task
		if task is None:
			return ToolOutput.error("orchestration task not found")
		return ToolOutput.json_output(_task_to_dict(task), summary=f"推演任务状态: {_task_status(task)}")

	async def _tool_orchestration_task_cancel(self, args: dict, context: dict):
		from axc_agent_engine.tools.tool_output import ToolOutput
		service = self._orchestration_service()
		if not service:
			return ToolOutput.error("Orchestration service not configured")
		task_id = str(args.get("task_id", ""))
		if not task_id:
			return ToolOutput.error("task_id 不能为空")
		cancel = getattr(service, "cancel_task", None)
		if not callable(cancel):
			return ToolOutput.error("Orchestration service does not support cancel_task")
		try:
			cancelled = await cancel(task_id)
		except TypeError:
			cancelled = cancel(task_id)
			if inspect.isawaitable(cancelled):
				cancelled = await cancelled
		summary = "推演任务已取消" if cancelled else "推演任务未取消"
		return ToolOutput.json_output({"task_id": task_id, "cancelled": bool(cancelled)}, summary=summary)

	def _is_agent_allowed(self, agent_name: str, caller: str = "") -> bool:
		return self._agent_policy.allowed(agent_name, caller)


def _bounded_timeout(value: Any, default: float) -> float:
	try:
		timeout = float(value)
	except (TypeError, ValueError):
		timeout = default
	return max(1.0, min(timeout, 3600.0))


def _collaboration_metadata(context: dict, exec_ctx: Any, depth: int) -> dict[str, Any]:
	metadata = {}
	state = getattr(exec_ctx, "state", None)
	state_metadata = getattr(state, "metadata", None)
	if isinstance(state_metadata, dict):
		metadata.update(state_metadata)
	metadata.update({
		"agent_call_depth": depth,
		"caller_agent": context.get("agent_name", ""),
		"caller_session_id": context.get("session_id", ""),
	})
	return metadata


def _agent_call_durable_summary(agent_name: str, result: Any) -> str:
	text = str(result)
	return f"Agent '{agent_name}' result:\n{text}"


def _task_status(task: Any) -> str:
	return str(getattr(task, "status", ""))


def _task_to_dict(task: Any) -> dict:
	events = getattr(task, "events", [])
	return {
		"task_id": getattr(task, "task_id", ""),
		"status": _task_status(task),
		"mode": getattr(task, "mode", ""),
		"topic": getattr(task, "topic", ""),
		"agent_names": list(getattr(task, "agent_names", []) or []),
		"events": list(events[-20:] if isinstance(events, list) else []),
		"result": getattr(task, "result", {}),
		"error": getattr(task, "error", ""),
	}
