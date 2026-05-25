"""PlanRuntime — PORRunner 使用的单一依赖对象。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
	from axc_agent_engine.core.context import ExecutionContext
	from axc_agent_engine.core.llm_caller import LLMCaller
	from axc_agent_engine.core.message_store import MessageStore
	from axc_agent_engine.core.plugin_manager import PluginManager
	from axc_agent_engine.tools.registry import ToolRegistry


@dataclass
class PlanRuntime:
	"""POR 执行所需的运行时依赖。"""
	llm_caller: "LLMCaller"
	message_store: "MessageStore"
	registry: "ToolRegistry"
	plugin_manager: "PluginManager"
	ctx: "ExecutionContext"
