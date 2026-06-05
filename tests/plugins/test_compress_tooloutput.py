"""Tests for CompressPlugin with ToolOutput (no longer truncates tool results)."""
import pytest
from axc_agent_engine.plugins.builtin.compress.plugin import CompressPlugin
from axc_agent_engine.core.context import ExecutionContext
from axc_agent_engine.tools.tool_output import ToolOutput
from axc_agent_engine.plugins import PluginContext
from axc_agent_engine.runtime.resources import ResourceRegistry
from axc_agent_engine.storage.result_store import InMemoryResultStore
from unittest.mock import AsyncMock


class TestCompressPluginPostToolCall:
	@pytest.mark.asyncio
	async def test_noop_returns_same_output(self):
		p = CompressPlugin()
		p.initialize({}, PluginContext())
		ctx = ExecutionContext()
		output = ToolOutput.text("some result")
		result = await p.post_tool_call(ctx, "any_tool", {}, output, 100)
		assert result is output

	@pytest.mark.asyncio
	async def test_noop_for_large_output(self):
		"""CompressPlugin does NOT truncate large ToolOutput."""
		p = CompressPlugin()
		p.initialize({}, PluginContext())
		ctx = ExecutionContext()
		output = ToolOutput.text("x" * 10000)
		result = await p.post_tool_call(ctx, "file_read", {}, output, 50)
		assert result is output
		assert result.content == "x" * 10000

	@pytest.mark.asyncio
	async def test_noop_for_json_output(self):
		p = CompressPlugin()
		p.initialize({}, PluginContext())
		ctx = ExecutionContext()
		output = ToolOutput.json_output({"big": "data" * 1000})
		result = await p.post_tool_call(ctx, "http_request", {}, output, 30)
		assert result is output

	@pytest.mark.asyncio
	async def test_externalizes_large_output_with_result_store(self):
		p = CompressPlugin()
		p.initialize({"tool_result": {"artifact_threshold_tokens": 10}}, PluginContext())
		store = InMemoryResultStore()
		ctx = ExecutionContext()
		ctx.services.result_store = store
		result = await p.post_tool_call(ctx, "file_read", {}, ToolOutput.text("x" * 1000), 30)
		assert result.artifacts
		assert await store.get(result.artifacts[0].id, limit=5) == "xxxxx"

	@pytest.mark.asyncio
	async def test_externalized_large_output_keeps_summary_and_artifact_refs_for_context(self):
		p = CompressPlugin()
		p.initialize({"tool_result": {"artifact_threshold_tokens": 10}}, PluginContext())
		store = InMemoryResultStore()
		ctx = ExecutionContext()
		ctx.services.result_store = store
		output = ToolOutput.text("raw payload " * 1000, summary="query succeeded with 42 rows")

		result = await p.post_tool_call(ctx, "business_query", {}, output, 30)

		assert result.artifacts
		assert result.context_view().startswith("query succeeded with 42 rows")
		assert result.artifacts[0].id in result.context_view()
		assert result.metadata["externalized"] is True
		assert await store.get(result.artifacts[0].id, limit=11) == "raw payload"

	@pytest.mark.asyncio
	async def test_records_durable_tool_result_for_boundary_and_context(self):
		p = CompressPlugin()
		p.initialize({"durable_tools": {"names": ["external_query"]}}, PluginContext())
		ctx = ExecutionContext()
		output = ToolOutput.text(
			"full result",
			summary="short",
		).with_metadata({"durable_summary": "durable analysis report"})

		await p.post_tool_call(ctx, "external_query", {}, output, 100)
		result = p.transform_messages([{"role": "user", "content": "next"}], ctx, "next")

		assert any("durable analysis report" in item for item in p._conversation_buffer)
		assert any("durable analysis report" in msg.get("content", "") for msg in result)


