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


def test_skill_initialize_raises_on_missing_directory(tmp_path):
	from axc_agent_engine.plugins.builtin.skill.plugin import SkillPlugin

	plugin = SkillPlugin()
	with pytest.raises(FileNotFoundError, match="Skill directory not found"):
		plugin.initialize({"paths": [str(tmp_path / "missing")]}, None)


def test_duplicate_skill_policy_error_reports_conflict(tmp_path):
	from axc_agent_engine.plugins.builtin.skill.plugin import SkillPlugin

	_create_skill(tmp_path / "first", "demo")
	_create_skill(tmp_path / "second", "demo")
	plugin = SkillPlugin()

	with pytest.raises(ValueError, match="Duplicate skill"):
		plugin.initialize({"paths": [str(tmp_path / "first"), str(tmp_path / "second")]}, None)


def test_skill_catalog_rejects_invalid_items():
	from axc_agent_engine.plugins import PluginContext
	from axc_agent_engine.plugins.builtin.skill.plugin import SkillPlugin

	class Catalog:
		def list_skills(self):
			return (
				{"name": "allowed", "description": "A", "content": "body", "trigger_keywords": "one"},
				{"name": "", "content": "empty"},
				"bad",
				{"name": "denied", "content": "no"},
			)

	plugin = SkillPlugin()
	with pytest.raises(ValueError, match="name cannot be empty"):
		plugin.initialize(
			{"allowed_skills": ["allowed", "denied"], "denied_skills": ["denied"]},
			PluginContext(resources={"skill.catalog": Catalog()}),
		)


def test_skill_catalog_duplicate_policy_replace_and_skip():
	from axc_agent_engine.plugins import PluginContext
	from axc_agent_engine.plugins.builtin.skill.plugin import SkillPlugin

	catalog = {"skills": {
		"first": {"name": "demo", "description": "old", "content": "old"},
		"second": {"name": "demo", "description": "new", "content": "new"},
	}}
	replace = SkillPlugin()
	replace.initialize({"duplicate_policy": "replace"}, PluginContext(resources={"skill.catalog": catalog}))
	skip = SkillPlugin()
	skip.initialize({"duplicate_policy": "skip"}, PluginContext(resources={"skill.catalog": catalog}))

	assert replace._skills["demo"]["description"] == "new"
	assert skip._skills["demo"]["description"] == "old"
	assert skip._load_errors == []


def test_skill_frontmatter_parse_and_extension_normalization(tmp_path):
	from axc_agent_engine.plugins.builtin.skill.plugin import SkillPlugin, _normalize_extensions, _parse_frontmatter

	with pytest.raises(ValueError, match="frontmatter YAML"):
		_parse_frontmatter("---\n: bad yaml\n---\nbody")
	assert _parse_frontmatter("plain body") == ({}, "plain body")
	assert _normalize_extensions(["py", ".sh", "exe", ""]) == {".py", ".sh"}
	assert _normalize_extensions("bad") == {".py", ".sh"}

	(skill_dir := tmp_path / "plain").mkdir()
	(skill_dir / "skill.md").write_text("plain body", encoding="utf-8")
	plugin = SkillPlugin()
	plugin.initialize({"paths": [str(tmp_path)], "allowed_extensions": ["py"]}, None)

	assert plugin._skills["plain"]["name"] == "plain"
	assert plugin._skills["plain"]["content"] == "plain body"


