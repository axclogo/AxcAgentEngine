from __future__ import annotations

import pytest

from axc_agent_engine.observability.audit import InMemoryAuditSink
from axc_agent_engine.core.context import ExecutionContext, ExecutionServices
from axc_agent_engine.runtime.sandbox_models import CommandResult
from axc_agent_engine.storage.result_store import InMemoryResultStore


def _create_skill(root, name: str = "demo", body: str = "body"):
	skill_dir = root / name
	scripts_dir = skill_dir / "scripts"
	scripts_dir.mkdir(parents=True)
	(skill_dir / "SKILL.md").write_text(
		f"---\nname: {name}\ndescription: Demo\nversion: 1.2.3\ntrusted: true\ntrigger_keywords:\n  - docs\n---\n{body}",
		encoding="utf-8",
	)
	(scripts_dir / "run.py").write_text("print('ok')", encoding="utf-8")
	return skill_dir


def test_skill_tools_expose_governance_capabilities(tmp_path):
	from axc_agent_engine.plugins.builtin.skill.plugin import SkillPlugin

	_create_skill(tmp_path)
	plugin = SkillPlugin()
	plugin.initialize({"paths": [str(tmp_path)]}, None)

	tools = {tool.name: tool for tool in plugin.get_tools()}
	assert tools["list_skills"].capability == "skill_read"
	assert tools["load_skill"].risk_level == "safe"
	assert tools["skill_status"].capability == "skill_read"
	assert tools["reload_skills"].risk_level == "moderate"
	assert tools["run_skill_script"].capability == "shell"


def test_allowed_and_denied_skills_control_loaded_surface(tmp_path):
	from axc_agent_engine.plugins.builtin.skill.plugin import SkillPlugin

	_create_skill(tmp_path, "demo")
	_create_skill(tmp_path, "secret")
	plugin = SkillPlugin()
	plugin.initialize({"paths": [str(tmp_path)], "allowed_skills": ["demo", "secret"], "denied_skills": ["secret"]}, None)

	assert sorted(plugin._skills) == ["demo"]


def test_skill_plugin_loads_mounted_catalog_resource():
	from axc_agent_engine.plugins import PluginContext
	from axc_agent_engine.plugins.builtin.skill.plugin import SkillPlugin

	catalog = {
		"skills": [
			{
				"name": "mounted",
				"description": "Mounted skill",
				"when_to_use": "runtime",
				"content": "Use the mounted catalog.",
				"trigger_keywords": ["runtime"],
			}
		]
	}
	plugin = SkillPlugin()
	plugin.initialize({}, PluginContext(resources={"skill.catalog": catalog}))

	assert sorted(plugin._skills) == ["mounted"]
	assert plugin._skills["mounted"]["source"] == "skill.catalog"


@pytest.mark.asyncio
async def test_run_skill_script_can_be_disabled(tmp_path):
	from axc_agent_engine.plugins.builtin.skill.plugin import SkillPlugin

	_create_skill(tmp_path)
	plugin = SkillPlugin()
	plugin.initialize({"paths": [str(tmp_path)], "allow_scripts": False}, None)

	result = await plugin._tool_run_script({"skill_name": "demo", "script_name": "run.py"}, {})

	assert result.is_error
	assert "禁用" in result.content


@pytest.mark.asyncio
async def test_run_skill_script_respects_allowed_script_names(tmp_path):
	from axc_agent_engine.plugins.builtin.skill.plugin import SkillPlugin

	_create_skill(tmp_path)
	plugin = SkillPlugin()
	plugin.initialize({"paths": [str(tmp_path)], "allowed_script_names": ["other.py"]}, None)

	result = await plugin._tool_run_script({"skill_name": "demo", "script_name": "run.py"}, {})

	assert result.is_error
	assert "允许列表" in result.content


