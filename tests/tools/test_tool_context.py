"""Tests for #1 ToolContext standardization."""
import asyncio
from axc_agent_engine.tools.context import ToolContext
from axc_agent_engine.core.context import ExecutionContext, ExecutionConfig, ExecutionState


class TestToolContext:
	def test_default_values(self):
		ctx = ToolContext()
		assert ctx.workspace == ""
		assert ctx.exec_ctx is None
		assert ctx.session_id == ""
		assert ctx.agent_name == ""
		assert ctx.tool_name == ""
		assert ctx.tool_call_id == ""
		assert ctx.request_queue is None
		assert ctx.response_queue is None

	def test_with_workspace(self):
		ctx = ToolContext(workspace="/tmp/test")
		assert ctx.workspace == "/tmp/test"

	def test_with_exec_ctx(self):
		exec_ctx = ExecutionContext()
		ctx = ToolContext(exec_ctx=exec_ctx)
		assert ctx.exec_ctx is exec_ctx

	def test_with_session_id(self):
		ctx = ToolContext(session_id="sess-123")
		assert ctx.session_id == "sess-123"

	def test_with_agent_name(self):
		ctx = ToolContext(agent_name="my-agent")
		assert ctx.agent_name == "my-agent"

	def test_with_queues(self):
		req_q = asyncio.Queue()
		resp_q = asyncio.Queue()
		ctx = ToolContext(request_queue=req_q, response_queue=resp_q)
		assert ctx.request_queue is req_q
		assert ctx.response_queue is resp_q

	def test_to_dict(self):
		exec_ctx = ExecutionContext()
		ctx = ToolContext(workspace="/w", exec_ctx=exec_ctx, session_id="s1", agent_name="a1")
		d = ctx.to_dict()
		assert d["workspace"] == "/w"
		assert d["exec_ctx"] is exec_ctx
		assert d["session_id"] == "s1"
		assert d["agent_name"] == "a1"
		assert d["tool_name"] == ""
		assert d["tool_call_id"] == ""
		assert d["request_queue"] is None
		assert d["response_queue"] is None

	def test_to_dict_keys(self):
		ctx = ToolContext()
		d = ctx.to_dict()
		expected_keys = {
			"workspace", "exec_ctx", "session_id", "agent_name", "tool_name", "tool_call_id",
			"request_queue", "response_queue", "artifact_store", "command_executor",
		}
		assert set(d.keys()) == expected_keys

	def test_full_construction(self):
		req_q = asyncio.Queue()
		resp_q = asyncio.Queue()
		exec_ctx = ExecutionContext(
			config=ExecutionConfig(workspace="/project"),
			state=ExecutionState(),
		)
		ctx = ToolContext(
			workspace="/project", exec_ctx=exec_ctx,
			session_id="sess-abc", agent_name="worker", tool_name="search", tool_call_id="tc-1",
			request_queue=req_q, response_queue=resp_q,
		)
		assert ctx.workspace == "/project"
		assert ctx.exec_ctx.config.workspace == "/project"
		assert ctx.session_id == "sess-abc"
		assert ctx.agent_name == "worker"
		assert ctx.tool_name == "search"
		assert ctx.tool_call_id == "tc-1"