@pytest.mark.asyncio
async def test_skill_fail_fast_and_catalog_edges(tmp_path):
	from axc_agent_engine.plugins import PluginContext
	from axc_agent_engine.plugins.builtin.skill.plugin import SkillPlugin, _catalog_skills, _parse_frontmatter

	with pytest.raises(ValueError, match="requires paths"):
		SkillPlugin().initialize({}, None)
	with pytest.raises(ValueError, match="duplicate_policy"):
		SkillPlugin().initialize({"paths": [str(tmp_path)], "duplicate_policy": "bad"}, None)
	with pytest.raises(TypeError, match="skill.catalog"):
		_catalog_skills(object())
	with pytest.raises(ValueError, match="frontmatter must be an object"):
		_parse_frontmatter("---\n- bad\n---\nbody")

	catalog = type("Catalog", (), {"skills": [{"name": "allowed", "content": "body"}, {"name": "denied", "content": "body"}]})()
	plugin = SkillPlugin()
	plugin.initialize(
		{"allowed_skills": ["allowed", "denied"], "denied_skills": ["denied"]},
		PluginContext(resources={"skill.catalog": catalog}),
	)
	assert sorted(plugin._skills) == ["allowed"]
	assert plugin.inject_context(None, "topic")
	status = await plugin._tool_skill_status({}, {})
	assert status.content["loaded"] == 1

	_create_skill(tmp_path, "skipme")
	(tmp_path / "ignored.txt").write_text("x", encoding="utf-8")
	empty_dir = tmp_path / "empty"
	empty_dir.mkdir()
	loader = SkillPlugin()
	with pytest.raises(ValueError, match="loaded no skills"):
		loader.initialize({"paths": [str(tmp_path)], "allowed_skills": ["missing"]}, None)


@pytest.mark.asyncio
async def test_list_load_skill_filter_not_found_and_metadata(tmp_path):
	from axc_agent_engine.plugins.builtin.skill.plugin import SkillPlugin

	_create_skill(tmp_path, "demo")
	plugin = SkillPlugin()
	plugin.initialize({"paths": [str(tmp_path)]}, None)
	ctx = ExecutionContext()

	listed = await plugin._tool_list_skills({"query": "docs"}, {"exec_ctx": ctx})
	filtered = await plugin._tool_list_skills({"query": "missing"}, {"exec_ctx": ctx})
	missing = await plugin._tool_load_skill({"skill_name": "missing"}, {"exec_ctx": ctx})

	assert listed.content["total"] == 1
	assert filtered.content["total"] == 0
	assert missing.is_error
	assert ctx.state.metadata["skill"]["last_action"] == "list"


@pytest.mark.asyncio
async def test_run_skill_script_rejects_missing_no_scripts_extension_and_path_escape(tmp_path):
	from axc_agent_engine.plugins.builtin.skill.plugin import SkillPlugin

	_create_skill(tmp_path, "demo")
	(tmp_path / "demo" / "scripts" / "note.txt").write_text("x", encoding="utf-8")
	plugin = SkillPlugin()
	plugin.initialize({"paths": [str(tmp_path)]}, None)
	plugin._skills["noscripts"] = {**plugin._skills["demo"], "name": "noscripts", "scripts_path": None}

	assert (await plugin._tool_run_script({"skill_name": "missing", "script_name": "run.py"}, {})).is_error
	assert (await plugin._tool_run_script({"skill_name": "noscripts", "script_name": "run.py"}, {})).is_error
	assert (await plugin._tool_run_script({"skill_name": "demo", "script_name": "note.txt"}, {})).is_error
	assert (await plugin._tool_run_script({"skill_name": "demo", "script_name": "../SKILL.md"}, {})).is_error


@pytest.mark.asyncio
async def test_run_skill_script_bad_args_execution_error_and_failed_exit(tmp_path):
	from axc_agent_engine.plugins.builtin.skill.plugin import SkillPlugin

	class BadArgsExecutor:
		async def run(self, spec):
			raise ValueError("bad quote")

	class FailingExecutor:
		async def run(self, spec):
			return CommandResult(exit_code=2, stdout="", stderr="boom", timed_out=True)

	class CrashingExecutor:
		async def run(self, spec):
			raise RuntimeError("crashed")

	_create_skill(tmp_path, "demo")
	plugin = SkillPlugin()
	plugin.initialize({"paths": [str(tmp_path)]}, None)

	bad_args = await plugin._tool_run_script(
		{"skill_name": "demo", "script_name": "run.py", "args": "'unterminated"},
		{"command_executor": BadArgsExecutor()},
	)
	failed = await plugin._tool_run_script(
		{"skill_name": "demo", "script_name": "run.py"},
		{"command_executor": FailingExecutor()},
	)
	crashed = await plugin._tool_run_script(
		{"skill_name": "demo", "script_name": "run.py"},
		{"command_executor": CrashingExecutor()},
	)

	assert bad_args.is_error
	assert failed.content["returncode"] == 2
	assert failed.content["timed_out"] is True
	assert crashed.is_error
