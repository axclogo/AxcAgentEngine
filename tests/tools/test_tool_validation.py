"""Tests for #17 Tool argument validation."""
import pytest
from axc_agent_engine.tools.executor import ToolArgumentValidator, execute_tool
from axc_agent_engine.tools.tool_output import ToolOutput
from axc_agent_engine.core.schema import ToolDefinition


class TestValidateArguments:
	def setup_method(self):
		self.validator = ToolArgumentValidator()

	def _make_tool(self, parameters: dict) -> ToolDefinition:
		async def noop(args, ctx):
			return ToolOutput.text("ok")
		return ToolDefinition(name="test", parameters=parameters, execute=noop)

	def test_no_schema(self):
		tool = self._make_tool({})
		assert self.validator.validate(tool, {"any": "thing"}) is None

	def test_non_object_schema(self):
		tool = self._make_tool({"type": "string"})
		assert self.validator.validate(tool, {}) is None

	def test_required_field_present(self):
		tool = self._make_tool({
			"type": "object",
			"properties": {"name": {"type": "string"}},
			"required": ["name"],
		})
		assert self.validator.validate(tool, {"name": "hello"}) is None

	def test_required_field_missing(self):
		tool = self._make_tool({
			"type": "object",
			"properties": {"name": {"type": "string"}},
			"required": ["name"],
		})
		err = self.validator.validate(tool, {})
		assert err is not None
		assert "Missing required parameter: name" in err

	def test_multiple_required_first_missing(self):
		tool = self._make_tool({
			"type": "object",
			"properties": {"a": {"type": "string"}, "b": {"type": "string"}},
			"required": ["a", "b"],
		})
		err = self.validator.validate(tool, {"b": "val"})
		assert "Missing required parameter: a" in err

	def test_type_string_valid(self):
		tool = self._make_tool({
			"type": "object",
			"properties": {"name": {"type": "string"}},
		})
		assert self.validator.validate(tool, {"name": "hello"}) is None

	def test_type_string_invalid(self):
		tool = self._make_tool({
			"type": "object",
			"properties": {"name": {"type": "string"}},
		})
		err = self.validator.validate(tool, {"name": 123})
		assert err is not None
		assert "type mismatch" in err

	def test_type_integer_valid(self):
		tool = self._make_tool({
			"type": "object",
			"properties": {"count": {"type": "integer"}},
		})
		assert self.validator.validate(tool, {"count": 5}) is None

	def test_type_integer_invalid(self):
		tool = self._make_tool({
			"type": "object",
			"properties": {"count": {"type": "integer"}},
		})
		err = self.validator.validate(tool, {"count": "five"})
		assert "type mismatch" in err

	def test_type_number_accepts_int(self):
		tool = self._make_tool({
			"type": "object",
			"properties": {"val": {"type": "number"}},
		})
		assert self.validator.validate(tool, {"val": 42}) is None

	def test_type_number_accepts_float(self):
		tool = self._make_tool({
			"type": "object",
			"properties": {"val": {"type": "number"}},
		})
		assert self.validator.validate(tool, {"val": 3.14}) is None

	def test_type_boolean_valid(self):
		tool = self._make_tool({
			"type": "object",
			"properties": {"flag": {"type": "boolean"}},
		})
		assert self.validator.validate(tool, {"flag": True}) is None

	def test_type_boolean_invalid(self):
		tool = self._make_tool({
			"type": "object",
			"properties": {"flag": {"type": "boolean"}},
		})
		err = self.validator.validate(tool, {"flag": "yes"})
		assert "type mismatch" in err

	def test_type_array_valid(self):
		tool = self._make_tool({
			"type": "object",
			"properties": {"items": {"type": "array"}},
		})
		assert self.validator.validate(tool, {"items": [1, 2, 3]}) is None

	def test_type_array_invalid(self):
		tool = self._make_tool({
			"type": "object",
			"properties": {"items": {"type": "array"}},
		})
		err = self.validator.validate(tool, {"items": "not-array"})
		assert "type mismatch" in err

	def test_type_object_valid(self):
		tool = self._make_tool({
			"type": "object",
			"properties": {"config": {"type": "object"}},
		})
		assert self.validator.validate(tool, {"config": {"key": "val"}}) is None

	def test_type_object_invalid(self):
		tool = self._make_tool({
			"type": "object",
			"properties": {"config": {"type": "object"}},
		})
		err = self.validator.validate(tool, {"config": [1, 2]})
		assert "type mismatch" in err

	def test_none_value_passes(self):
		tool = self._make_tool({
			"type": "object",
			"properties": {"name": {"type": "string"}},
		})
		assert self.validator.validate(tool, {"name": None}) is None

	def test_unknown_type_passes(self):
		tool = self._make_tool({
			"type": "object",
			"properties": {"x": {"type": "custom_type"}},
		})
		assert self.validator.validate(tool, {"x": "anything"}) is None

	def test_extra_fields_ignored(self):
		tool = self._make_tool({
			"type": "object",
			"properties": {"name": {"type": "string"}},
			"required": ["name"],
		})
		assert self.validator.validate(tool, {"name": "ok", "extra": 123}) is None

	def test_empty_required_list(self):
		tool = self._make_tool({
			"type": "object",
			"properties": {"name": {"type": "string"}},
			"required": [],
		})
		assert self.validator.validate(tool, {}) is None


class TestExecuteToolValidation:
	@pytest.mark.asyncio
	async def test_validation_failure_returns_error_result(self):
		async def noop(args, ctx):
			return ToolOutput.text("ok")
		tool = ToolDefinition(
			name="test_tool",
			parameters={"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
			execute=noop,
		)
		result = await execute_tool(tool, {}, "call-1")
		assert not result.success
		assert "Missing required parameter: path" in result.error

	@pytest.mark.asyncio
	async def test_validation_success_executes_tool(self):
		async def echo(args, ctx):
			return ToolOutput.json_output(args)
		tool = ToolDefinition(
			name="echo",
			parameters={"type": "object", "properties": {"msg": {"type": "string"}}, "required": ["msg"]},
			execute=echo,
		)
		result = await execute_tool(tool, {"msg": "hello"}, "call-2")
		assert result.success
		assert "hello" in result.context_view()

	@pytest.mark.asyncio
	async def test_no_execute_function(self):
		tool = ToolDefinition(name="broken", parameters={})
		result = await execute_tool(tool, {}, "call-3")
		assert not result.success
		assert "no execute function" in result.error

	@pytest.mark.asyncio
	async def test_type_mismatch_returns_error(self):
		async def noop(args, ctx):
			return ToolOutput.text("ok")
		tool = ToolDefinition(
			name="typed",
			parameters={"type": "object", "properties": {"count": {"type": "integer"}}, "required": ["count"]},
			execute=noop,
		)
		result = await execute_tool(tool, {"count": "not-int"}, "call-4")
		assert not result.success
		assert "type mismatch" in result.error
