"""Skill 插件 — 技能元数据注入 + 按需加载 + 受控脚本执行。"""
from __future__ import annotations

import hashlib
import logging
import os
import shlex
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from axc_agent_engine.core.errors import ErrorCategory, ErrorEnvelope
from axc_agent_engine.core.schema import ToolDefinition
from axc_agent_engine.plugins.base import BasePlugin
from axc_agent_engine.plugins.builtin.common import (
	exec_ctx_from_tool_context,
	externalize_text,
	result_store_from_context,
	strict_bounded_int,
)
from axc_agent_engine.plugins.builtin.config_schemas import SKILL_CONFIG_SCHEMA
from axc_agent_engine.runtime.sandbox_local import LocalSubprocessExecutor
from axc_agent_engine.runtime.sandbox_models import CommandSpec
from axc_agent_engine.runtime.sandbox_workspace import WorkspaceCommandExecutor

if TYPE_CHECKING:
	from axc_agent_engine.core.context import ExecutionContext
	from axc_agent_engine.plugins import PluginContext

logger = logging.getLogger(__name__)

_SKILL_FILENAMES = ("SKILL.md", "skill.md", "CLAUDE.md")
_SCRIPT_RUNNERS = {
	".py": "python3",
	".sh": "bash",
}


@dataclass(frozen=True)
class SkillScriptRequest:
	skill_name: str
	script_name: str
	script_args: str


@dataclass(frozen=True)
class ResolvedSkillScript:
	skill: dict[str, Any]
	scripts_path: str
	script_path: str
	runner: str


class SkillScriptPolicy:
	def __init__(
		self,
		skills: dict[str, dict],
		allow_scripts: bool,
		allowed_script_names: set[str],
		allowed_extensions: set[str],
	) -> None:
		self.skills = skills
		self.allow_scripts = allow_scripts
		self.allowed_script_names = allowed_script_names
		self.allowed_extensions = allowed_extensions

	def resolve(self, request: SkillScriptRequest) -> ResolvedSkillScript | tuple[str, str, bool]:
		if not self.allow_scripts:
			return "技能脚本执行已禁用", "skill.scripts_disabled", False
		if request.skill_name not in self.skills:
			return f"技能 '{request.skill_name}' 不存在", "skill.not_found", False
		skill = self.skills[request.skill_name]
		scripts_path = skill.get("scripts_path")
		if not scripts_path:
			return f"技能 '{request.skill_name}' 没有 scripts 目录", "skill.no_scripts", False
		if self.allowed_script_names and request.script_name not in self.allowed_script_names:
			return f"脚本 '{request.script_name}' 不在允许列表", "skill.script_denied", False
		script_file = os.path.join(scripts_path, request.script_name)
		if not os.path.exists(script_file):
			return f"脚本 '{request.script_name}' 不存在", "skill.script_not_found", False
		real_script = os.path.realpath(script_file)
		real_scripts_dir = os.path.realpath(scripts_path)
		if not (real_script == real_scripts_dir or real_script.startswith(real_scripts_dir + os.sep)):
			return "路径不合法", "skill.path_escape", False
		ext = os.path.splitext(real_script)[1].lower()
		runner = _SCRIPT_RUNNERS.get(ext)
		if ext not in self.allowed_extensions or not runner:
			allowed = ", ".join(sorted(self.allowed_extensions))
			return f"脚本类型不支持: {ext or '<none>'}, allowed: {allowed}", "skill.extension_denied", False
		return ResolvedSkillScript(
			skill=skill,
			scripts_path=real_scripts_dir,
			script_path=real_script,
			runner=runner,
		)