class TestCompressPluginTransformMessages:
	def test_snip_compact_still_works(self):
		"""L1 snip still truncates oversized tool messages in context."""
		p = CompressPlugin()
		p.initialize({"snip_threshold": 100}, PluginContext())
		ctx = ExecutionContext()
		messages = [
			{"role": "system", "content": "sys"},
			{"role": "user", "content": "hi"},
			{"role": "tool", "tool_call_id": "1", "content": "x" * 2000},
		]
		result = p.transform_messages(messages, ctx)
		tool_msg = [m for m in result if m["role"] == "tool"][0]
		assert len(tool_msg["content"]) < 2000

	def test_micro_compact_still_works(self):
		"""L2 keeps recent rounds and drops old tool messages from the active window."""
		p = CompressPlugin()
		p.initialize({"micro_compact_keep_recent": 2}, PluginContext())
		ctx = ExecutionContext()
		messages = [{"role": "system", "content": "sys"}]
		# Add 5 rounds of user+assistant+tool
		for i in range(5):
			messages.append({"role": "user", "content": f"q{i}"})
			messages.append({"role": "assistant", "content": f"a{i}", "tool_calls": [{"id": f"tc{i}", "function": {"name": "t", "arguments": "{}"}}]})
			messages.append({"role": "tool", "tool_call_id": f"tc{i}", "content": "x" * 500})
		result = p.transform_messages(messages, ctx)
		users = [m["content"] for m in result if m["role"] == "user"]
		assert users == ["q3", "q4"]

	def test_pinned_tool_result_survives_small_context_budget(self):
		p = CompressPlugin()
		p.initialize({
			"context_window": {"max_input_tokens": 120, "reserve_output_tokens": 20},
			"recent_window": {"rounds": 1},
			"tool_result": {"max_inline_tokens": 1000},
		}, PluginContext())
		ctx = ExecutionContext()
		messages = [{"role": "system", "content": "sys"}]
		for i in range(10):
			messages.append({"role": "user", "content": f"q{i}"})
			messages.append({
				"role": "assistant",
				"content": "",
				"tool_calls": [{"id": f"tc{i}", "function": {"name": "agent_call", "arguments": "{}"}}],
			})
			metadata = {"durable": True, "durable_summary": "关键阶段报告"} if i == 0 else {}
			messages.append({"role": "tool", "tool_call_id": f"tc{i}", "content": f"result{i}", "metadata": metadata})

		result = p.transform_messages(messages, ctx)

		assert any(msg.get("role") == "tool" and "result0" in msg.get("content", "") for msg in result)

	def test_empty_messages(self):
		p = CompressPlugin()
		p.initialize({}, PluginContext())
		ctx = ExecutionContext()
		assert p.transform_messages([], ctx) == []

	def test_no_tool_messages(self):
		p = CompressPlugin()
		p.initialize({}, PluginContext())
		ctx = ExecutionContext()
		messages = [
			{"role": "system", "content": "sys"},
			{"role": "user", "content": "hi"},
			{"role": "assistant", "content": "hello"},
		]
		result = p.transform_messages(messages, ctx)
		assert result == messages


class TestCompressPluginSummary:
	@pytest.mark.asyncio
	async def test_summary_generation(self):
		mock_llm = AsyncMock()
		mock_llm.ask = AsyncMock(return_value="This is a summary")
		plugin_ctx = PluginContext(utility_model=mock_llm)
		p = CompressPlugin()
		p.initialize({"summary_after_rounds": 2, "summary_keep_recent": 1}, plugin_ctx)
		ctx = ExecutionContext()
		# Simulate rounds
		await p.on_round_end(ctx, "user msg 1", "assistant msg 1", [])
		await p.on_round_end(ctx, "user msg 2", "assistant msg 2", [])
		assert p._summary == "This is a summary"

	@pytest.mark.asyncio
	async def test_summary_applied_to_messages(self):
		p = CompressPlugin()
		p.initialize({"summary_keep_recent": 1}, PluginContext())
		p._summary = "Previous conversation summary"
		ctx = ExecutionContext()
		messages = [
			{"role": "system", "content": "sys"},
			{"role": "user", "content": "old"},
			{"role": "assistant", "content": "old reply"},
			{"role": "user", "content": "new"},
			{"role": "assistant", "content": "new reply"},
		]
		result = p.transform_messages(messages, ctx)
		# 应该注入摘要
		summary_msgs = [m for m in result if "摘要" in m.get("content", "")]
		assert len(summary_msgs) == 1

	@pytest.mark.asyncio
	async def test_no_summary_without_utility_model(self):
		p = CompressPlugin()
		p.initialize({"summary_after_rounds": 1}, PluginContext())
		ctx = ExecutionContext()
		await p.on_round_end(ctx, "msg", "reply", [])
		assert p._summary == ""

	@pytest.mark.asyncio
	async def test_circuit_breaker(self):
		mock_llm = AsyncMock()
		mock_llm.ask = AsyncMock(side_effect=RuntimeError("LLM down"))
		plugin_ctx = PluginContext(utility_model=mock_llm)
		p = CompressPlugin()
		p.initialize({"summary_after_rounds": 1, "max_compact_failures": 2}, plugin_ctx)
		ctx = ExecutionContext()
		await p.on_round_end(ctx, "msg1", "reply1", [])
		assert p._compact_failures == 1
		# Reset round count to trigger again
		p._round_count = 0
		p._summary = ""
		await p.on_round_end(ctx, "msg2", "reply2", [])
		assert p._compact_broken is True


