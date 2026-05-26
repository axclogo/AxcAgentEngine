"""Tests for #3 MessageStore snapshot/rollback."""
from axc_agent_engine.core.message_store import MessageStore


class TestMessageStoreSnapshot:
	def test_snapshot_returns_count(self):
		ms = MessageStore()
		ms.append({"role": "user", "content": "hello"})
		ms.append({"role": "assistant", "content": "hi"})
		assert ms.snapshot() == 2

	def test_snapshot_empty_store(self):
		ms = MessageStore()
		assert ms.snapshot() == 0

	def test_rollback_to_zero(self):
		ms = MessageStore()
		ms.append({"role": "user", "content": "a"})
		ms.append({"role": "user", "content": "b"})
		ms.rollback(0)
		assert ms.count == 0
		assert ms.get_all() == []

	def test_rollback_to_snapshot(self):
		ms = MessageStore()
		ms.append({"role": "system", "content": "sys"})
		ms.append({"role": "user", "content": "q1"})
		snap = ms.snapshot()
		ms.append({"role": "assistant", "content": "a1"})
		ms.append({"role": "tool", "content": "result"})
		assert ms.count == 4
		ms.rollback(snap)
		assert ms.count == 2
		assert ms.get_all()[-1]["content"] == "q1"

	def test_rollback_preserves_earlier_messages(self):
		ms = MessageStore()
		ms.append({"role": "system", "content": "prompt"})
		ms.append({"role": "user", "content": "hello"})
		snap = ms.snapshot()
		ms.append({"role": "assistant", "content": "world"})
		ms.rollback(snap)
		msgs = ms.get_all()
		assert len(msgs) == 2
		assert msgs[0]["content"] == "prompt"
		assert msgs[1]["content"] == "hello"

	def test_multiple_snapshots(self):
		ms = MessageStore()
		ms.append({"role": "user", "content": "1"})
		snap1 = ms.snapshot()
		ms.append({"role": "user", "content": "2"})
		snap2 = ms.snapshot()
		ms.append({"role": "user", "content": "3"})
		ms.rollback(snap2)
		assert ms.count == 2
		ms.rollback(snap1)
		assert ms.count == 1

	def test_rollback_then_append(self):
		ms = MessageStore()
		ms.append({"role": "user", "content": "a"})
		snap = ms.snapshot()
		ms.append({"role": "user", "content": "b"})
		ms.rollback(snap)
		ms.append({"role": "user", "content": "c"})
		assert ms.count == 2
		assert ms.get_all()[-1]["content"] == "c"

	def test_snapshot_after_rollback(self):
		ms = MessageStore()
		ms.append({"role": "user", "content": "a"})
		ms.append({"role": "user", "content": "b"})
		ms.rollback(1)
		snap = ms.snapshot()
		assert snap == 1

	def test_rollback_to_same_point_noop(self):
		ms = MessageStore()
		ms.append({"role": "user", "content": "a"})
		snap = ms.snapshot()
		ms.rollback(snap)
		assert ms.count == 1

	def test_error_message_format(self):
		"""Verify tool error messages use English format."""
		ms = MessageStore()
		from axc_agent_engine.tools.executor import ToolResult
		results = [ToolResult(tool_call_id="t1", tool_name="test", arguments={}, error="fail", success=False)]
		ms.append_tool_results(results)
		assert "[Error]" in ms.get_all()[0]["content"]

	def test_get_all_returns_deep_copy(self):
		ms = MessageStore()
		ms.append({"role": "user", "content": [{"type": "text", "text": "hello"}]})

		messages = ms.get_all()
		messages[0]["content"][0]["text"] = "mutated"

		assert ms.get_all()[0]["content"][0]["text"] == "hello"

	def test_append_defensively_copies_input_message(self):
		ms = MessageStore()
		message = {"role": "user", "content": {"text": "hello"}}

		ms.append(message)
		message["content"]["text"] = "mutated"

		assert ms.get_all()[0]["content"]["text"] == "hello"
