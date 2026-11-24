"""Black-box assertions for third-party strategy authors. 第三方循环可复用的黑盒契约测试。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, AsyncIterable, Iterable, Mapping
from typing import TYPE_CHECKING
from uuid import uuid4

from axc_agent_engine.core.events import TERMINAL_EVENTS, Event, EventType
from axc_agent_engine.observability.audit import AuditEvent, AuditEventType

if TYPE_CHECKING:
	from axc_agent_engine.agent import Agent
	from axc_agent_engine.engine import Engine


async def assert_stream_contract(
	events: AsyncIterable[Event], terminal: EventType = EventType.DONE, content: str | None = None
) -> Event:
	"""Check a finite run or resume stream and return its terminal event. 校验运行或恢复流的终止事件。"""
	last = None
	async for event in events:
		if not isinstance(event, Event):
			raise TypeError(f"Expected an Event, received {type(event).__name__}")
		if last is not None:
			raise AssertionError(f"Received {event.type} after terminal event {last.type}")
		if event.type in TERMINAL_EVENTS:
			last = event
	if last is None or last.type != terminal:
		raise AssertionError(f"Expected terminal {terminal}, received {last}")
	if content is not None and content != last.content:
		raise AssertionError(f"Expected {content!r}, received {last.content!r}")
	return last


async def assert_isolated_runs(agent: Agent, cases: Mapping[str, str]) -> None:
	"""Check concurrent input/output pairs in separate sessions. 验证并发会话各自得到预期输出。"""
	if len(cases) < 2:
		raise ValueError("Provide at least two distinct inputs")
	async with asyncio.TaskGroup() as group:
		for message, expected in cases.items():
			events = agent.stream(message, session_id=uuid4().hex)
			group.create_task(assert_stream_contract(events, content=expected))


async def assert_cancellable_run(
	engine: Engine, agent: Agent, message: str, ready: asyncio.Event, timeout: float = 5.0
) -> None:
	"""Cancel a run when the fixture signals that it is in flight. 测试夹具通知运行已就绪后取消。"""
	run_id = uuid4().hex
	events = agent.stream(message, metadata={"run_id": run_id})
	async with asyncio.timeout(timeout), asyncio.TaskGroup() as group:
		group.create_task(assert_stream_contract(events, EventType.CANCELLED))
		await ready.wait()
		if not engine.cancel_run(run_id):
			raise AssertionError("Strategy completed before the cancellation point")
	if engine.cancel_run(run_id):
		raise AssertionError("Cancelled run is still registered")


async def assert_stream_closes(
	stream: AsyncGenerator[Event, None], released: asyncio.Event, timeout: float = 5.0
) -> None:
	"""Close after the first nonterminal event and check the loop released its resources. 校验提前关闭后的资源释放。"""
	async with asyncio.timeout(timeout):
		try:
			event = await anext(stream)
			if event.type in TERMINAL_EVENTS:
				raise AssertionError("Expected a nonterminal event before closing the stream")
		finally:
			await stream.aclose()
	if not released.is_set():
		raise AssertionError("Loop resources are still active after aclose()")


def assert_tool_denied(
	events: Iterable[AuditEvent], tool_call_id: str, code: str = "policy.capability_not_allowed"
) -> None:
	"""Verify a specific call was rejected before execution. 校验指定调用在执行前被拒绝。"""
	calls = [event for event in events if event.tool_call_id == tool_call_id]
	if (
		len(calls) != 1
		or calls[0].type != AuditEventType.TOOL_CALL_REJECTED
		or calls[0].allowed
		or calls[0].error.get("code") != code
	):
		raise AssertionError(f"Expected one rejection for {tool_call_id!r} with code {code!r}, received {calls!r}")
