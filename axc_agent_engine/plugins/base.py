"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
插件基类 — 所有内置和外部插件都继承它。

English: Base class for all builtin and external plugins."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
	from axc_agent_engine.core.context import ExecutionContext
	from axc_agent_engine.plugins import PluginContext
	from axc_agent_engine.core.schema import ToolDefinition
	from axc_agent_engine.tools.tool_output import ToolOutput


class BasePlugin:
	"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
插件基类。

	生命周期：
	  initialize → on_execution_start → [inject_context → transform_messages
	    → pre_llm_call → post_llm_call → pre_tool_call → post_tool_call
	    → on_round_end → should_stop] × N rounds → on_execution_end → destroy

	所有 hook 统一使用 async，便于支持异步操作。

	English: Defines the plugin lifecycle and hook contract. Hooks are async-first
	so plugins can perform I/O without blocking the engine.
	"""
	name: str = ""
	display_name: str = ""
	priority: int = 50
	phase: str = "core"  # English: execution ordering phase, "pre" | "core" | "post". 中文：用于排序的执行阶段。
	version: str = "0.1.0"
	depends_on: list[str] = []  # English: explicit plugin dependency names. 中文：显式依赖声明。
	fail_closed: bool = False  # English: fail hook errors closed. 中文：hook 失败时中止执行，而不是吞掉错误。

	def initialize(self, config: dict, plugin_ctx: "PluginContext") -> None:
		"""Engine 级初始化，传入配置和上下文。
		子类应调用 super().initialize(config, plugin_ctx) 保存 plugin_ctx。

		English: Engine-level initialization. Subclasses should call super() to
		keep plugin_ctx available.
		"""
		self._plugin_ctx = plugin_ctx

	async def close(self) -> None:
		"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
释放插件资源（异步）。

		English: Release plugin resources asynchronously.
		"""
		pass

	#English: Bilingual note. 中文：── 异步 hooks ──
	#English: English: Asynchronous lifecycle hooks. 中文：源码说明。

	async def on_execution_start(self, exec_ctx: "ExecutionContext") -> None:
		pass

	async def on_execution_end(self, exec_ctx: "ExecutionContext", result: str, error: str) -> None:
		pass

	async def on_execution_complete(self, exec_ctx: "ExecutionContext", result: str, trace: dict) -> str:
		"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
执行完成后的处理，可修改最终结果。

		English: Post-process the final result after execution completes.
		"""
		return result

	async def on_round_end(self, exec_ctx: "ExecutionContext", user_message: str,
						   assistant_message: str, tool_calls: list[dict]) -> None:
		pass

	async def on_error(self, exec_ctx: "ExecutionContext", error: Exception) -> None:
		pass

	async def on_plan_created(self, exec_ctx: "ExecutionContext", plan_info: dict) -> None:
		"""English: This documentation describes the related engine component behavior.
中文：计划创建后触发。"""
		pass

	async def on_step_completed(self, exec_ctx: "ExecutionContext", step_info: dict) -> None:
		"""English: This documentation describes the related engine component behavior.
中文：步骤完成后触发。"""
		pass

	async def post_llm_call(self, exec_ctx: "ExecutionContext", messages: list[dict],
							response: dict, duration_ms: int) -> None:
		pass

	async def pre_tool_call(self, exec_ctx: "ExecutionContext", tool_name: str,
							arguments: dict) -> tuple[bool, dict]:
		"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
工具调用前 hook，返回 (是否允许, 可能修改后的参数)。

		English: Pre-tool hook returning (allowed, possibly modified arguments).
		"""
		return True, arguments

	async def post_tool_call(self, exec_ctx: "ExecutionContext", tool_name: str,
							 arguments: dict, result: "ToolOutput", duration_ms: int) -> "ToolOutput":
		"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
工具调用后 hook，接收并返回 ToolOutput。

		English: Post-tool hook that receives and returns a ToolOutput.
		"""
		return result

	async def on_tool_call_failed(self, exec_ctx: "ExecutionContext", tool_name: str,
								  arguments: dict, error: dict, duration_ms: int) -> None:
		"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
工具调用失败 hook；不修改工具结果，用于 tracing/audit 类插件补齐失败观测。

		English: Failure-observation hook for tracing/audit plugins; it does not
		change the tool result.
		"""
		pass

	#English: Bilingual note. 中文：── 同步 hooks（轻量，不做 I/O） ──
	#English: English: Lightweight synchronous hooks; do not perform I/O here. 中文：源码说明。

	def inject_context(self, exec_ctx: "ExecutionContext", topic: str = "") -> str:
		"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
返回要注入 system prompt 的额外上下文。

		English: Return extra context to inject into the system prompt.
		"""
		return ""

	def transform_messages(self, messages: list[dict], exec_ctx: "ExecutionContext",
						   current_message: str = "") -> list[dict]:
		"""LLM 调用前转换消息列表。"""
		return messages

	def get_tools(self) -> "list[ToolDefinition]":
		"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
返回工具定义；可使用 initialize() 阶段设置的 self._plugin_ctx。

		English: Return tool definitions; plugins may use self._plugin_ctx set by initialize().
		"""
		return []

	def pre_llm_call(self, exec_ctx: "ExecutionContext", messages: list[dict],
					 tools: list[dict] | None) -> tuple[list[dict], list[dict] | None]:
		"""LLM 调用前 hook，返回 (messages, tools)。"""
		return messages, tools

	def should_stop(self, exec_ctx: "ExecutionContext") -> tuple[bool, str]:
		"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
检查是否应停止执行，返回 (should_stop, reason)。

		English: Return whether execution should stop and the reason.
		"""
		return False, ""
