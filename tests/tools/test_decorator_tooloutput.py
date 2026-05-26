"""Tests for @tool decorator ToolOutput enforcement."""
import pytest
from axc_agent_engine.tools.decorator import tool
from axc_agent_engine.tools.tool_output import ToolOutput


class TestDecoratorToolOutputEnforcement:
	def test_accepts_tooloutput_annotation(self):
		@tool(name="good")
		async def good_tool(x: str) -> ToolOutput:
			return ToolOutput.text(x)
		assert good_tool.tool_definition.name == "good"

	def test_rejects_str_annotation(self):
		with pytest.raises(TypeError, match="必须返回 ToolOutput"):
			@tool(name="bad")
			async def bad_tool(x: str) -> str:
				return x

	def test_rejects_dict_annotation(self):
		with pytest.raises(TypeError, match="必须返回 ToolOutput"):
			@tool(name="bad")
			async def bad_tool(x: str) -> dict:
				return {"x": x}

	def test_rejects_list_annotation(self):
		with pytest.raises(TypeError, match="必须返回 ToolOutput"):
			@tool(name="bad")
			async def bad_tool() -> list:
				return []

	def test_rejects_int_annotation(self):
		with pytest.raises(TypeError, match="必须返回 ToolOutput"):
			@tool(name="bad")
			async def bad_tool() -> int:
				return 0

	def test_no_annotation_allowed(self):
		"""Functions without return annotation are allowed (no enforcement)."""
		@tool(name="no_ann")
		async def no_ann_tool(x: str):
			return ToolOutput.text(x)
		assert no_ann_tool.tool_definition.name == "no_ann"

	@pytest.mark.asyncio
	async def test_execution_returns_tooloutput(self):
		@tool(name="exec_test")
		async def exec_tool(msg: str) -> ToolOutput:
			return ToolOutput.text(f"got: {msg}")
		td = exec_tool.tool_definition
		result = await td.execute({"msg": "hello"}, {})
		assert isinstance(result, ToolOutput)
		assert result.content == "got: hello"

	@pytest.mark.asyncio
	async def test_execution_with_defaults(self):
		@tool(name="defaults")
		async def defaults_tool(a: str, b: str = "world") -> ToolOutput:
			return ToolOutput.text(f"{a} {b}")
		td = defaults_tool.tool_definition
		result = await td.execute({"a": "hello"}, {})
		assert result.content == "hello world"

	@pytest.mark.asyncio
	async def test_execution_json_output(self):
		@tool(name="json_test")
		async def json_tool(n: int) -> ToolOutput:
			return ToolOutput.json_output({"result": n * 2})
		td = json_tool.tool_definition
		result = await td.execute({"n": 5}, {})
		assert result.content_type == "json"
		assert result.content["result"] == 10

	def test_decorator_preserves_docstring(self):
		@tool(name="doc_test")
		async def documented() -> ToolOutput:
			"""This is the description."""
			return ToolOutput.text("ok")
		assert documented.tool_definition.description == "This is the description."

	def test_decorator_custom_description(self):
		@tool(name="custom", description="Custom desc")
		async def custom_tool() -> ToolOutput:
			"""Ignored docstring."""
			return ToolOutput.text("ok")
		assert custom_tool.tool_definition.description == "Custom desc"

	def test_decorator_read_only(self):
		@tool(name="ro", is_read_only=True)
		async def ro_tool() -> ToolOutput:
			return ToolOutput.text("ok")
		assert ro_tool.tool_definition.is_read_only is True

	def test_decorator_timeout(self):
		@tool(name="slow", timeout=300)
		async def slow_tool() -> ToolOutput:
			return ToolOutput.text("ok")
		assert slow_tool.tool_definition.timeout == 300

	def test_decorator_deferred(self):
		@tool(name="defer", deferred=True)
		async def deferred_tool() -> ToolOutput:
			return ToolOutput.text("ok")
		assert deferred_tool.tool_definition.deferred is True

	def test_parameters_schema_generated(self):
		@tool(name="params")
		async def params_tool(name: str, count: int, flag: bool = False) -> ToolOutput:
			return ToolOutput.text("ok")
		td = params_tool.tool_definition
		props = td.parameters["properties"]
		assert props["name"]["type"] == "string"
		assert props["count"]["type"] == "integer"
		assert props["flag"]["type"] == "boolean"
		assert "name" in td.parameters["required"]
		assert "count" in td.parameters["required"]
		assert "flag" not in td.parameters["required"]