@pytest.mark.asyncio
async def test_run_skill_script_uses_limits_metadata_and_audit(tmp_path):
	from axc_agent_engine.plugins.builtin.skill.plugin import SkillPlugin

	class FakeExecutor:
		def __init__(self):
			self.spec = None

		async def run(self, spec):
			self.spec = spec
			return CommandResult(exit_code=0, stdout="ok", stderr="")

	_create_skill(tmp_path)
	audit = InMemoryAuditSink()
	ctx = ExecutionContext(services=ExecutionServices(audit_sink=audit))
	ctx.state.metadata.update({"agent_name": "agent-a", "session_id": "sess-1"})
	executor = FakeExecutor()
	plugin = SkillPlugin()
	plugin.initialize({"paths": [str(tmp_path)], "timeout": 7, "stdout_limit": 11, "stderr_limit": 13}, None)

	result = await plugin._tool_run_script(
		{"skill_name": "demo", "script_name": "run.py"},
		{"command_executor": executor, "exec_ctx": ctx},
	)
	events = await audit.list_events()

	assert not result.is_error
	assert executor.spec.timeout == 7
	assert executor.spec.stdout_limit == 11
	assert executor.spec.stderr_limit == 13
	assert ctx.state.metadata["skill"]["last_action"] == "run_script"
	assert events[-1].type == "skill_script_executed"
	assert events[-1].actor == "agent-a"


@pytest.mark.asyncio
async def test_load_skill_large_content_externalized(tmp_path):
	from axc_agent_engine.plugins.builtin.skill.plugin import SkillPlugin

	_create_skill(tmp_path, body="x" * 64)
	store = InMemoryResultStore()
	plugin = SkillPlugin()
	plugin.initialize({"paths": [str(tmp_path)], "max_skill_content_chars": 8}, None)

	result = await plugin._tool_load_skill({"skill_name": "demo"}, {"result_store": store})

	assert not result.is_error
	assert result.content["content"]["truncated"] is True
	assert result.artifacts
	assert await store.get(result.artifacts[0].id, 0, 64) == "x" * 64


@pytest.mark.asyncio
async def test_script_large_stdout_externalized(tmp_path):
	from axc_agent_engine.plugins.builtin.skill.plugin import SkillPlugin

	class FakeExecutor:
		async def run(self, spec):
			return CommandResult(exit_code=0, stdout="x" * 64, stderr="")

	_create_skill(tmp_path)
	store = InMemoryResultStore()
	plugin = SkillPlugin()
	plugin.initialize({"paths": [str(tmp_path)], "max_result_bytes": 8}, None)

	result = await plugin._tool_run_script(
		{"skill_name": "demo", "script_name": "run.py"},
		{"command_executor": FakeExecutor(), "result_store": store},
	)

	assert not result.is_error
	assert result.content["stdout"]["truncated"] is True
	assert result.artifacts
	assert await store.get(result.artifacts[0].id, 0, 64) == "x" * 64


@pytest.mark.asyncio
async def test_skill_status_and_reload_report_load_errors(tmp_path):
	from axc_agent_engine.plugins.builtin.skill.plugin import SkillPlugin

	plugin = SkillPlugin()
	plugin.initialize({"paths": [str(tmp_path / "missing")]}, None)

	status = await plugin._tool_skill_status({}, {})
	assert status.content["loaded"] == 0
	assert status.content["errors"]

	_create_skill(tmp_path)
	plugin._paths = [str(tmp_path)]
	reloaded = await plugin._tool_reload_skills({}, {})

	assert reloaded.content["loaded"] == 1
	assert reloaded.content["errors"] == []


def test_duplicate_skill_policy_error_reports_conflict(tmp_path):
	from axc_agent_engine.plugins.builtin.skill.plugin import SkillPlugin

	_create_skill(tmp_path / "first", "demo")
	_create_skill(tmp_path / "second", "demo")
	plugin = SkillPlugin()
	plugin.initialize({"paths": [str(tmp_path / "first"), str(tmp_path / "second")], "duplicate_policy": "error"}, None)

	assert "demo" in plugin._skills
	assert any(error.get("error") == "duplicate skill" for error in plugin._load_errors)
