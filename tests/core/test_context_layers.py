"""Tests for context management layers."""
import pytest

from axc_agent_engine.plugins.builtin.compress.context.normalizer import normalize_messages, public_message
from axc_agent_engine.plugins.builtin.compress.context.packer import pack_context
from axc_agent_engine.plugins.builtin.compress.context.recall import (
	fallback_recall,
	read_recall_resource,
	write_recall_resource,
)
from axc_agent_engine.plugins.builtin.compress.context.recent_window import select_recent_window
from axc_agent_engine.plugins.builtin.compress.context.scoring import keyword_score
from axc_agent_engine.plugins.builtin.compress.context.summarizer import SessionSummarizer, summary_message
from axc_agent_engine.plugins.builtin.compress.context.tool_result import (
	TOOL_COMPACT_MARKER,
	compact_tool_messages,
	externalize_large_tool_output,
)
from axc_agent_engine.storage.result_store import InMemoryResultStore
from axc_agent_engine.tools.tool_output import ToolOutput


class TestNormalizeMessages:
	def test_removes_empty_text_message(self):
		result = normalize_messages([{"role": "user", "content": "   "}])
		assert result == []

	def test_keeps_assistant_tool_call_without_content(self):
		result = normalize_messages([{"role": "assistant", "tool_calls": [{"id": "x"}]}])
		assert len(result) == 1
		assert result[0]["role"] == "assistant"

	def test_removes_tool_result_without_call_id(self):
		result = normalize_messages([{"role": "tool", "content": "orphan"}])
		assert result == []

	def test_fills_metadata(self):
		result = normalize_messages([{"role": "user", "content": "hello world"}])
		assert result[0]["round"] == 1
		assert result[0]["token_estimate"] >= 1
		assert result[0]["created_at"] == 0

	def test_public_message_strips_internal_metadata(self):
		message = {"role": "user", "content": "hi", "round": 1, "token_estimate": 1}
		assert public_message(message) == {"role": "user", "content": "hi"}


class TestToolResultManagement:
	def test_keeps_small_tool_message(self):
		messages = [{"role": "tool", "tool_call_id": "x", "content": "small"}]
		assert compact_tool_messages(messages, max_inline_tokens=100) == messages

	def test_compacts_large_tool_message(self):
		messages = [{"role": "tool", "tool_call_id": "x", "content": "x" * 1000}]
		result = compact_tool_messages(messages, max_inline_tokens=20)
		assert TOOL_COMPACT_MARKER in result[0]["content"]
		assert len(result[0]["content"]) < 1000

	@pytest.mark.asyncio
	async def test_externalizes_large_tool_output(self):
		store = InMemoryResultStore()
		output = ToolOutput.text("x" * 2000)
		result = await externalize_large_tool_output(output, store, artifact_threshold_tokens=20)
		assert result.artifacts
		assert await store.get(result.artifacts[0].id, limit=10) == "x" * 10

	@pytest.mark.asyncio
	async def test_externalize_noops_without_store(self):
		output = ToolOutput.text("x" * 2000)
		result = await externalize_large_tool_output(output, None, artifact_threshold_tokens=20)
		assert result is output


class TestRecentWindow:
	def test_keeps_system_and_recent_rounds(self):
		messages = normalize_messages([
			{"role": "system", "content": "sys"},
			{"role": "user", "content": "old"},
			{"role": "assistant", "content": "old reply"},
			{"role": "user", "content": "new"},
			{"role": "assistant", "content": "new reply"},
		])
		result = select_recent_window(messages, rounds=1)
		assert [m["content"] for m in result] == ["sys", "new", "new reply"]

	def test_keeps_tool_call_pair(self):
		messages = normalize_messages([
			{"role": "user", "content": "q1"},
			{"role": "assistant", "content": "", "tool_calls": [{"id": "call-1"}]},
			{"role": "tool", "tool_call_id": "call-1", "content": "tool result"},
			{"role": "user", "content": "q2"},
		])
		result = select_recent_window(messages, rounds=1)
		assert any(m.get("tool_call_id") == "call-1" for m in result) is False
		result = select_recent_window(messages, rounds=2)
		assert any(m.get("tool_call_id") == "call-1" for m in result)


class TestPacker:
	def test_strips_internal_metadata(self):
		messages = normalize_messages([{"role": "user", "content": "hi"}])
		result = pack_context(messages, max_input_tokens=100, reserve_output_tokens=10)
		assert result.messages == [{"role": "user", "content": "hi"}]

	def test_always_keeps_last_user(self):
		messages = normalize_messages([
			{"role": "user", "content": "old" * 1000},
			{"role": "user", "content": "current"},
		])
		result = pack_context(messages, max_input_tokens=20, reserve_output_tokens=5)
		assert result.messages[-1]["content"] == "current"
		assert result.truncated is True


class TestRecall:
	def test_keyword_score(self):
		assert keyword_score("python tests", "python code") == 0.5

	def test_fallback_recall_returns_relevant_items(self):
		messages = normalize_messages([
			{"role": "user", "content": "python testing details"},
			{"role": "assistant", "content": "unrelated"},
		])
		result = fallback_recall(messages, "python", top_k=3, token_limit=100)
		assert result[0].text == "python testing details"

	def test_read_sync_recall_resource(self):
		class Resource:
			def search(self, query, top_k=1):
				return [{"text": query, "score": 0.9}]

		result = read_recall_resource(Resource(), "hello", top_k=1)
		assert result[0].text == "hello"

	def test_read_async_recall_resource(self):
		class Resource:
			async def search(self, query, top_k=1):
				return [{"text": query, "score": 0.9}]

		result = read_recall_resource(Resource(), "async hello", top_k=1)
		assert result[0].text == "async hello"

	@pytest.mark.asyncio
	async def test_read_async_recall_resource_inside_running_loop(self):
		class Resource:
			async def search(self, query, top_k=1):
				return [{"text": query, "score": 0.9}]

		result = read_recall_resource(Resource(), "loop hello", top_k=1)
		assert result[0].text == "loop hello"

	@pytest.mark.asyncio
	async def test_write_recall_resource(self):
		class Resource:
			def __init__(self):
				self.written = []

			async def add_texts(self, texts, metadata):
				self.written.append((texts, metadata))

		resource = Resource()
		await write_recall_resource(resource, ["text"], [{"round": 1}])
		assert resource.written == [(["text"], [{"round": 1}])]


class TestSummarizer:
	@pytest.mark.asyncio
	async def test_generates_summary(self, mock_llm):
		summarizer = SessionSummarizer(max_tokens=50)
		result = await summarizer.generate(mock_llm, ["user: hi"])
		assert result == "test response"

	@pytest.mark.asyncio
	async def test_breaks_after_failures(self):
		class LLM:
			async def ask(self, prompt):
				raise RuntimeError("down")

		summarizer = SessionSummarizer(max_failures=2)
		await summarizer.generate(LLM(), ["a"])
		await summarizer.generate(LLM(), ["b"])
		assert summarizer.state.broken is True

	def test_summary_message(self):
		assert summary_message("done") == {"role": "system", "content": "[会话历史摘要]\ndone"}
