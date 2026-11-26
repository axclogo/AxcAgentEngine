"""Base class for all builtin and external plugins.
插件基类 — 所有内置和外部插件都继承它。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar

from axc_agent_engine.plugins.config_schema import PluginConfigSchema

if TYPE_CHECKING:
	from axc_agent_engine.core.context import ExecutionContext
	from axc_agent_engine.core.schema import ToolDefinition
	from axc_agent_engine.plugins.context import PluginContext
	from axc_agent_engine.tools.tool_output import ToolOutput


@dataclass(frozen=True)
class PreToolCallDecision:
	"""A single tool call’s gate result. 单次工具调用的放行或拒绝结果。"""

	allowed: bool
	arguments: dict
	plugin_name: str = ""
	reason: str = ""
	code: str = "tool.rejected_by_plugin"
	details: dict | None = None


class BasePlugin:
	"""Defines the plugin lifecycle and hook contract. Hooks are async-first
	        so plugins can perform I/O without blocking the engine.
	插件基类。

	        生命周期：
	          initialize → on_execution_start → [inject_context → transform_messages
	            → pre_llm_call → post_llm_call → pre_tool_call → post_tool_call
	            → on_round_end → should_stop] × N rounds → on_execution_end → destroy

	        所有 hook 统一使用 async，便于支持异步操作。"""

	name: str = ""
	display_name: str = ""
	supports_session_fork: bool = True
	priority: int = 50
	phase: str = "core"  # English: execution ordering phase, "pre" | "core" | "post". 中文：用于排序的执行阶段。
	version: str = "0.1.0"
	depends_on: ClassVar[list[str]] = []  # English: explicit plugin dependency names. 中文：显式依赖声明。
	config_schema: PluginConfigSchema | dict | None = None

	def initialize(self, config: dict, plugin_ctx: PluginContext) -> None:
		"""Engine 级初始化，传入配置和上下文。
		子类应调用 super().initialize(config, plugin_ctx) 保存 plugin_ctx。

		English: Engine-level initialization. Subclasses should call super() to
		keep plugin_ctx available.
		"""
		self._plugin_ctx = plugin_ctx

	def restore_legacy_scope(self, saved: dict, supplied: dict) -> str | None:
		"""Recover a missing plugin scope before run hooks. 在运行钩子前恢复旧快照缺少的插件归属。"""
		return None

	def capture_session(self, ctx: ExecutionContext) -> dict | None:
		"""Portable session-private data at this cut. 此切点上的可移植会话私有数据。"""
		return None

	async def restore_session(self, ctx: ExecutionContext, state: dict) -> None:
		"""Install a detached seed in the child session. 将独立的起始数据还原至子会话。"""
		raise ValueError(f"Plugin {self.name} cannot restore session data")

	def fork_scope(self, scope: str | None, source: dict, target: dict, state: dict | None) -> str | None:
		"""Bind a stored scope to the child identity. 将已存作用域绑定到子会话身份。"""
		if scope is not None:
			raise ValueError(f"Plugin {self.name} cannot fork its stored scope")
		return None

	async def close(self) -> None:
		"""Release plugin resources asynchronously.
		释放插件资源（异步）。"""

	# Asynchronous hooks. 中文：── 异步 hooks ──
	# Asynchronous lifecycle hooks. 异步生命周期钩子。

	async def on_session_reset(self, agent_name: str, session_id: str) -> None:
		"""Drop session state. An empty ID covers all sessions of this agent. 清理会话状态，空 ID 表示当前 Agent 的全部会话。"""

	async def on_session_rewind(self, agent_name: str, session_id: str, before_run_id: str) -> None:
		"""Run before history is rewritten. 在历史改写前调用。"""

	async def on_execution_start(self, exec_ctx: ExecutionContext) -> None:
		pass

	async def on_execution_end(self, exec_ctx: ExecutionContext, result: str, error: str) -> None:
		pass

	async def on_execution_complete(self, exec_ctx: ExecutionContext, result: str, trace: dict) -> str:
		"""Post-process the final result after execution completes.
		执行完成后的处理，可修改最终结果。"""
		return result

	async def on_round_end(
		self, exec_ctx: ExecutionContext, user_message: str, assistant_message: str, tool_calls: list[dict]
	) -> None:
		pass

	async def on_error(self, exec_ctx: ExecutionContext, error: Exception) -> None:
		pass

	async def on_plan_created(self, exec_ctx: ExecutionContext, plan_info: dict) -> None:
		"""Called when a plan is created. 计划创建后触发。"""

	async def on_step_completed(self, exec_ctx: ExecutionContext, step_info: dict) -> None:
		"""Called after a plan step completes. 步骤完成后触发。"""

	async def post_llm_call(
		self, exec_ctx: ExecutionContext, messages: list[dict], response: dict, duration_ms: int
	) -> None:
		pass

	async def pre_tool_call(
		self, exec_ctx: ExecutionContext, tool_name: str, arguments: dict
	) -> tuple[bool, dict] | PreToolCallDecision:
		"""Return permission and arguments, optionally with a rejection reason. 返回放行标志、参数及可选拒绝原因。"""
		return True, arguments

	async def post_tool_call(
		self, exec_ctx: ExecutionContext, tool_name: str, arguments: dict, result: ToolOutput, duration_ms: int
	) -> ToolOutput:
		"""Post-tool hook that receives and returns a ToolOutput.
		工具调用后 hook，接收并返回 ToolOutput。"""
		return result

	async def on_tool_call_failed(
		self, exec_ctx: ExecutionContext, tool_name: str, arguments: dict, error: dict, duration_ms: int
	) -> None:
		"""Failure-observation hook for tracing/audit plugins; it does not
		                change the tool result.
		工具调用失败 hook；不修改工具结果，用于 tracing/audit 类插件补齐失败观测。"""

	# Synchronous hooks. 中文：── 同步 hooks（轻量，不做 I/O） ──
	# Lightweight synchronous hooks; do not perform I/O here. 同步钩子应轻量，避免在此执行 I/O。

	def inject_context(self, exec_ctx: ExecutionContext, topic: str = "") -> str:
		"""Return extra context to inject into the system prompt.
		返回要注入 system prompt 的额外上下文。"""
		return ""

	def transform_messages(
		self, messages: list[dict], exec_ctx: ExecutionContext, current_message: str = ""
	) -> list[dict]:
		"""LLM 调用前转换消息列表。"""
		return messages

	def get_tools(self) -> list[ToolDefinition]:
		"""Return tool definitions; plugins may use self._plugin_ctx set by initialize().
		返回工具定义；可使用 initialize() 阶段设置的 self._plugin_ctx。"""
		return []

	def pre_llm_call(
		self, exec_ctx: ExecutionContext, messages: list[dict], tools: list[dict] | None
	) -> tuple[list[dict], list[dict] | None]:
		"""LLM 调用前 hook，返回 (messages, tools)。"""
		return messages, tools

	def should_stop(self, exec_ctx: ExecutionContext) -> tuple[bool, str]:
		"""Return whether execution should stop and the reason.
		检查是否应停止执行，返回 (should_stop, reason)。"""
		return False, ""
