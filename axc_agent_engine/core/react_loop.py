"""Reusable ReAct turn/loop services. ReAct 单轮执行与循环服务。"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from axc_agent_engine.core.errors import ProviderError
from axc_agent_engine.core.events import Event, EventType
from axc_agent_engine.runtime.tasks import cancel_and_wait, managed_stream
from axc_agent_engine.tools.orchestrator import execute_tool_calls
from axc_agent_engine.tools.utils import parse_tool_calls

if TYPE_CHECKING:
	from axc_agent_engine.core.context import ExecutionContext
	from axc_agent_engine.core.llm_caller import LLMCaller
	from axc_agent_engine.core.message_store import MessageStore
	from axc_agent_engine.core.plugin_manager import PluginManager
	from axc_agent_engine.tools.registry import ToolRegistry


StreamLLMCall = Callable[[list[dict], list[dict] | None], AsyncIterator[Event | tuple[dict, list[Event]]]]
EventFilter = Callable[[Event], bool]
EventSink = Callable[[Event], None]


@dataclass
class ToolFlowResult:
	parsed_calls: list[dict] = field(default_factory=list)
	events: list[Event] = field(default_factory=list)


@dataclass
class ReActTurnResult:
	message: dict
	content: str
	parsed_calls: list[dict] = field(default_factory=list)
	has_tool_calls: bool = False
	failed: bool = False
	user_message: str = ""


class ToolCallFlow:
	"""Parse, execute, store, and optionally emit tool call results for one ReAct round. 解析并执行本轮工具调用，记录结果并按需发送事件。"""

	def __init__(
		self,
		registry: ToolRegistry,
		plugin_manager: PluginManager,
		ctx: ExecutionContext,
		messages: MessageStore,
	) -> None:
		self._registry = registry
		self._pm = plugin_manager
		self._ctx = ctx
		self._messages = messages

	async def run(
		self,
		tool_calls: list[dict],
		emit_events: bool = True,
		event_sink: EventSink | None = None,
	) -> ToolFlowResult:
		parsed_calls = parse_tool_calls(tool_calls)
		parsed_calls = self.resolve_names(parsed_calls)
		events: list[Event] = []
		if emit_events:
			for call in parsed_calls:
				_add_event(events, Event.tool_call(call["name"], call["id"], call["arguments"]), event_sink)
		results = await execute_tool_calls(parsed_calls, self._registry, self._pm.plugins, self._ctx)
		self._messages.append_tool_results(results)
		if emit_events:
			for result in results:
				artifact_refs = [a.to_dict() for a in result.output.artifacts] if result.output else []
				display_content = result.output.display_view() if result.success and result.output else result.error
				context_content = result.output.context_view() if result.success and result.output else result.error
				event = Event.tool_result(
					result.tool_name,
					result.tool_call_id,
					display_content,
					artifact_refs,
					metadata={
						"context_view": context_content,
					},
				)
				event.metadata["duration_ms"] = result.duration_ms
				_add_event(events, event, event_sink)
		return ToolFlowResult(parsed_calls=parsed_calls, events=events)

	def resolve_names(self, calls: list[dict]) -> list[dict]:
		return [{**call, "name": self._registry.resolve_name(call.get("name", ""))} for call in calls]


class ReActTurnRunner:
	"""Run a single ReAct turn against a message store and execution context. 在指定历史和执行上下文中运行一轮 ReAct。"""

	def __init__(
		self,
		llm_caller: LLMCaller,
		registry: ToolRegistry,
		plugin_manager: PluginManager,
		ctx: ExecutionContext,
		messages: MessageStore,
	) -> None:
		self._llm = llm_caller
		self._registry = registry
		self._pm = plugin_manager
		self._ctx = ctx
		self._messages = messages
		self._tool_flow = ToolCallFlow(registry, plugin_manager, ctx, messages)

	async def run(
		self,
		user_message: str = "",
		stream_llm_call: StreamLLMCall | None = None,
		emit_tool_events: bool = True,
		event_filter: EventFilter | None = None,
		event_sink: EventSink | None = None,
	) -> AsyncIterator[Event | ReActTurnResult]:
		if stream_llm_call and self._ctx.runtime.inbox and (pending := await self._ctx.runtime.inbox.take()):
			user_message = "\n".join(pending)
		messages = self._pm.transform_messages(self._messages.get_all(), self._ctx, user_message)
		tools_schema = self._registry.get_openai_schemas() if self._registry.count > 0 else None
		if self._ctx.config.stream and stream_llm_call:
			message, events = None, None
			async with managed_stream(stream_llm_call(messages, tools_schema)) as stream:
				async for ev in stream:
					if isinstance(ev, tuple):
						message, events = ev
					elif event_filter is None or event_filter(ev):
						_emit_event(ev, event_sink)
						yield ev
			if message is None:
				raise ProviderError("LLM call returned no result")
		else:
			message, events = await self._llm.call(self._ctx, messages, tools_schema)
		if self._ctx.state.fallback_triggered:
			event = Event.state_change("Model switched to fallback", {"reason": self._ctx.state.fallback_reason})
			self._ctx.state.fallback_triggered = False
			self._ctx.state.fallback_reason = ""
			if event_filter is None or event_filter(event):
				_emit_event(event, event_sink)
				yield event
		for ev in events:
			if event_filter is None or event_filter(ev):
				_emit_event(ev, event_sink)
				yield ev
		tool_calls = message.get("tool_calls", [])
		for call in tool_calls:
			if not call.get("id"):
				call["id"] = uuid.uuid4().hex
		self._messages.append(message)
		content = message.get("content", "") or ""
		if not tool_calls:
			yield ReActTurnResult(message=message, content=content, user_message=user_message)
			return
		tool_flow_result: ToolFlowResult
		async with managed_stream(
			self._run_tool_flow(tool_calls, emit_tool_events, event_filter, event_sink)
		) as stream:
			async for item in stream:
				if isinstance(item, ToolFlowResult):
					tool_flow_result = item
				else:
					yield item
		yield ReActTurnResult(
			message=message,
			content=content,
			parsed_calls=tool_flow_result.parsed_calls,
			has_tool_calls=True,
			user_message=user_message,
		)

	async def _run_tool_flow(
		self,
		tool_calls: list[dict],
		emit_tool_events: bool,
		event_filter: EventFilter | None,
		event_sink: EventSink | None,
	) -> AsyncIterator[Event | ToolFlowResult]:
		queue: asyncio.Queue[Event | None] = asyncio.Queue()

		def live_sink(event: Event) -> None:
			_emit_event(event, event_sink)
			if event_filter is None or event_filter(event):
				queue.put_nowait(event)

		async def run_flow() -> ToolFlowResult:
			try:
				return await self._tool_flow.run(tool_calls, emit_events=emit_tool_events, event_sink=live_sink)
			finally:
				queue.put_nowait(None)

		previous_sink = self._ctx.runtime.event_sink
		self._ctx.runtime.event_sink = live_sink
		task = asyncio.create_task(run_flow())
		try:
			while True:
				event = await queue.get()
				if event is None:
					break
				yield event
			yield await task
		finally:
			self._ctx.runtime.event_sink = previous_sink
			await cancel_and_wait(task)


def por_visible_event(event: Event) -> bool:
	return event.type in (
		EventType.THINKING_START,
		EventType.THINKING_DELTA,
		EventType.THINKING_END,
		EventType.TOOL_ARGS_PREVIEW,
	)


def _add_event(events: list[Event], event: Event, event_sink: EventSink | None) -> None:
	events.append(event)
	_emit_event(event, event_sink)


def _emit_event(event: Event, event_sink: EventSink | None) -> None:
	if event_sink:
		event_sink(event)