class SkillScriptRunner:
	def __init__(self, timeout: int, stdout_limit: int, stderr_limit: int) -> None:
		self.timeout = timeout
		self.stdout_limit = stdout_limit
		self.stderr_limit = stderr_limit

	async def run(self, resolved: ResolvedSkillScript, request: SkillScriptRequest, context: dict) -> Any:
		executor = WorkspaceCommandExecutor(
			resolved.scripts_path,
			inner=context.get("command_executor") or LocalSubprocessExecutor(),
		)
		extra_args = shlex.split(request.script_args) if request.script_args else []
		return await executor.run(CommandSpec(
			argv=[resolved.runner, resolved.script_path, *extra_args],
			cwd=resolved.scripts_path,
			timeout=self.timeout,
			stdout_limit=self.stdout_limit,
			stderr_limit=self.stderr_limit,
		))


class SkillScriptPresenter:
	def __init__(self, plugin_ctx: Any, max_result_bytes: int) -> None:
		self.plugin_ctx = plugin_ctx
		self.max_result_bytes = max_result_bytes

	async def payload(
		self,
		request: SkillScriptRequest,
		result: Any,
		context: dict,
		started: float,
	) -> tuple[dict[str, Any], list[Any]]:
		stdout, stdout_ref = await _maybe_externalize_text(
			result.stdout,
			result_store_from_context(context, self.plugin_ctx),
			self.max_result_bytes,
			"skill_stdout",
			{"skill_name": request.skill_name, "script_name": request.script_name},
		)
		stderr, stderr_ref = await _maybe_externalize_text(
			result.stderr,
			result_store_from_context(context, self.plugin_ctx),
			self.max_result_bytes,
			"skill_stderr",
			{"skill_name": request.skill_name, "script_name": request.script_name},
		)
		payload = {
			"skill_name": request.skill_name,
			"script_name": request.script_name,
			"stdout": stdout,
			"stderr": stderr,
			"returncode": result.exit_code,
			"timed_out": result.timed_out,
			"duration_ms": int((time.time() - started) * 1000),
		}
		return payload, [ref for ref in (stdout_ref, stderr_ref) if ref]


class SkillAuditRecorder:
	async def record(
		self,
		exec_ctx: Any,
		event_type: str,
		tool_name: str,
		capability: str,
		risk_level: str,
		duration_ms: int,
		allowed: bool,
		metadata: dict[str, Any],
		error: ErrorEnvelope | None = None,
	) -> None:
		audit_sink = getattr(getattr(exec_ctx, "services", None), "audit_sink", None)
		if not audit_sink:
			return
		from axc_agent_engine.observability.audit import AuditEvent
		state_metadata = getattr(getattr(exec_ctx, "state", None), "metadata", {}) or {}
		agent_info = getattr(getattr(exec_ctx, "runtime", None), "agent_info", None)
		await audit_sink.record(AuditEvent(
			type=event_type,
			actor=str(state_metadata.get("user_id") or state_metadata.get("agent_name") or getattr(agent_info, "name", "") or ""),
			session_id=str(state_metadata.get("session_id") or getattr(agent_info, "session_id", "") or ""),
			tool_name=tool_name,
			capability=capability,
			risk_level=risk_level,
			allowed=allowed,
			duration_ms=duration_ms,
			error=error.to_dict() if error else {},
			metadata=metadata,
		))