class TestCompressPluginRecall:
	@pytest.mark.asyncio
	async def test_writes_round_to_recall_resource(self):
		class RecallResource:
			def __init__(self):
				self.writes = []

			async def add_texts(self, texts, metadata):
				self.writes.append((texts, metadata))

		resource = RecallResource()
		ctx = PluginContext(resources=ResourceRegistry({"context_recall": resource}))
		p = CompressPlugin()
		p.initialize({"recall": {"resource": "context_recall"}}, ctx)
		await p.on_round_end(ExecutionContext(), "hello", "world", [])
		assert resource.writes[0][0] == ["hello", "world"]

	def test_reads_sync_recall_resource(self):
		class RecallResource:
			def search(self, query, top_k=12):
				return [{"text": f"found {query}", "score": 1.0}]

		ctx = PluginContext(resources=ResourceRegistry({"context_recall": RecallResource()}))
		p = CompressPlugin()
		p.initialize({"recall": {"resource": "context_recall"}}, ctx)
		result = p.transform_messages([{"role": "user", "content": "python"}], ExecutionContext(), "python")
		assert any("found python" in m.get("content", "") for m in result)

	def test_reads_async_recall_resource(self):
		class RecallResource:
			async def search(self, query, top_k=12):
				return [{"text": f"async found {query}", "score": 1.0}]

		ctx = PluginContext(resources=ResourceRegistry({"context_recall": RecallResource()}))
		p = CompressPlugin()
		p.initialize({"recall": {"resource": "context_recall"}}, ctx)
		result = p.transform_messages([{"role": "user", "content": "python"}], ExecutionContext(), "python")
		assert any("async found python" in m.get("content", "") for m in result)


class TestSafetyPluginWithToolOutput:
	@pytest.mark.asyncio
	async def test_pii_masking_on_tooloutput_text(self):
		from axc_agent_engine.plugins.builtin.safety.plugin import SafetyPlugin
		p = SafetyPlugin()
		p.initialize({"pii_masking": True}, None)
		output = ToolOutput.text("Contact: 13912345678")
		result = await p.post_tool_call(None, "t", {}, output, 0)
		assert "12345678" not in result.content
		assert "139" in result.content

	@pytest.mark.asyncio
	async def test_pii_masking_on_summary(self):
		from axc_agent_engine.plugins.builtin.safety.plugin import SafetyPlugin
		p = SafetyPlugin()
		p.initialize({"pii_masking": True}, None)
		output = ToolOutput(content="data", content_type="text", summary="Phone: 13812345678")
		result = await p.post_tool_call(None, "t", {}, output, 0)
		assert "12345678" not in result.summary

	@pytest.mark.asyncio
	async def test_no_masking_when_disabled(self):
		from axc_agent_engine.plugins.builtin.safety.plugin import SafetyPlugin
		p = SafetyPlugin()
		p.initialize({"pii_masking": False}, None)
		output = ToolOutput.text("13912345678")
		result = await p.post_tool_call(None, "t", {}, output, 0)
		assert result.content == "13912345678"

	@pytest.mark.asyncio
	async def test_json_content_not_masked(self):
		"""PII masking only applies to str content."""
		from axc_agent_engine.plugins.builtin.safety.plugin import SafetyPlugin
		p = SafetyPlugin()
		p.initialize({"pii_masking": True}, None)
		output = ToolOutput.json_output({"phone": "13912345678"})
		result = await p.post_tool_call(None, "t", {}, output, 0)
		# JSON content is dict, not str, so masking doesn't apply
		assert result.content["phone"] == "13912345678"
