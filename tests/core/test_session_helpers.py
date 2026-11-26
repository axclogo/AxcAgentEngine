"""Coverage helpers for session rewind primitives. 会话回退基础组件测试。"""

from __future__ import annotations

import pytest

from axc_agent_engine.core.message_store import MessageStore
from axc_agent_engine.core.run_context import checkpoint_owner_conflict
from axc_agent_engine.core.session_manager import SessionManager


def test_validate_history_rejects_non_message_items() -> None:
	with pytest.raises(ValueError, match="Invalid checkpoint message history"):
		MessageStore.validate_history([1])


@pytest.mark.parametrize(
	"history",
	[
		[{"role": "assistant", "tool_calls": [{}]}],
		[{"role": "assistant", "tool_calls": {}}],
		[{"role": "assistant", "tool_calls": False}],
		[{"role": "assistant", "tool_calls": 0}],
		[{"role": "tool", "tool_call_id": []}],
		[{"role": "tool", "tool_call_id": {}}],
	],
)
def test_validate_history_rejects_malformed_tool_fields(history: list[dict]) -> None:
	with pytest.raises(ValueError, match="Invalid checkpoint"):
		MessageStore.validate_history(history)


def test_validate_history_stops_at_unanswered_tool_call() -> None:
	with pytest.raises(ValueError, match="Missing tool results"):
		MessageStore.validate_history(
			[
				{"role": "assistant", "tool_calls": [{"id": "call-1"}]},
				{"role": "assistant", "content": "next"},
			]
		)


def test_set_at_replaces_existing_message() -> None:
	store = MessageStore()
	store.extend([{"role": "user", "content": "old"}])
	store.set_at(0, {"role": "user", "content": "new"})
	assert store.get_all() == [{"role": "user", "content": "new"}]
	store.set_at(5, {"role": "user", "content": "ignored"})
	assert store.get_all() == [{"role": "user", "content": "new"}]


async def test_replace_history_validates_arguments() -> None:
	manager = SessionManager("scope")
	with pytest.raises(ValueError, match="session_id"):
		await manager.replace_history("", [])
	with pytest.raises(ValueError, match="session history"):
		await manager.replace_history("session", ["bad"])


async def test_replace_history_evicts_old_session() -> None:
	manager = SessionManager("scope", max_sessions=1)
	await manager.replace_history("first", [{"role": "user", "content": "a"}])
	await manager.replace_history("second", [{"role": "user", "content": "b"}])
	assert await manager.get("first") is None
	assert (await manager.get("second")).messages == [{"role": "user", "content": "b"}]


def test_checkpoint_owner_conflict_rejects_non_mapping_owner() -> None:
	with pytest.raises(TypeError, match="owner"):
		checkpoint_owner_conflict([], {})