class SkillPlugin(BasePlugin):
	name = "skill"
	display_name = "技能系统"
	priority = 20
	version = "2.0.0"
	config_schema = SKILL_CONFIG_SCHEMA

	def initialize(self, config: dict, plugin_ctx: "PluginContext") -> None:
		self._plugin_ctx = plugin_ctx
		self._paths = [str(path) for path in config.get("paths", [])]
		self._catalog_resource = "skill.catalog"
		self._allowed_skills = {str(name) for name in config.get("allowed_skills", [])}
		self._denied_skills = {str(name) for name in config.get("denied_skills", [])}
		self._allow_scripts = bool(config.get("allow_scripts", True))
		self._allowed_script_names = {_normalize_relpath(name) for name in config.get("allowed_script_names", [])}
		self._allowed_extensions = _normalize_extensions(config.get("allowed_extensions", list(_SCRIPT_RUNNERS)))
		self._duplicate_policy = str(config.get("duplicate_policy", "error")).lower()
		if self._duplicate_policy not in {"skip", "replace", "error"}:
			raise ValueError("skill.duplicate_policy must be one of skip, replace, error")
		self._timeout = strict_bounded_int(config.get("timeout", 60), 1, 3600, "skill.timeout")
		self._stdout_limit = strict_bounded_int(config.get("stdout_limit", 1500), 1, 10 * 1024 * 1024, "skill.stdout_limit")
		self._stderr_limit = strict_bounded_int(config.get("stderr_limit", 500), 1, 10 * 1024 * 1024, "skill.stderr_limit")
		self._max_skill_content_chars = strict_bounded_int(
			config.get("max_skill_content_chars", 100_000),
			1,
			10_000_000,
			"skill.max_skill_content_chars",
		)
		self._max_result_bytes = strict_bounded_int(
			config.get("max_result_bytes", 256_000),
			1,
			50 * 1024 * 1024,
			"skill.max_result_bytes",
		)
		self._skills: dict[str, dict] = {}
		self._load_errors: list[dict[str, str]] = []
		self._script_command_runner = SkillScriptRunner(self._timeout, self._stdout_limit, self._stderr_limit)
		self._script_presenter = SkillScriptPresenter(self._plugin_ctx, self._max_result_bytes)
		self._audit_recorder = SkillAuditRecorder()
		self._load_skills()

	def inject_context(self, exec_ctx: "ExecutionContext", topic: str = "") -> str:
		if not self._skills:
			return ""
		lines = ["## 可用技能", "以下技能可通过 load_skill 工具加载详细指令："]
		for name, skill in self._skills.items():
			desc = skill.get("description", "")
			when = skill.get("when_to_use", "")
			hint = f"（{when}）" if when else ""
			lines.append(f"- {name}: {desc}{hint}")
		return "\n".join(lines)

	def get_tools(self) -> list[ToolDefinition]:
		"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
提供 skill 查询、加载、状态、重载和脚本执行工具。"""
		return [
			ToolDefinition(
				name="list_skills",
				description="列出可用技能，可按关键词过滤",
				parameters={
					"type": "object",
					"properties": {
						"query": {"type": "string", "description": "关键词，可为空", "default": ""},
					},
				},
				is_read_only=True,
				capability="skill_read",
				risk_level="safe",
				execute=self._tool_list_skills,
			),
			ToolDefinition(
				name="load_skill",
				description="加载技能的详细指令内容",
				parameters={
					"type": "object",
					"properties": {
						"skill_name": {"type": "string", "description": "技能名称"},
					},
					"required": ["skill_name"],
				},
				is_read_only=True,
				capability="skill_read",
				risk_level="safe",
				execute=self._tool_load_skill,
			),
			ToolDefinition(
				name="skill_status",
				description="查看技能系统加载状态、配置和错误",
				parameters={"type": "object", "properties": {}},
				is_read_only=True,
				capability="skill_read",
				risk_level="safe",
				execute=self._tool_skill_status,
			),
			ToolDefinition(
				name="reload_skills",
				description="重新扫描并加载技能目录",
				parameters={"type": "object", "properties": {}},
				is_read_only=False,
				capability="skill_read",
				risk_level="moderate",
				execute=self._tool_reload_skills,
			),
			ToolDefinition(
				name="run_skill_script",
				description="执行技能目录下的脚本",
				parameters={
					"type": "object",
					"properties": {
						"skill_name": {"type": "string", "description": "技能名称"},
						"script_name": {"type": "string", "description": "脚本文件名"},
						"args": {"type": "string", "description": "脚本参数", "default": ""},
					},
					"required": ["skill_name", "script_name"],
				},
				is_read_only=False,
				capability="shell",
				risk_level="dangerous",
				execute=self._tool_run_script,
			),
		]

	def _load_skills(self) -> None:
		"""English: This documentation describes the related engine component behavior.
