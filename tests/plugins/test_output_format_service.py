import sys

import pytest

from axc_agent_engine.observability.audit import InMemoryAuditSink
from axc_agent_engine.core.context import ExecutionContext, ExecutionServices
from axc_agent_engine.core.plugin_manager import PluginManager
from axc_agent_engine.plugins import PluginContext
from axc_agent_engine.plugins.builtin.output_format.plugin import OutputContractError, OutputFormatPlugin
from axc_agent_engine.plugins.builtin.output_format.support import OutputFormatService
from axc_agent_engine.plugins.builtin.output_format.support.service import (
	OutputRepairer,
	OutputValidator,
	RepairPromptBuilder,
	ValidationResult,
	_fallback_json_schema_errors,
	_json_type,
	_matches_json_type,
	extract_json,
	strip_code_fence,
)


def test_output_format_service_validates_json_schema():
	service = OutputFormatService("json_schema", {"schema": {"type": "object", "required": ["answer"]}})
	assert service.validate('{"answer":"ok"}').valid
	result = service.validate('{"other":"no"}')
	assert not result.valid


@pytest.mark.asyncio
async def test_output_format_service_repairs_with_utility_model():
	class Utility:
		async def ask(self, prompt):
			return '{"answer":"fixed"}'

	service = OutputFormatService("json_schema", {"schema": {"type": "object", "required": ["answer"]}}, Utility())
	content, result = await service.validate_and_repair("not json", max_attempts=1)
	assert result.valid
	assert content == '{"answer":"fixed"}'


@pytest.mark.asyncio
async def test_output_format_service_revalidates_after_local_json_repair():
	service = OutputFormatService("json_schema", {"schema": {"type": "object", "required": ["answer"]}})
	content, result = await service.validate_and_repair('prefix {"answer":"ok"} suffix', max_attempts=1)
	assert result.valid
	assert content == '{"answer":"ok"}'


def test_output_format_service_validates_nested_schema_rules():
	service = OutputFormatService("json_schema", {"schema": {
		"type": "object",
		"required": ["items"],
		"additionalProperties": False,
		"properties": {
			"items": {
				"type": "array",
				"minItems": 1,
				"items": {
					"type": "object",
					"required": ["name", "kind"],
					"properties": {
						"name": {"type": "string"},
						"kind": {"enum": ["a", "b"]},
					},
				},
			},
		},
	}})
	valid = service.validate('{"items":[{"name":"x","kind":"a"}]}')
	invalid = service.validate('{"items":[{"name":1,"kind":"z"}],"extra":true}')
	assert valid.valid
	assert not invalid.valid
	assert any("extra" in error or "kind" in error or "name" in error for error in invalid.errors)


def test_output_format_service_text_regex_and_line_rules():
	service = OutputFormatService("text", {
		"max_lines": 2,
		"required_patterns": [r"ID-\d+"],
		"forbidden_patterns": [r"SECRET"],
	})
	assert service.validate("Ticket ID-123\nclosed").valid
	result = service.validate("Ticket\nwithout id\nSECRET")
	assert not result.valid
	assert len(result.errors) == 3


def test_output_validator_unknown_format_and_empty_markdown_boundaries():
	assert OutputValidator("", {}, 0).validate("").valid
	assert OutputValidator("unknown", {}, 0).validate("anything").valid
	result = OutputValidator("markdown", {}, 0).validate("  ")
	assert not result.valid
	assert result.errors == ["empty output"]


def test_output_format_service_markdown_required_sections_order():
	service = OutputFormatService("markdown", {
		"template": "## Summary\n\n## Risk",
		"section_order": True,
	})
	assert service.validate("## Summary\nok\n## Risk\nlow").valid
	result = service.validate("## Risk\nlow\n## Summary\nok")
	assert not result.valid
	assert "sections are not in required order" in result.errors


def test_output_format_markdown_derives_required_sections_from_template_and_patterns():
	service = OutputFormatService("markdown", {
		"template": "# Title {name}\n\n## Risk",
		"required_patterns": [r"ID-\d+"],
		"forbidden_patterns": [r"SECRET"],
	})

	result = service.validate("# Title Alpha\nbody SECRET")

	assert not result.valid
	assert any("Risk" in error for error in result.errors)
	assert any("required pattern" in error for error in result.errors)
	assert any("forbidden pattern" in error for error in result.errors)


def test_output_format_service_max_output_chars():
	service = OutputFormatService("text", {"must_contain": ["ok"]}, max_output_chars=4)
	result = service.validate("too long ok")
	assert not result.valid
	assert result.errors[0].startswith("output exceeds max_output_chars")


def test_output_format_service_markdown_and_text_more_rules():
	assert not OutputFormatService("markdown").validate("").valid
	md = OutputFormatService("markdown", {
		"template": "## A",
		"required_sections": ["A"],
		"required_patterns": [r"ID-\d+"],
		"forbidden_patterns": ["BAD"],
	})
	result = md.validate("B BAD")
	assert not result.valid
	text = OutputFormatService("text", {
		"max_length": 3,
		"must_contain": ["x"],
		"must_not_contain": ["bad"],
	})
	assert not text.validate("bad long").valid


