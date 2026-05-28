import pytest

from axc_agent_engine.core.context import ExecutionConfig, ExecutionContext
from axc_agent_engine.plugins import PluginContext
from axc_agent_engine.plugins.builtin.compress.plugin import CompressPlugin
from axc_agent_engine.plugins.builtin.compress.context.boundary import InMemoryCompressionBoundaryStore
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
			"truncated": False,
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