中文：扫描技能目录"""
		self._skills.clear()
		self._load_errors.clear()
		if not self._paths and not self._has_catalog_resource():
			raise ValueError("skill plugin requires paths or mounted skill.catalog")
		self._load_catalog_skills()
		for path in self._paths:
			real_path = os.path.realpath(path)
			if not os.path.isdir(real_path):
				raise FileNotFoundError(f"Skill directory not found: {path}")
			for entry in sorted(os.listdir(real_path)):
				skill_dir = os.path.join(real_path, entry)
				if not os.path.isdir(skill_dir):
					continue
				skill_md = _find_skill_markdown(skill_dir)
				if not skill_md:
					continue
				self._load_skill_dir(skill_dir, skill_md)
		if not self._skills:
			raise ValueError("skill plugin loaded no skills; check paths, skill.catalog, allowed_skills, and denied_skills")
		logger.info(f"[skill] Loaded {len(self._skills)} skills")

	def _load_catalog_skills(self) -> None:
		if not self._plugin_ctx or not self._catalog_resource:
			return
		catalog = self._plugin_ctx.resources.get(self._catalog_resource)
		if not catalog:
			return
		raw_skills = _catalog_skills(catalog)
		for raw_skill in raw_skills:
			self._load_catalog_skill(raw_skill)

	def _load_catalog_skill(self, raw_skill: Any) -> None:
		if not isinstance(raw_skill, dict):
			raise ValueError("skill.catalog item must be an object")
		name = str(raw_skill.get("name", "")).strip()
		if not name:
			raise ValueError("skill.catalog item name cannot be empty")
		if self._allowed_skills and name not in self._allowed_skills:
			return
		if name in self._denied_skills:
			return
		if name in self._skills:
			if self._duplicate_policy == "error":
				raise ValueError(f"Duplicate skill: {name}")
			if self._duplicate_policy == "skip":
				return
		trigger_keywords = raw_skill.get("trigger_keywords", [])
		if isinstance(trigger_keywords, str):
			trigger_keywords = [trigger_keywords]
		elif not isinstance(trigger_keywords, list):
			trigger_keywords = []
		content = str(raw_skill.get("content") or raw_skill.get("body") or "")
		scripts_path = str(raw_skill.get("scripts_path") or "")
		self._skills[name] = {
			"name": name,
			"description": str(raw_skill.get("description", "")),
			"when_to_use": str(raw_skill.get("when_to_use", "")),
			"trigger_keywords": [str(item) for item in trigger_keywords],
			"content": content,
			"content_length": len(content),
			"content_hash": str(raw_skill.get("content_hash") or hashlib.sha256(content.encode("utf-8")).hexdigest()),
			"version": str(raw_skill.get("version", "")),
			"author": str(raw_skill.get("author", "")),
			"source": str(raw_skill.get("source") or self._catalog_resource),
			"trusted": bool(raw_skill.get("trusted", False)),
			"skill_dir": str(raw_skill.get("skill_dir", "")),
			"skill_md": str(raw_skill.get("skill_md", "")),
			"scripts_path": scripts_path if scripts_path and os.path.isdir(scripts_path) else None,
			"scripts": _list_scripts(scripts_path, self._allowed_extensions) if scripts_path and os.path.isdir(scripts_path) else [],
		}

	def _load_skill_dir(self, skill_dir: str, skill_md: str) -> None:
		"""English: This documentation describes the related engine component behavior.