def test_output_format_extract_strip_and_prompt_builders():
	assert extract_json("```json\n{\"a\":1}\n```") == '{"a":1}'
	assert extract_json("prefix [1,2] suffix") == "[1,2]"
	assert extract_json("none") == ""
	assert strip_code_fence("```json\nx\n```") == "x"
	assert RepairPromptBuilder("json_schema", {"schema": {"type": "object"}}).build("abcdef")
	assert RepairPromptBuilder("markdown", {"template": "## A"}).build("x")
	assert RepairPromptBuilder("text", {"max_length": 3, "must_contain": ["a"], "must_not_contain": ["b"]}).build("x")
	assert RepairPromptBuilder("", {}).build("x") == ""
	assert ValidationResult(True, content="abc").to_dict()["content_length"] == 3


def test_output_format_extract_json_prefers_braces_then_arrays():
	assert extract_json("before {\"a\":1} middle [2] after") == '{"a":1}'
	assert extract_json("before [1, 2] after") == "[1, 2]"
	assert strip_code_fence("```\nplain") == "plain"


def test_fallback_json_schema_reports_nested_edge_errors():
	schema = {
		"type": "object",
		"required": ["items"],
		"additionalProperties": False,
		"properties": {
			"items": {
				"type": "array",
				"minItems": 2,
				"maxItems": 2,
				"items": {"type": ["integer", "null"], "enum": [1, None]},
			},
		},
	}

	errors = _fallback_json_schema_errors({"items": [2, "bad", 3], "extra": True}, schema)

	assert any("additional property" in error for error in errors)
	assert any("expected at most 2" in error for error in errors)
	assert any("not in enum" in error for error in errors)
	assert any("expected ['integer', 'null']" in error for error in errors)
	assert _matches_json_type(False, "boolean") is True
	assert _matches_json_type(True, "integer") is False
	assert _matches_json_type(1.2, "number") is True
	assert _matches_json_type(object(), "unknown") is True
	assert _json_type(None) == "null"
	assert _json_type(True) == "boolean"
	assert _json_type({}) == "object"
	assert _json_type([]) == "array"
	assert _json_type("x") == "string"
	assert _json_type(1) == "integer"
	assert _json_type(1.0) == "number"


def test_json_schema_fallback_path_when_jsonschema_is_missing(monkeypatch):
	monkeypatch.setitem(sys.modules, "jsonschema", None)
	service = OutputFormatService("json_schema", {"schema": {"type": "object", "required": ["answer"]}})

	result = service.validate('{"other": true}')

	assert not result.valid
	assert result.degraded is True
	assert any("missing required field" in error for error in result.errors)


@pytest.mark.asyncio
async def test_output_repairer_uses_local_repair_without_prompt_or_model():
	assert await OutputRepairer("json_schema", None, RepairPromptBuilder("json_schema", {}), 0).repair(
		"prefix {\"a\":1} suffix"
	) == '{"a":1}'
	assert await OutputRepairer("unknown", None, RepairPromptBuilder("unknown", {}), 0).repair("raw") == "raw"


@pytest.mark.asyncio
async def test_output_repairer_timeout_zero_and_empty_model_result_use_local():
	class EmptyUtility:
		async def ask(self, prompt):
			return ""

	repairer = OutputRepairer("json_schema", EmptyUtility(), RepairPromptBuilder("json_schema", {}), 0)

	assert await repairer.repair("prefix {\"a\":1} suffix") == '{"a":1}'


@pytest.mark.asyncio
async def test_output_format_service_repair_failure_records_error():
	class FailingUtility:
		async def ask(self, prompt):
			raise RuntimeError("repair down")

	service = OutputFormatService(
		"json_schema",
		{"schema": {"type": "object", "required": ["answer"]}},
		FailingUtility(),
	)

	result = await service.repair_with_result("bad", max_attempts=2)

	assert not result.validation.valid
	assert result.attempts == 1
	assert "repair attempt 1 failed" in result.errors[0]
	assert result.to_dict()["errors"] == result.errors


@pytest.mark.asyncio
async def test_output_format_plugin_records_metadata_and_audit(plugin_ctx):
	audit = InMemoryAuditSink()
	plugin = OutputFormatPlugin()
	plugin.initialize({
		"type": "json_schema",
		"schema_id": "answer_contract",
		"schema_version": "1",
		"schema": {"type": "object", "required": ["answer"]},
	}, plugin_ctx)
	ctx = ExecutionContext(services=ExecutionServices(audit_sink=audit))
	ctx.state.metadata.update({"agent_name": "agent-a", "session_id": "s1"})
	result = await plugin.on_execution_complete(ctx, 'prefix {"answer":"ok"} suffix', {})
	events = await audit.list_events()
	assert result == '{"answer":"ok"}'
	assert ctx.state.metadata["output_format"]["valid"] is True
	assert ctx.state.metadata["output_format"]["schema_id"] == "answer_contract"
	assert events[0].type == "output_format_validated"


