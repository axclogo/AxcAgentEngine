"""Run lifecycle, checkpoint recording and model streaming. 运行生命周期、检查点记录和模型流输出。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator

from axc_agent_engine.core.context import ExecutionContext
from axc_agent_engine.core.events import Event
from axc_agent_engine.core.llm_caller import LLMCaller
from axc_agent_engine.core.message_store import MessageStore
from axc_agent_engine.core.plugin_manager import PluginManager
from axc_agent_engine.core.run_context import checkpoint_owner
from axc_agent_engine.core.stream_sink import QueueStreamSink
from axc_agent_engine.runtime.checkpoint import Checkpoint, CheckpointStatus
from axc_agent_engine.runtime.tasks import cancel_and_wait


class CheckpointRecorder:
	"""Maintain checkpoint sequence numbers for one run. 管理单次运行的检查点序号。"""

	def __init__(self, ctx: ExecutionContext, messages: MessageStore) -> None:
		self._ctx = ctx
		self._messages = messages
		self.sequence = 0
		self.forked_from: dict | None = None

	async def save(
		self,
		run_id: str,
		kind: str,
		status: CheckpointStatus,
		extra_state: dict | None = None,
	) -> None:
		store = self._ctx.services.checkpoint_store
		if not store:
			return
		state = execution_checkpoint_state(self._ctx, self._messages, run_id, extra_state)
		self.sequence += 1
		await store.save(
			Checkpoint(
				run_id=run_id,
				sequence=self.sequence,
				status=status,
				kind=kind,
				state=state,
				metadata={"forked_from": self.forked_from} if self.forked_from else {},
			)
		)


def execution_checkpoint_state(
	ctx: ExecutionContext,
	messages: MessageStore,
	run_id: str,
	extra_state: dict | None = None,
) -> dict:
	metadata = {
		"run_id": run_id,
		"session_id": ctx.state.metadata.get("session_id", ""),
		"agent_name": ctx.state.metadata.get("agent_name", ""),
	}
	phase = str((extra_state or {}).get("phase") or "execution")
	state = {
		"run_id": run_id,
		"kind": (extra_state or {}).get("kind", "execution"),
		"phase": phase,
		"cursor": {"current_round": ctx.state.current_round},
		"messages": messages.get_all(),
		"usage": {
			"input_tokens": ctx.state.total_input_tokens,
			"output_tokens": ctx.state.total_output_tokens,
		},
		"payload": {},
		"metadata": metadata,
	}
	if identity := checkpoint_owner(ctx.state.metadata):
		state["owner"] = identity
	if ctx.runtime.plugin_scopes:
		state["plugin_scopes"] = dict(ctx.runtime.plugin_scopes)
	if extra_state:
		state["payload"] = {
			key: value
			for key, value in extra_state.items()
			if key
			not in {
				"phase",
				"kind",
				"strategy",
				"state_version",
				"strategy_state",
				"inbox",
				"unsettled_writes",
				"fork_state",
			}
		}
		state.update(extra_state)
	return state


class ExecutionRunLifecycle:
	"""Dispatch run lifecycle hooks. 分发运行生命周期钩子。"""

	def __init__(self, plugin_manager: PluginManager, ctx: ExecutionContext) -> None:
		self._pm = plugin_manager
		self._ctx = ctx

	async def start(self) -> None:
		await self._pm.on_execution_start(self._ctx)

	async def complete(self, content: str) -> str:
		trace = {
			"input_tokens": self._ctx.state.total_input_tokens,
			"output_tokens": self._ctx.state.total_output_tokens,
			"rounds": self._ctx.state.current_round,
		}
		return await self._pm.on_execution_complete(self._ctx, content, trace)

	async def error(self, error: Exception) -> None:
		await self._pm.on_error(self._ctx, error)

	async def end(self, result: str, error: str) -> None:
		await self._pm.on_execution_end(self._ctx, result, error)


class StreamLLMBridge:
	"""Forward model events and join the producer when the consumer closes. 转发模型事件并在消费端关闭时回收任务。"""

	def __init__(self, llm_caller: LLMCaller, ctx: ExecutionContext) -> None:
		self._llm = llm_caller
		self._ctx = ctx

	async def call(
		self,
		messages: list[dict],
		tools_schema: list[dict] | None,
	) -> AsyncGenerator[Event | tuple[dict, list[Event]], None]:
		queue: asyncio.Queue[Event | None] = asyncio.Queue(maxsize=1000)
		sink = QueueStreamSink(queue)
		done_marker = None
		consumer_closed = False

		async def _run_llm() -> tuple[dict, list[Event]]:
			try:
				return await self._llm.call(self._ctx, messages, tools_schema, stream_sink=sink)
			finally:
				if not consumer_closed:
					await queue.put(done_marker)

		task = asyncio.create_task(_run_llm())
		try:
			while True:
				event = await queue.get()
				if event is done_marker:
					break
				yield event
		finally:
			consumer_closed = True
			self._ctx.runtime.stream_delta_emitted = False
			await cancel_and_wait(task)
		yield task.result()
