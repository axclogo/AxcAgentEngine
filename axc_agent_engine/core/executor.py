"""Run lifecycle and strategy dispatch. 管理执行生命周期与策略分发。"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncGenerator
from contextlib import aclosing
from copy import deepcopy
from functools import cached_property

from axc_agent_engine.core.context import ExecutionContext
from axc_agent_engine.core.errors import CancelledError
from axc_agent_engine.core.events import TERMINAL_EVENTS, Event, EventType
from axc_agent_engine.core.execution_run import CheckpointRecorder, ExecutionRunLifecycle, StreamLLMBridge
from axc_agent_engine.core.input_messages import extract_last_user_message
from axc_agent_engine.core.llm_caller import LLMCaller
from axc_agent_engine.core.message_store import MessageStore
from axc_agent_engine.core.plugin_manager import PluginManager
from axc_agent_engine.core.run_context import checkpoint_owner
from axc_agent_engine.plugins.strategy import (
	ExecutionLoop,
	ExecutionStrategyPlugin,
	StrategyRuntime,
)
from axc_agent_engine.plugins.strategy_selection import select_strategy
from axc_agent_engine.runtime.checkpoint import CheckpointStatus
from axc_agent_engine.runtime.inbox import RunInbox
from axc_agent_engine.runtime.tasks import stream_until
from axc_agent_engine.tools.registry import ToolRegistry


class Executor:
	"""Drive a request-scoped loop. 驱动单次请求的执行循环。"""

	def __init__(
		self,
		llm_caller: LLMCaller,
		registry: ToolRegistry,
		plugin_manager: PluginManager,
		ctx: ExecutionContext,
		routing_mode: str = "auto",
		strategy: ExecutionStrategyPlugin | None = None,
	) -> None:
		self._pm = plugin_manager
		self._ctx = ctx
		self._messages = MessageStore()
		self._checkpoint_recorder = CheckpointRecorder(ctx, self._messages)
		self._lifecycle = ExecutionRunLifecycle(plugin_manager, ctx)
		stream_bridge = StreamLLMBridge(llm_caller, ctx)
		self._strategy = strategy or select_strategy(plugin_manager.plugins, "", routing_mode)[0]
		self._runtime = StrategyRuntime(
			llm_caller,
			registry,
			plugin_manager,
			ctx,
			self._messages,
			stream_bridge,
			self._save_strategy_checkpoint,
		)
		self._strategy_state: dict | None = None
		self._run_id = ""
		self._inbox = RunInbox(self._messages, self._persist_control)
		ctx.runtime.inbox = self._inbox
		self._writes: dict[str, dict] = {}
		self._settled: set[str] = set()
		if ctx.services.checkpoint_store:
			ctx.runtime.before_write = self._before_write
			ctx.runtime.after_write = self._settled.add
		self._restored_from_checkpoint = False
		self.skip_user_init: bool = False

	@cached_property
	def _loop(self) -> ExecutionLoop:
		return self._strategy.create_loop(self._runtime)

	@property
	def message_store(self) -> MessageStore:
		return self._messages

	def load_resume_snapshot(
		self,
		run_id: str,
		snapshot: dict,
		*,
		message: str = "",
		resume_metadata: dict | None = None,
	) -> Event | None:
		"""Validate and restore a workflow snapshot. 校验并还原工作流快照。"""
		self._run_id = run_id
		self._ctx.state.metadata["run_id"] = run_id
		if snapshot.get("unsettled_writes"):
			raise ValueError(f"Tool outcome requires reconciliation before resume: {snapshot['unsettled_writes']}")
		self._inbox.restore(snapshot.get("inbox", {}), message=message)
		self._restore_identity(snapshot, resume_metadata or {})
		self._strategy_state = self._strategy.restore_state(snapshot)
		self._checkpoint_recorder.sequence = snapshot.get("sequence", 0)
		self._checkpoint_recorder.forked_from = snapshot.get("forked_from")
		current_round = snapshot.get("current_round", 0)
		input_tokens = snapshot.get("input_tokens", 0)
		output_tokens = snapshot.get("output_tokens", 0)
		if any(type(n) is not int or n < 0 for n in (current_round, input_tokens, output_tokens)):
			raise ValueError("Checkpoint round and token counts must be nonnegative integers")
		self._ctx.state.current_round = current_round
		self._ctx.state.total_input_tokens = input_tokens
		self._ctx.state.total_output_tokens = output_tokens
		self._messages.restore_history(snapshot.get("messages", []))
		self._restored_from_checkpoint = True
		if "result" in snapshot:
			if self._inbox.pending():
				self._strategy_state = None
			else:
				event = Event.done(
					snapshot["result"], {"usage": _usage_metadata(self._ctx), **snapshot["result_metadata"]}
				)
				self._add_receipts(event)
				return event
		return None

	def _restore_identity(self, snapshot: dict, resume_metadata: dict) -> None:
		metadata = snapshot.get("metadata", {})
		if not isinstance(metadata, dict):
			raise TypeError("Invalid checkpoint metadata")
		owner = snapshot.get("owner", {})
		supplied = {**self._ctx.state.metadata, **resume_metadata}
		if snapshot.get("forked_from") and owner != checkpoint_owner(supplied):
			raise ValueError("Branch requires the original tenant and user")
		if any(key in supplied and supplied[key] != value for key, value in owner.items()):
			raise ValueError("Checkpoint belongs to another tenant or user. Supply original resume metadata")
		scopes = snapshot.get("plugin_scopes", {})
		if snapshot.get("forked_from"):
			identity = {**supplied, **metadata}
			resolved = self._pm.fork_scopes(scopes, identity, identity, snapshot.get("session_seed"))
			if resolved != scopes:
				raise ValueError("Resume cannot change stored plugin scopes")
			self._ctx.runtime.plugin_scopes = resolved
		else:
			self._restore_scopes(scopes, metadata, resume_metadata)
		self._ctx.state.metadata.update(owner)
		for key, value in metadata.items():
			if key != "run_id" and value not in ("", None):
				self._ctx.state.metadata[key] = value

	def _restore_scopes(self, scopes: dict, metadata: dict, supplied: dict) -> None:
		if not isinstance(scopes, dict) or not all(
			isinstance(k, str) and isinstance(v, str) and v.strip() for k, v in scopes.items()
		):
			raise ValueError("Invalid checkpoint plugin_scopes")
		self._ctx.runtime.plugin_scopes = dict(scopes)
		saved = {
			**metadata,
			"agent_name": metadata.get("agent_name") or self._ctx.state.metadata.get("agent_name", ""),
			"session_id": metadata.get("session_id") or self._ctx.state.metadata.get("session_id", ""),
		}
		for plugin in self._pm.plugins:
			if plugin.name not in scopes:
				resolved = plugin.restore_legacy_scope(saved, supplied)
				if resolved is not None:
					self._ctx.runtime.plugin_scopes[plugin.name] = resolved

	async def run_stream(self, user_message: str) -> AsyncGenerator[Event, None]:
		"""Stream a run and persist its terminal state. 输出运行事件并保存终止状态。"""
		self._run_id = self._ctx.state.metadata.get("run_id") or uuid.uuid4().hex[:16]
		self._ctx.state.metadata["run_id"] = self._run_id
		run_control = self._ctx.services.run_control
		task = asyncio.current_task()
		store = self._ctx.services.checkpoint_store
		if store and not self._restored_from_checkpoint and await store.latest(self._run_id):
			raise ValueError("Run ID already has a checkpoint. Use resume or choose a new run ID")
		parent = run_control.token(self._ctx.state.metadata.get("parent_run_id", "")) if run_control else None
		if run_control:
			token = run_control.register(self._run_id, self._ctx, task)
			if parent:
				parent.children[self._run_id] = token
				if parent.cancelled:
					token.cancel(parent.reason)
		try:
			async with aclosing(self._run(user_message)) as events:
				async for event in events:
					if event.type in TERMINAL_EVENTS:
						break
					yield event
			await self._record_terminal(event)
		except GeneratorExit:
			await self._save_checkpoint(
				"execution", CheckpointStatus.INTERRUPTED, {"phase": "cancelled", "error": "stream closed"}
			)
			raise
		except Exception as exc:
			await self._save_checkpoint("execution", CheckpointStatus.FAILED, {"phase": "exception", "error": str(exc)})
			await self._lifecycle.error(exc)
			raise
		finally:
			if parent:
				parent.children.pop(self._run_id, None)
			if run_control:
				run_control.unregister(self._run_id, self._ctx, task)
		yield event

	async def _run(self, message: str) -> AsyncGenerator[Event, None]:
		result = error = ""
		try:
			restoring = self._restored_from_checkpoint
			await self._start(message)
			state = deepcopy(self._strategy_state)
			if restoring:
				if (state is None or not self._strategy.defer_resume_input) and (pending := await self._inbox.take()):
					message = "\n".join(pending)
				if not message:
					message = extract_last_user_message(self._messages.get_all())
			timeout = self._ctx.config.total_timeout
			deadline = self._runtime._start_time + timeout if timeout else None
			while True:
				stream = stream_until(
					self._loop.run(message, state), deadline, Event.error(f"总执行超时（{timeout}s）")
				)
				async with aclosing(stream) as events:
					async for event in events:
						if event.type in TERMINAL_EVENTS:
							break
						yield event
					else:
						raise RuntimeError(f"Strategy {self._strategy.name} ended without a terminal event")
				if event.type != EventType.DONE:
					break
				followup = await self._inbox.take(finish=True, progress={"strategy_state": None})
				if not followup:
					break
				state = None
				message = "\n".join(followup)
				self._strategy_state = None

			if event.type == EventType.DONE:
				result = await self._lifecycle.complete(event.content)
				event = Event.done(result, {**event.metadata, "usage": _usage_metadata(self._ctx)})
			else:
				error = event.content
		except (asyncio.CancelledError, CancelledError) as exc:
			error = str(exc) or self._ctx.state.cancel_reason or "cancelled"
			self._ctx.cancel(error)
			event = Event.cancelled(error, {"usage": _usage_metadata(self._ctx)})
		except Exception as exc:
			error = str(exc)
			raise
		finally:
			self._inbox.open = False
			await self._lifecycle.end(result, error)
		yield event

	async def _record_terminal(self, event: Event) -> None:
		if event.type == EventType.DONE:
			status = CheckpointStatus.COMPLETED
			fields = {
				"phase": "done",
				"forkable": True,
				"result": event.content,
				"result_metadata": event.metadata,
				"usage": event.metadata["usage"],
			}
		elif event.type == EventType.ERROR:
			status = CheckpointStatus.FAILED
			fields = {"phase": "error", "error": event.content}
		else:
			status = CheckpointStatus.INTERRUPTED
			fields = {"phase": "cancelled", "error": event.content}

		self._add_receipts(event)
		await self._save_checkpoint("execution", status, fields)

	def _add_receipts(self, event: Event) -> None:
		if self._inbox.consumed:
			event.metadata["processed_message_ids"] = list(self._inbox.entries)[: self._inbox.consumed]
		if pending := self._inbox.pending():
			event.metadata["unprocessed_message_ids"] = list(self._inbox.entries)[-len(pending) :]

	async def _start(self, user_message: str) -> None:
		self._runtime._start_time = asyncio.get_running_loop().time()
		restoring = self._restored_from_checkpoint
		self._restored_from_checkpoint = False
		if not restoring:
			self._ctx.state.current_round = 0
			self._init_messages(user_message)
		await self._lifecycle.start()
		if not restoring:
			self._messages.upsert_plugin_context(self._pm.collect_context(self._ctx))
		await self._save_checkpoint("execution", CheckpointStatus.RUNNING, {"phase": "start"})

	async def _save_strategy_checkpoint(self, kind: str, status: CheckpointStatus, fields: dict | None) -> None:
		fields = fields or {}
		reserved = fields.keys() & {
			"run_id",
			"kind",
			"messages",
			"cursor",
			"usage",
			"payload",
			"metadata",
			"strategy",
			"state_version",
			"inbox",
			"unsettled_writes",
			"plugin_scopes",
			"fork_state",
			"owner",
		}
		if reserved:
			raise ValueError(f"Checkpoint fields reserved by the engine: {sorted(reserved)}")
		if "forkable" in fields and not isinstance(fields["forkable"], bool):
			raise ValueError("forkable must be a bool")
		state = fields.get("strategy_state")
		if state is not None and not isinstance(state, dict):
			raise ValueError("strategy_state must be a dict or None")
		await self._save_checkpoint(kind, status, fields)

	async def _save_checkpoint(self, kind: str, status: CheckpointStatus, extra_state: dict | None = None) -> None:
		"""Persist progress through the host checkpoint store. 将进度保存到宿主检查点存储。"""
		async with self._inbox.lock:
			fields = dict(extra_state or {})
			phase = fields.get("phase")
			step = fields.pop("settled_step", None)
			cleared = {
				key
				for key in self._settled
				if phase in {"done", "strategy_saved"}
				or (step is not None or phase == "round_end")
				and self._writes[key]["step_id"] == step
			}
			remaining = {key: value for key, value in self._writes.items() if key not in cleared}
			await self._persist(kind, status, fields, remaining)
			self._writes = remaining
			self._settled.difference_update(cleared)

	async def _before_write(self, call_id: str, name: str, step_id: int | None) -> None:
		async with self._inbox.lock:
			if call_id in self._writes:
				raise ValueError(f"Duplicate outstanding tool call ID: {call_id}")
			writes = {**self._writes, call_id: {"name": name, "step_id": step_id}}
			await self._persist("execution", CheckpointStatus.RUNNING, {"phase": "control"}, writes)
			self._writes = writes

	async def _persist_control(self, progress: dict | None = None) -> None:
		await self._persist(
			"execution", CheckpointStatus.RUNNING, {"phase": "control", **(progress or {})}, self._writes
		)

	async def _persist(self, kind: str, status: CheckpointStatus, fields: dict, writes: dict) -> None:
		state = deepcopy(fields["strategy_state"]) if "strategy_state" in fields else self._strategy_state
		if fields.pop("forkable", False) and self._ctx.services.checkpoint_store and not writes:
			fields.update(self._pm.fork_checkpoint_fields(self._ctx))
		if self._inbox.entries:
			fields["inbox"] = self._inbox.snapshot()
		if writes:
			fields["unsettled_writes"] = writes
		fields.update(
			strategy=self._strategy.name,
			state_version=self._strategy.state_version,
			strategy_state=state,
		)
		await self._checkpoint_recorder.save(self._run_id, kind, status, fields)
		self._strategy_state = state

	def _init_messages(self, user_message: str) -> None:
		"""Build the initial run history. 构建本次运行的初始消息历史。"""
		self._messages.init_system_prompt(self._ctx.config.system_prompt)
		if not self.skip_user_init:
			user_msg = {"role": "user", "content": user_message}
			self._messages.append(user_msg)
			self._ctx.add_image_tokens([user_msg])


def _usage_metadata(ctx: ExecutionContext) -> dict[str, int]:
	return {
		"input_tokens": ctx.state.total_input_tokens,
		"output_tokens": ctx.state.total_output_tokens,
		"total_tokens": ctx.state.total_input_tokens + ctx.state.total_output_tokens,
	}
