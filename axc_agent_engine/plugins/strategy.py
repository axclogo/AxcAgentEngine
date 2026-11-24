"""Execution strategy contract. 执行策略插件接口。"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable
from contextlib import aclosing
from copy import deepcopy
from dataclasses import dataclass
from typing import Protocol

from axc_agent_engine.core.context import ExecutionContext
from axc_agent_engine.core.errors import ExecutionTimeoutError, MaxRoundsError
from axc_agent_engine.core.events import Event
from axc_agent_engine.core.execution_run import StreamLLMBridge
from axc_agent_engine.core.llm_caller import LLMCaller
from axc_agent_engine.core.message_store import MessageStore
from axc_agent_engine.core.plugin_manager import PluginManager
from axc_agent_engine.core.react_loop import ReActTurnResult, ReActTurnRunner
from axc_agent_engine.plugins.base import BasePlugin
from axc_agent_engine.runtime.checkpoint import CheckpointStatus
from axc_agent_engine.tools.executor import ToolResult
from axc_agent_engine.tools.orchestrator import execute_tool_calls
from axc_agent_engine.tools.registry import ToolRegistry

__all__ = [
	"ExecutionLoop",
	"ExecutionStrategyPlugin",
	"ModelReply",
	"ReActTurnResult",
	"StrategyRuntime",
	"validate_state_version",
]


@dataclass(frozen=True)
class ModelReply:
	"""Final assistant message after streaming events. 流式事件之后的完整助手消息。"""

	message: dict


@dataclass
class StrategyRuntime:
	"""Public operations bound to one run. 每次运行独享的策略接口。"""

	_llm: LLMCaller
	_registry: ToolRegistry
	_plugins: PluginManager
	_ctx: ExecutionContext
	_messages: MessageStore
	_stream: StreamLLMBridge
	_checkpoint: Callable[[str, CheckpointStatus, dict | None], Awaitable[None]]
	_start_time: float = 0

	def check_cancelled(self) -> None:
		self._ctx.check_cancelled()

	def begin_round(self) -> int:
		self.check_cancelled()
		if self._ctx.state.current_round >= self._ctx.config.max_rounds:
			raise MaxRoundsError(f"Exceeded max rounds {self._ctx.config.max_rounds}")
		timeout = self._ctx.config.total_timeout
		if timeout and asyncio.get_running_loop().time() - self._start_time >= timeout:
			raise ExecutionTimeoutError(f"总执行超时（{timeout}s）")
		self._ctx.state.current_round += 1
		return self._ctx.state.current_round

	def model_turn(self, user_message: str) -> AsyncIterator[Event | ReActTurnResult]:
		"""Run one model and tool round with host hooks. 通过宿主钩子完成一次模型及工具调用。"""
		runner = ReActTurnRunner(self._llm, self._registry, self._plugins, self._ctx, self._messages)
		return runner.run(user_message, stream_llm_call=self._stream.call)

	def stop_reason(self) -> str | None:
		stop, reason = self._plugins.check_should_stop(self._ctx)
		return reason if stop else None

	async def report_error(self, error: Exception) -> None:
		await self._plugins.on_error(self._ctx, error)

	async def checkpoint_round(self, tools: list[str] | None = None, *, state: dict | None = None) -> None:
		if tools is None:
			await self._checkpoint("round", CheckpointStatus.RUNNING, {"phase": "round_start"})
		else:
			await self._checkpoint(
				"round",
				CheckpointStatus.COMPLETED,
				{
					"phase": "round_end",
					"tool_calls": tools,
					"forkable": True,
					"strategy_state": state,
				},
			)

	def history(self) -> list[dict]:
		return self._messages.get_all()

	def append_message(self, message: dict) -> None:
		self._messages.append(message)

	async def call_model(
		self, user_message: str, tools: list[dict] | None = None
	) -> AsyncGenerator[Event | ModelReply, None]:
		"""Apply message hooks and stream the final reply. 应用消息钩子并产出模型事件和完整回复。"""
		self.check_cancelled()
		pending = await self.receive_messages()
		messages = self._plugins.transform_messages(
			self.history(), self._ctx, "\n".join(pending) if pending else user_message
		)
		async with aclosing(self._stream.call(messages, tools)) as response:
			async for item in response:
				if isinstance(item, Event):
					yield item
				else:
					reply, events = item
					for event in events:
						yield event
					yield ModelReply(reply)

	async def receive_messages(self) -> list[str]:
		"""Admit queued input into run history in order. 按顺序将补充消息写入运行历史。"""
		return await self._ctx.runtime.inbox.take() if self._ctx.runtime.inbox else []

	def tool_schemas(self) -> list[dict]:
		return self._registry.get_openai_schemas()

	async def execute_tools(self, calls: list[dict]) -> list[ToolResult]:
		"""Execute normalized calls under the agent's tool policy. 按 Agent 的工具策略执行标准化调用。"""
		return await execute_tool_calls(calls, self._registry, self._plugins, self._ctx)

	def append_tool_results(self, results: list[ToolResult]) -> None:
		self._messages.append_tool_results(results)

	async def end_round(self, user_message: str, reply: str, calls: list[dict]) -> None:
		await self._plugins.on_round_end(self._ctx, user_message, reply, calls)

	async def save_state(self, kind: str, state: dict, *, forkable: bool = False) -> None:
		await self._checkpoint(
			kind,
			CheckpointStatus.RUNNING,
			{
				"phase": "strategy_saved",
				"strategy_state": state,
				"forkable": forkable,
			},
		)


class ExecutionLoop(Protocol):
	"""Yield events through one terminal event. 每次运行产生事件，直到首个终止事件。"""

	def run(self, message: str, state: dict | None) -> AsyncGenerator[Event, None]: ...


class ExecutionStrategyPlugin(BasePlugin, ABC):
	"""Create a separate loop for each request. 为每次请求创建独立循环。"""

	state_version: int = 1
	defer_resume_input: bool = False

	def __init_subclass__(cls) -> None:
		super().__init_subclass__()
		validate_state_version(cls.state_version)

	def restore_state(self, snapshot: dict) -> dict | None:
		"""Validate and detach a saved continuation. 校验并复制已保存的续跑状态。"""
		owner = snapshot.get("strategy")
		if owner is None and self.name not in {"react", "por"}:
			raise ValueError(f"Cannot restore an unnamed checkpoint with strategy {self.name}")
		if owner is not None and owner != self.name:
			raise ValueError(f"Checkpoint requires strategy {owner}, configured {self.name}")
		version = snapshot.get("state_version", 1)
		validate_state_version(version)
		state = deepcopy(snapshot.get("strategy_state"))
		if state is not None and not isinstance(state, dict):
			raise ValueError("strategy_state must be a dict or None")
		if version != self.state_version:
			state = self.migrate_state(state, version)
			if state is not None and not isinstance(state, dict):
				raise ValueError("migrate_state must return a dict or None")
		return state

	def migrate_state(self, state: dict | None, from_version: int) -> dict | None:
		"""Migrate an older snapshot before any run hooks. 在执行钩子前迁移旧版本的快照。"""
		raise ValueError(f"Strategy {self.name} cannot restore state version {from_version} as {self.state_version}")

	def inspection_settings(self) -> dict:
		"""Public settings suitable for inspection. 可供诊断展示的公开配置。"""
		return {}

	@abstractmethod
	def create_loop(self, runtime: StrategyRuntime) -> ExecutionLoop: ...


def validate_state_version(version: int) -> None:
	if type(version) is not int or version < 1:
		raise ValueError("Strategy state_version must be a positive integer")
