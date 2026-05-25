import pytest

from axc_agent_engine.observability.audit import InMemoryAuditSink
from axc_agent_engine.core.context import ExecutionContext, ExecutionServices
from axc_agent_engine.core.plugin_manager import PluginManager
from axc_agent_engine.plugins import PluginContext
from axc_agent_engine.plugins.builtin.output_format.plugin import OutputContractError, OutputFormatPlugin
from axc_agent_engine.plugins.builtin.output_format.support import OutputFormatService


def test_output_format_service_validates_json_schema():
	service = OutputFormatService("json_schema", {"schema": {"type": "object", "required": ["answer"]}})
	assert service.validate('{"answer":"ok"}').valid
	result = service.validate('{"other":"no"}')
	assert not result.valid


@pytest.mark.asyncio
async def test_output_format_service_repairs_with_utility_llm():
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


def test_output_format_service_markdown_required_sections_order():
	service = OutputFormatService("markdown", {
		"template": "## Summary\n\n## Risk",
		"section_order": True,
	})
	assert service.validate("## Summary\nok\n## Risk\nlow").valid
	result = service.validate("## Risk\nlow\n## Summary\nok")
	assert not result.valid
	assert "sections are not in required order" in result.errors


def test_output_format_service_max_output_chars():
	service = OutputFormatService("text", {"must_contain": ["ok"]}, max_output_chars=4)
	result = service.validate("too long ok")
	assert not result.valid
	assert result.errors[0].startswith("output exceeds max_output_chars")


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
async def test_plugin_manager_propagates_fail_closed_complete_error(plugin_ctx):
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
