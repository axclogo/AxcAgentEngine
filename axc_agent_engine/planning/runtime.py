"""PlanRuntime — PORRunner 使用的单一依赖对象。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from axc_agent_engine.plugins.strategies.react_kernel import ReActKernel
from axc_agent_engine.runtime.checkpoint import CheckpointStatus

if TYPE_CHECKING:
	from axc_agent_engine.core.context import ExecutionContext
	from axc_agent_engine.core.llm_caller import LLMCaller
	from axc_agent_engine.core.message_store import MessageStore
	from axc_agent_engine.core.plugin_manager import PluginManager
	from axc_agent_engine.tools.registry import ToolRegistry


DEFAULT_PARALLEL_STEPS = 4


@dataclass
class PlanRuntime:
	"""POR 执行所需的运行时依赖。"""

	llm_caller: LLMCaller
	message_store: MessageStore
	registry: ToolRegistry
	plugin_manager: PluginManager
	ctx: ExecutionContext
	step_factory: Callable = ReActKernel
	checkpoint: Callable[[str, CheckpointStatus, dict | None], Awaitable[None]] | None = None
	max_parallel_steps: int = DEFAULT_PARALLEL_STEPS