中文：加载单个技能目录"""
		with open(skill_md, "r", encoding="utf-8") as f:
			content = f.read()
		meta, body = _parse_frontmatter(content)
		name = str(meta.get("name", os.path.basename(skill_dir))).strip()
		if not name:
			raise ValueError(f"Skill name cannot be empty: {skill_dir}")
		if self._allowed_skills and name not in self._allowed_skills:
			return
		if name in self._denied_skills:
			return
		if name in self._skills:
			if self._duplicate_policy == "error":
				raise ValueError(f"Duplicate skill: {name}")
			if self._duplicate_policy == "skip":
				return
		scripts_path = os.path.join(skill_dir, "scripts")
		trigger_keywords = meta.get("trigger_keywords", [])
		if isinstance(trigger_keywords, str):
			trigger_keywords = [trigger_keywords]
		elif not isinstance(trigger_keywords, list):
			trigger_keywords = []
		self._skills[name] = {
			"name": name,
			"description": str(meta.get("description", "")),
			"when_to_use": str(meta.get("when_to_use", "")),
			"trigger_keywords": [str(item) for item in trigger_keywords],
			"content": body,
			"content_length": len(body),
			"content_hash": hashlib.sha256(body.encode("utf-8")).hexdigest(),
			"version": str(meta.get("version", "")),
			"author": str(meta.get("author", "")),
			"source": str(meta.get("source", "")),
			"trusted": bool(meta.get("trusted", False)),
			"skill_dir": os.path.realpath(skill_dir),
			"skill_md": os.path.realpath(skill_md),
			"scripts_path": scripts_path if os.path.isdir(scripts_path) else None,
			"scripts": _list_scripts(scripts_path, self._allowed_extensions) if os.path.isdir(scripts_path) else [],
		}

	async def _tool_list_skills(self, args: dict, context: dict):
		"""list_skills 工具实现"""
		from axc_agent_engine.tools.tool_output import ToolOutput
		exec_ctx = exec_ctx_from_tool_context(context)
		self._sync_metadata(exec_ctx, "list")
		query = str(args.get("query", "")).strip().lower()
		skills = []
		for skill in self._skills.values():
			keywords = skill.get("trigger_keywords", [])
			haystack = " ".join([
				skill.get("name", ""),
				skill.get("description", ""),
				skill.get("when_to_use", ""),
				" ".join(keywords) if isinstance(keywords, list) else str(keywords),
			]).lower()
			if query and query not in haystack:
				continue
			skills.append({
				"name": skill["name"],
				"description": skill["description"],
				"when_to_use": skill["when_to_use"],
				"trigger_keywords": keywords,
				"has_scripts": bool(skill.get("scripts_path")),
				"scripts": skill.get("scripts", []),
				"version": skill.get("version", ""),
				"source": skill.get("source", ""),
				"trusted": skill.get("trusted", False),
				"content_hash": skill.get("content_hash", ""),
			})
		return ToolOutput.json_output({
			"skills": skills,
			"total": len(skills),
			"errors": list(self._load_errors),
		}, summary=f"找到 {len(skills)} 个 skill")

	async def _tool_load_skill(self, args: dict, context: dict):
		"""load_skill 工具实现"""
		from axc_agent_engine.tools.tool_output import ToolOutput
		started = time.time()
		exec_ctx = exec_ctx_from_tool_context(context)
		skill_name = args.get("skill_name", "")
		if skill_name not in self._skills:
			available = list(self._skills.keys())
			await self._audit(
				exec_ctx,
				"skill_load_rejected",
				"load_skill",
				"skill_read",
				"safe",
				int((time.time() - started) * 1000),
				False,
				{"skill_name": skill_name, "available": available},
				_error_envelope("skill.not_found", f"技能 '{skill_name}' 不存在", {"available": available}),
			)
			return ToolOutput.error(f"技能 '{skill_name}' 不存在, available: {available}")
		skill = self._skills[skill_name]
		content, artifact = await _maybe_externalize_text(
			skill["content"],
			result_store_from_context(context, self._plugin_ctx),
			self._max_skill_content_chars,
			"skill_content",
			{"skill_name": skill_name, "content_hash": skill.get("content_hash", "")},
		)
		payload = {
			"name": skill["name"],
			"description": skill["description"],
			"when_to_use": skill.get("when_to_use", ""),
			"trigger_keywords": skill.get("trigger_keywords", []),
			"version": skill.get("version", ""),
			"author": skill.get("author", ""),
			"source": skill.get("source", ""),
			"trusted": skill.get("trusted", False),
			"content_hash": skill.get("content_hash", ""),
			"content_length": skill.get("content_length", 0),
			"content": content,
		}
		artifacts = [artifact] if artifact else []
		self._sync_metadata(exec_ctx, "load", skill_name)
		await self._audit(
			exec_ctx,
			"skill_loaded",
			"load_skill",
			"skill_read",
			"safe",
			int((time.time() - started) * 1000),
			True,
			{"skill_name": skill_name, "artifact_id": artifact.id if artifact else ""},
		)
		return ToolOutput(
			content=payload,
			content_type="json",
			summary=f"已加载 skill：{skill_name}",
			artifacts=artifacts,
			metadata={"skill_name": skill_name, "capability": "skill_read", "risk_level": "safe"},
		)

	async def _tool_skill_status(self, args: dict, context: dict):
		"""skill_status 工具实现"""
		from axc_agent_engine.tools.tool_output import ToolOutput
		exec_ctx = exec_ctx_from_tool_context(context)
		self._sync_metadata(exec_ctx, "status")
		return ToolOutput.json_output(self._status_payload(), summary=f"已加载 {len(self._skills)} 个 skill")

	async def _tool_reload_skills(self, args: dict, context: dict):
		"""reload_skills 工具实现"""
		from axc_agent_engine.tools.tool_output import ToolOutput
		started = time.time()
		exec_ctx = exec_ctx_from_tool_context(context)
		self._load_skills()
		self._sync_metadata(exec_ctx, "reload")
		payload = self._status_payload()
		await self._audit(
			exec_ctx,
			"skills_reloaded",
			"reload_skills",
			"skill_read",
			"moderate",
			int((time.time() - started) * 1000),
			True,
			{"loaded": len(self._skills), "errors": len(self._load_errors)},
		)
		return ToolOutput.json_output(payload, summary=f"已重新加载 {len(self._skills)} 个 skill")

	async def _tool_run_script(self, args: dict, context: dict):
		"""run_skill_script 工具实现"""
		from axc_agent_engine.tools.tool_output import ToolOutput
		started = time.time()
		exec_ctx = exec_ctx_from_tool_context(context)
		request = self._script_request(args)
		resolved_or_error = await self._resolve_script_request(request, exec_ctx, started)
		if not isinstance(resolved_or_error, ResolvedSkillScript):
			return resolved_or_error
		resolved = resolved_or_error
		try:
			result = await self._script_command_runner.run(resolved, request, context)
			payload, artifacts = await self._script_success_payload(request, result, context, started)
			self._sync_metadata(exec_ctx, "run_script", request.skill_name)
			error = None
			if result.exit_code != 0 or result.timed_out:
				error = _error_envelope(
					"skill.script_failed",
					"技能脚本执行失败",
					{"returncode": result.exit_code, "timed_out": result.timed_out},
				)
			await self._audit(
				exec_ctx,
				"skill_script_executed",
				"run_skill_script",
				"shell",
				"dangerous",
				payload["duration_ms"],
				True,
				{
					"skill_name": request.skill_name,
					"script_name": request.script_name,
					"returncode": result.exit_code,
					"timed_out": result.timed_out,
					"artifact_ids": [ref.id for ref in artifacts],
				},
				error,
			)
			return ToolOutput(
				content=payload,
				content_type="json",
				summary=f"脚本 {request.script_name}：exit_code={result.exit_code}",
				artifacts=artifacts,
				metadata={"skill_name": request.skill_name, "script_name": request.script_name, "capability": "shell", "risk_level": "dangerous"},
			)
		except ValueError as e:
			return await self._script_error(
				f"脚本参数不合法: {e}",
				exec_ctx,
				started,
				request.skill_name,
				request.script_name,
				"skill.bad_args",
				allowed=False,
			)
		except Exception as e:
			return await self._script_error(
				str(e),
				exec_ctx,
				started,
				request.skill_name,
				request.script_name,
				"skill.execution_error",
				allowed=True,
			)

	def _script_request(self, args: dict) -> SkillScriptRequest:
		return SkillScriptRequest(
			skill_name=str(args.get("skill_name", "")),
			script_name=_normalize_relpath(args.get("script_name", "")),
			script_args=args.get("args", ""),
		)

	async def _script_success_payload(
		self,
		request: SkillScriptRequest,
		result: Any,
		context: dict,
		started: float,
	) -> tuple[dict[str, Any], list[Any]]:
		return await self._script_presenter.payload(request, result, context, started)

	async def _resolve_script_request(
		self,
		request: SkillScriptRequest,
		exec_ctx: Any,
		started: float,
	) -> ResolvedSkillScript | Any:
		policy = SkillScriptPolicy(
			self._skills,
			self._allow_scripts,
			self._allowed_script_names,
			self._allowed_extensions,
		)
		resolved = policy.resolve(request)
		if isinstance(resolved, ResolvedSkillScript):
			return resolved
		message, code, allowed = resolved
		return await self._script_error(message, exec_ctx, started, request.skill_name, request.script_name, code, allowed=allowed)

	async def _script_error(self, message: str, exec_ctx: Any, started: float, skill_name: str,
							script_name: str, code: str, allowed: bool) -> Any:
		from axc_agent_engine.tools.tool_output import ToolOutput
		await self._audit(
			exec_ctx,
			"skill_script_rejected" if not allowed else "skill_script_failed",
			"run_skill_script",
			"shell",
			"dangerous",
			int((time.time() - started) * 1000),
			allowed,
			{"skill_name": skill_name, "script_name": script_name},
			_error_envelope(code, message),
		)
		return ToolOutput.error(message)

	async def _audit(self, exec_ctx: Any, event_type: str, tool_name: str, capability: str,
					 risk_level: str, duration_ms: int, allowed: bool, metadata: dict[str, Any],
					 error: ErrorEnvelope | None = None) -> None:
		await self._audit_recorder.record(
			exec_ctx,
			event_type,
			tool_name,
			capability,
			risk_level,
			duration_ms,
			allowed,
			metadata,
			error,
		)

	def _sync_metadata(self, exec_ctx: Any, action: str, skill_name: str = "") -> None:
		if not exec_ctx:
			return
		exec_ctx.state.metadata["skill"] = {
			"last_action": action,
			"last_skill": skill_name,
			"loaded": len(self._skills),
			"errors": len(self._load_errors),
			"allow_scripts": self._allow_scripts,
		}

	def _status_payload(self) -> dict[str, Any]:
		return {
			"loaded": len(self._skills),
			"paths": list(self._paths),
			"errors": list(self._load_errors),
			"config": {
				"allow_scripts": self._allow_scripts,
				"allowed_skills": sorted(self._allowed_skills),
				"denied_skills": sorted(self._denied_skills),
				"allowed_script_names": sorted(self._allowed_script_names),
				"allowed_extensions": sorted(self._allowed_extensions),
				"duplicate_policy": self._duplicate_policy,
				"timeout": self._timeout,
				"stdout_limit": self._stdout_limit,
				"stderr_limit": self._stderr_limit,
				"max_skill_content_chars": self._max_skill_content_chars,
				"max_result_bytes": self._max_result_bytes,
			},
			"skills": [_skill_public_metadata(skill) for skill in self._skills.values()],
		}

	def _has_catalog_resource(self) -> bool:
		return bool(self._plugin_ctx and self._plugin_ctx.resources.get(self._catalog_resource))


def _find_skill_markdown(skill_dir: str) -> str:
	for filename in _SKILL_FILENAMES:
		path = os.path.join(skill_dir, filename)
		if os.path.exists(path):
			return path
	return ""


def _parse_frontmatter(content: str) -> tuple[dict, str]:
	"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
解析 Markdown frontmatter（YAML 格式）"""
	if not content.startswith("---"):
		return {}, content
	parts = content.split("---", 2)
	if len(parts) < 3:
		return {}, content
	try:
		import yaml
		meta = yaml.safe_load(parts[1]) or {}
	except Exception as exc:
		raise ValueError("Skill frontmatter YAML is invalid") from exc
	if not isinstance(meta, dict):
		raise ValueError("Skill frontmatter must be an object")
	body = parts[2].strip()
	return meta, body