@pytest.mark.asyncio
async def test_output_format_plugin_strict_failure_raises(plugin_ctx):
	plugin = OutputFormatPlugin()
	plugin.initialize({
		"type": "json_schema",
		"strict": True,
		"auto_repair": False,
		"schema": {"type": "object", "required": ["answer"]},
	}, plugin_ctx)
	ctx = ExecutionContext()
	with pytest.raises(OutputContractError):
		await plugin.on_execution_complete(ctx, '{"other":"no"}', {})
	assert ctx.state.metadata["output_format"]["strict_failed"] is True


@pytest.mark.asyncio
async def test_plugin_manager_propagates_complete_error(plugin_ctx):
	plugin = OutputFormatPlugin()
	plugin.initialize({
		"type": "json_schema",
		"strict": True,
		"auto_repair": False,
		"schema": {"type": "object", "required": ["answer"]},
	}, plugin_ctx)
	pm = PluginManager([plugin])
	with pytest.raises(OutputContractError):
		await pm.on_execution_complete(ExecutionContext(), '{"other":"no"}', {})


@pytest.mark.asyncio
async def test_output_format_validate_tool(plugin_ctx):
	plugin = OutputFormatPlugin()
	plugin.initialize({
		"type": "text",
		"constraints": {"must_contain": ["READY"], "forbidden_patterns": [r"SECRET"]},
	}, plugin_ctx)
	tools = {tool.name: tool for tool in plugin.get_tools()}
	result = await tools["output_format_validate"].execute({"content": "READY"}, {})
	bad = await tools["output_format_validate"].execute({"content": "SECRET"}, {})
	assert result.content["valid"] is True
	assert bad.content["valid"] is False
	assert tools["output_format_validate"].capability == "output_validation"


@pytest.mark.asyncio
async def test_output_format_validate_tool_empty_and_repair(plugin_ctx):
	class Utility:
		async def ask(self, prompt):
			return "```json\n{\"answer\":\"ok\"}\n```"
	plugin_ctx.utility_model = Utility()
	plugin = OutputFormatPlugin()
	plugin.initialize({
		"type": "json_schema",
		"schema": {"type": "object", "required": ["answer"]},
		"repair_attempts": 1,
	}, plugin_ctx)
	tool = plugin.get_tools()[0]
	assert (await tool.execute({"content": ""}, {})).is_error
	result = await tool.execute({"content": "bad", "repair": True}, {})
	assert result.content["valid"] is True


@pytest.mark.asyncio
async def test_output_format_plugin_context_and_noop(plugin_ctx):
	plugin = OutputFormatPlugin()
	plugin.initialize({}, plugin_ctx)
	assert plugin.inject_context(ExecutionContext()) == ""
	assert await plugin.on_execution_complete(ExecutionContext(), "x", {}) == "x"
	plugin.initialize({"type": "markdown", "template": "## A"}, plugin_ctx)
	assert "## A" in plugin.inject_context(ExecutionContext())
	plugin.initialize({"type": "text", "constraints": "READY"}, plugin_ctx)
	assert "READY" in plugin.inject_context(ExecutionContext())
	assert plugin._validate_output("READY")


@pytest.mark.asyncio
async def test_output_format_plugin_json_context_validate_schema_and_non_strict_failure(plugin_ctx):
	plugin = OutputFormatPlugin()
	plugin.initialize({
		"type": "json_schema",
		"schema": {"type": "object", "required": ["answer"]},
		"auto_repair": False,
	}, plugin_ctx)
	ctx = ExecutionContext()

	result = await plugin.on_execution_complete(ctx, '{"other": true}', {})

	assert "JSON Schema" in plugin.inject_context(ctx)
	assert result == '{"other": true}'
	assert ctx.state.metadata["output_format"]["valid"] is False
	assert plugin._validate_json('{"answer":"ok"}') is True
	assert plugin._validate_schema({"answer": "ok"}) is True
	assert plugin._validate_schema({"other": "x"}) is False
	plugin._schema = "bad"
	assert plugin._validate_schema({}) is True


@pytest.mark.asyncio
async def test_output_format_plugin_auto_repair_failure_strict_and_non_strict(plugin_ctx):
	class BadUtility:
		async def ask(self, prompt):
			return "still bad"

	plugin_ctx.utility_model = BadUtility()
	non_strict = OutputFormatPlugin()
	non_strict.initialize({
		"type": "json_schema",
		"schema": {"type": "object", "required": ["answer"]},
		"strict": False,
		"repair_attempts": 1,
	}, plugin_ctx)
	strict = OutputFormatPlugin()
	strict.initialize({
		"type": "json_schema",
		"schema": {"type": "object", "required": ["answer"]},
		"strict": True,
		"repair_attempts": 1,
	}, plugin_ctx)

	assert await non_strict.on_execution_complete(ExecutionContext(), "bad", {}) == "still bad"
	with pytest.raises(OutputContractError):
		await strict.on_execution_complete(ExecutionContext(), "bad", {})
