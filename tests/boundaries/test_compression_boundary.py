import pytest

from axc_agent_engine.core.context import ExecutionConfig, ExecutionContext
from axc_agent_engine.plugins import PluginContext
from axc_agent_engine.plugins.builtin.compress.plugin import CompressPlugin
from axc_agent_engine.plugins.builtin.compress.context.boundary import (
	CompressionBoundary,
	InMemoryCompressionBoundaryStore,
	KVCompressionBoundaryStore,
)
from axc_agent_engine.tools.tool_output import ToolOutput


@pytest.mark.asyncio
async def test_compress_plugin_persists_and_restores_boundary():
	store = InMemoryCompressionBoundaryStore()
	ctx = PluginContext(resources={"boundary": store})
	plugin = CompressPlugin()
	plugin.initialize({
		"summary": {"after_rounds": 1},
		"boundary": {"resource": "boundary"},
	}, ctx)
	exec_ctx = ExecutionContext(config=ExecutionConfig())
	exec_ctx.state.metadata["agent_name"] = "agent"
	exec_ctx.state.metadata["session_id"] = "s1"
	await plugin.on_execution_start(exec_ctx)
	await plugin.on_round_end(exec_ctx, "hello", "world", [])
	boundary = await store.load("agent", "s1")
	assert boundary is not None
	assert boundary.round_count == 1

	restored = CompressPlugin()
	restored.initialize({"boundary": {"resource": "boundary"}}, ctx)
	await restored.on_execution_start(exec_ctx)
	assert restored._round_count == 1


@pytest.mark.asyncio
async def test_compress_plugin_restores_file_cache_after_summary():
	class LLM:
		async def ask(self, prompt):
			return "summary"

	store = InMemoryCompressionBoundaryStore()
	ctx = PluginContext(utility_model=LLM(), resources={"boundary": store})
	plugin = CompressPlugin()
	plugin.initialize({
		"summary": {"after_rounds": 1},
		"boundary": {"resource": "boundary"},
	}, ctx)
	exec_ctx = ExecutionContext(config=ExecutionConfig())
	exec_ctx.state.metadata["agent_name"] = "agent"
	exec_ctx.state.metadata["session_id"] = "s1"

	await plugin.on_execution_start(exec_ctx)
	await plugin.post_tool_call(
		exec_ctx,
		"file_read",
		{"path": "src/app.py"},
		ToolOutput.json_output({
			"path": "src/app.py",
			"text": "print('cached')",
			"start_line": 1,
			"end_line": 1,
			"total_lines": 1,
		}),
		8,
	)
	await plugin.on_round_end(exec_ctx, "read file", "done", [])

	restored = CompressPlugin()
	restored.initialize({"boundary": {"resource": "boundary"}}, ctx)
	await restored.on_execution_start(exec_ctx)
	messages = restored.transform_messages([{"role": "user", "content": "continue"}], exec_ctx)
	assert any("[恢复的文件缓存]" in m.get("content", "") for m in messages)
	assert any("print('cached')" in m.get("content", "") for m in messages)


@pytest.mark.asyncio
async def test_compress_plugin_generates_and_persists_tool_summary():
	class LLM:
		async def ask(self, prompt):
			assert "file_read" in prompt
			return "Read src/app.py and found cached content."

	store = InMemoryCompressionBoundaryStore()
	ctx = PluginContext(utility_model=LLM(), resources={"boundary": store})
	plugin = CompressPlugin()
	plugin.initialize({
		"tool_summary": {"enabled": True},
		"boundary": {"resource": "boundary"},
	}, ctx)
	exec_ctx = ExecutionContext(config=ExecutionConfig())
	exec_ctx.state.metadata["agent_name"] = "agent"
	exec_ctx.state.metadata["session_id"] = "s1"

	await plugin.on_execution_start(exec_ctx)
	await plugin.post_tool_call(exec_ctx, "file_read", {"path": "src/app.py"}, ToolOutput.text("cached content"), 12)
	await plugin.on_round_end(exec_ctx, "read", "done", [{"name": "file_read"}])

	boundary = await store.load("agent", "s1")
	assert boundary is not None
	assert boundary.tool_summaries == ["Read src/app.py and found cached content."]

	restored = CompressPlugin()
	restored.initialize({"boundary": {"resource": "boundary"}}, ctx)
	await restored.on_execution_start(exec_ctx)
	messages = restored.transform_messages([{"role": "user", "content": "continue"}], exec_ctx)
	assert any("[工具摘要]" in m.get("content", "") for m in messages)


@pytest.mark.asyncio
async def test_boundary_roundtrip_filters_invalid_values_and_store_clones():
	boundary = CompressionBoundary.from_dict({
		"agent_name": "a",
		"session_id": "s",
		"summary": None,
		"round_count": "2",
		"compressed_rounds": "1",
		"last_message_index": "9",
		"buffer": ["x", ""],
		"file_cache": [{"path": "p"}, "bad"],
		"tool_summaries": ["t", ""],
		"durable_results": ["d", ""],
		"updated_at": "3.5",
	})
	assert boundary.round_count == 2
	assert boundary.file_cache == [{"path": "p"}]
	assert boundary.tool_summaries == ["t"]

	store = InMemoryCompressionBoundaryStore()
	await store.save(boundary)
	loaded = await store.load("a", "s")
	loaded.buffer.append("mutated")
	assert (await store.load("a", "s")).buffer == ["x"]
	await store.delete("a", "s")
	assert await store.load("a", "s") is None


@pytest.mark.asyncio
async def test_kv_boundary_store_noops_and_keyed_roundtrip():
	class KV:
		def __init__(self):
			self.data = {}
			self.deleted = []

		async def get(self, key):
			return self.data.get(key)

		async def set(self, key, value):
			self.data[key] = value

		async def delete(self, key):
			self.deleted.append(key)
			self.data.pop(key, None)

	assert await KVCompressionBoundaryStore(None).load("a", "s") is None
	store = KVCompressionBoundaryStore(KV(), prefix="p:")
	assert await store.load("a", "") is None
	await store.save(CompressionBoundary(agent_name="a", session_id=""))
	assert store.kv_store.data == {}
	await store.save(CompressionBoundary(agent_name="a", session_id="s", summary="sum"))
	assert await store.load("a", "s")
	await store.delete("a", "s")
	assert store.kv_store.deleted == ["p:a:s"]