def _skill_public_metadata(skill: dict[str, Any]) -> dict[str, Any]:
	return {
		"name": skill.get("name", ""),
		"description": skill.get("description", ""),
		"when_to_use": skill.get("when_to_use", ""),
		"trigger_keywords": skill.get("trigger_keywords", []),
		"version": skill.get("version", ""),
		"author": skill.get("author", ""),
		"source": skill.get("source", ""),
		"trusted": skill.get("trusted", False),
		"content_hash": skill.get("content_hash", ""),
		"content_length": skill.get("content_length", 0),
		"skill_dir": skill.get("skill_dir", ""),
		"skill_md": skill.get("skill_md", ""),
		"has_scripts": bool(skill.get("scripts_path")),
		"scripts": skill.get("scripts", []),
	}


def _catalog_skills(catalog: Any) -> list[Any]:
	if isinstance(catalog, dict):
		skills = catalog.get("skills", catalog)
		return list(skills.values()) if isinstance(skills, dict) else list(skills or [])
	if isinstance(catalog, (list, tuple)):
		return list(catalog)
	list_skills = getattr(catalog, "list_skills", None)
	if callable(list_skills):
		return list(list_skills())
	skills = getattr(catalog, "skills", None)
	if isinstance(skills, dict):
		return list(skills.values())
	if isinstance(skills, (list, tuple)):
		return list(skills)
	raise TypeError("skill.catalog must be a dict, sequence, list_skills() provider, or expose skills")


