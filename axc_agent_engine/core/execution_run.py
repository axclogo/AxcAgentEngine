"""Executor run helpers kept outside the public Executor facade.
中文：此文档说明相关引擎组件的行为。"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator

from axc_agent_engine.core.context import ExecutionContext
from axc_agent_engine.core.errors import ProviderError
from axc_agent_engine.core.events import Event
from axc_agent_engine.core.llm_caller import LLMCaller
from axc_agent_engine.core.message_store import MessageStore
from axc_agent_engine.core.plugin_manager import PluginManager
from axc_agent_engine.core.stream_sink import QueueStreamSink
from axc_agent_engine.runtime.checkpoint import Checkpoint, CheckpointStatus

logger = logging.getLogger(__name__)


class CheckpointRecorder:
	"""Best-effort execution checkpoint persistence.
中文：此文档说明相关引擎组件的行为。"""

	def __init__(self, ctx: ExecutionContext, messages: MessageStore) -> None:
		self._ctx = ctx
		self._messages = messages

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
		try:
			await store.save(Checkpoint(
				run_id=run_id,
				sequence=len(self._messages.get_all()) + self._ctx.state.current_round,
				status=status,
				kind=kind,
				state=state,
			))
		except Exception as e:
			logger.warning(f"Checkpoint save error: {e}")


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
		#English: Backward-compatible fields for existing stores/tests. 中文：源码说明。
		"current_round": ctx.state.current_round,
		"input_tokens": ctx.state.total_input_tokens,
		"output_tokens": ctx.state.total_output_tokens,
	}
	if extra_state:
		payload = dict(extra_state)
		payload.pop("phase", None)
		payload.pop("kind", None)
		state["payload"] = payload
		state.update(extra_state)
	return state


class ExecutionRunLifecycle:
	"""Plugin-facing lifecycle hooks for an executor run.
中文：此文档说明相关引擎组件的行为。"""

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
	"""Runs an LLM call in a task and yields realtime stream events explicitly.
中文：此文档说明相关引擎组件的行为。"""

	def __init__(self, llm_caller: LLMCaller, ctx: ExecutionContext) -> None:
		self._llm = llm_caller
		self._ctx = ctx

	async def call(
		self,
		messages: list[dict],
		tools_schema: list[dict] | None,
	) -> AsyncIterator[Event | tuple[dict, list[Event]]]:
		queue: asyncio.Queue[Event | None] = asyncio.Queue(maxsize=1000)
		sink = QueueStreamSink(queue)
		done_marker = None
		llm_result: list = []

		async def _run_llm() -> None:
			try:
				msg, evts = await self._llm.call(self._ctx, messages, tools_schema, stream_sink=sink)
				llm_result.append((msg, evts))
			except Exception as e:
				llm_result.append(e)
			finally:
				await queue.put(done_marker)

		task = asyncio.create_task(_run_llm())
		try:
			while True:
				event = await queue.get()
				if event is done_marker:
					break
				yield event
		finally:
			self._ctx.runtime.stream_delta_emitted = False
			if not task.done():
				task.cancel()
				try:
					await task
				except (asyncio.CancelledError, Exception):
					pass
		if not llm_result:
			raise ProviderError("LLM call produced no result")
		result = llm_result[0]
		if isinstance(result, Exception):
			raise result
		yield result
