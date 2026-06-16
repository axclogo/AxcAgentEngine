"""Tests for MessageStore with ToolOutput context_view integration."""
from axc_agent_engine.core.message_store import MessageStore
from axc_agent_engine.tools.executor import ToolResult
from axc_agent_engine.tools.tool_output import ToolOutput, ArtifactRef


class TestMessageStoreToolOutput:
	def test_append_success_uses_context_view(self):
		ms = MessageStore()
		output = ToolOutput.text("hello world")
		results = [ToolResult(tool_call_id="1", tool_name="t", arguments={}, output=output, success=True)]
		ms.append_tool_results(results)
		assert ms.count == 1
		assert ms.get_all()[0]["content"] == "hello world"

	def test_append_uses_context_view_not_display_view(self):
		ms = MessageStore()
		output = ToolOutput.text("full result", llm_view="context view")
		results = [ToolResult(tool_call_id="1", tool_name="t", arguments={}, output=output, success=True)]
		ms.append_tool_results(results)
		assert output.display_view() == "full result"
		assert ms.get_all()[0]["content"] == "context view"

	def test_append_error_uses_error_message(self):
		ms = MessageStore()
		results = [ToolResult(tool_call_id="1", tool_name="t", arguments={}, error="bad", success=False)]
		ms.append_tool_results(results)
		assert "[错误] bad" in ms.get_all()[0]["content"]

	def test_append_long_content_not_truncated_by_default(self):
		ms = MessageStore()
		output = ToolOutput.text("x" * 5000)
		results = [ToolResult(tool_call_id="1", tool_name="t", arguments={}, output=output, success=True)]
		ms.append_tool_results(results)
		content = ms.get_all()[0]["content"]
		assert content == "x" * 5000

	def test_append_with_summary_keeps_content_for_llm_context(self):
		ms = MessageStore()
		output = ToolOutput.text("very long " * 500, summary="brief")
		results = [ToolResult(tool_call_id="1", tool_name="t", arguments={}, output=output, success=True)]
		ms.append_tool_results(results)
		assert ms.get_all()[0]["content"] == "very long " * 500

	def test_append_json_output(self):
		ms = MessageStore()
		output = ToolOutput.json_output({"status": 200, "data": [1, 2, 3]})
		results = [ToolResult(tool_call_id="1", tool_name="t", arguments={}, output=output, success=True)]
		ms.append_tool_results(results)
		content = ms.get_all()[0]["content"]
		assert "200" in content

	def test_append_with_artifacts_shows_refs(self):
		ms = MessageStore()
		ref = ArtifactRef(id="abc123", kind="text", size=5000)
		output = ToolOutput(content="data", content_type="text", artifacts=[ref])
		results = [ToolResult(tool_call_id="1", tool_name="t", arguments={}, output=output, success=True)]
		ms.append_tool_results(results)
		content = ms.get_all()[0]["content"]
		assert "abc123" in content

	def test_append_error_tooloutput(self):
		ms = MessageStore()
		output = ToolOutput.error("something failed")
		results = [ToolResult(tool_call_id="1", tool_name="t", arguments={}, output=output, error="something failed", success=False)]
		ms.append_tool_results(results)
		assert "[错误]" in ms.get_all()[0]["content"]

	def test_append_multiple_results(self):
		ms = MessageStore()
		results = [
			ToolResult(tool_call_id="1", tool_name="a", arguments={}, output=ToolOutput.text("r1"), success=True),
			ToolResult(tool_call_id="2", tool_name="b", arguments={}, output=ToolOutput.text("r2"), success=True),
			ToolResult(tool_call_id="3", tool_name="c", arguments={}, error="fail", success=False),
		]
		ms.append_tool_results(results)
		assert ms.count == 3
		assert "r1" in ms.get_all()[0]["content"]
		assert "r2" in ms.get_all()[1]["content"]
		assert "[错误]" in ms.get_all()[2]["content"]

	def test_tool_call_id_preserved(self):
		ms = MessageStore()
		results = [ToolResult(tool_call_id="tc-42", tool_name="t", arguments={}, output=ToolOutput.text("ok"), success=True)]
		ms.append_tool_results(results)
		assert ms.get_all()[0]["tool_call_id"] == "tc-42"

	def test_role_is_tool(self):
		ms = MessageStore()
		results = [ToolResult(tool_call_id="1", tool_name="t", arguments={}, output=ToolOutput.text("ok"), success=True)]
		ms.append_tool_results(results)
		assert ms.get_all()[0]["role"] == "tool"

	def test_large_json_content_not_truncated_by_default(self):
		ms = MessageStore()
		big_data = {"items": [{"id": i, "name": f"item_{i}" * 50} for i in range(100)]}
		output = ToolOutput.json_output(big_data)
		results = [ToolResult(tool_call_id="1", tool_name="t", arguments={}, output=output, success=True)]
		ms.append_tool_results(results)
		content = ms.get_all()[0]["content"]
		assert "item_99" in content
		assert "省略" not in content