def _list_scripts(scripts_path: str, allowed_extensions: set[str]) -> list[dict[str, Any]]:
	scripts = []
	for root, _, files in os.walk(scripts_path):
		for filename in sorted(files):
			path = os.path.join(root, filename)
			relpath = os.path.relpath(path, scripts_path)
			ext = os.path.splitext(filename)[1].lower()
			scripts.append({
				"name": relpath,
				"extension": ext,
				"supported": ext in _SCRIPT_RUNNERS,
				"allowed": ext in allowed_extensions and ext in _SCRIPT_RUNNERS,
				"size": os.path.getsize(path),
				"sha256": _file_sha256(path),
			})
	return scripts


def _file_sha256(path: str) -> str:
	hasher = hashlib.sha256()
	with open(path, "rb") as f:
		for chunk in iter(lambda: f.read(1024 * 1024), b""):
			hasher.update(chunk)
	return hasher.hexdigest()


async def _maybe_externalize_text(content: str, result_store: Any, threshold: int, kind: str,
								  metadata: dict[str, Any]) -> tuple[Any, Any]:
	return await externalize_text(content, result_store, threshold, {**metadata, "kind": kind}, logger, "skill", threshold)


def _normalize_relpath(value: Any) -> str:
	path = os.path.normpath(str(value or "")).replace("\\", "/")
	if path == ".":
		return ""
	return path.lstrip("/")


def _normalize_extensions(values: Any) -> set[str]:
	if not isinstance(values, (list, tuple, set)):
		values = list(_SCRIPT_RUNNERS)
	extensions = set()
	for value in values:
		ext = str(value or "").strip().lower()
		if not ext:
			continue
		if not ext.startswith("."):
			ext = f".{ext}"
		if ext in _SCRIPT_RUNNERS:
			extensions.add(ext)
	return extensions or set(_SCRIPT_RUNNERS)


def _error_envelope(code: str, message: str, details: dict[str, Any] | None = None) -> ErrorEnvelope:
	return ErrorEnvelope(
		code=code,
		message=message,
		category=ErrorCategory.TOOL,
		retryable=False,
		details=details or {},
	)
