"""Builtin tool definitions.
中文：此文档说明相关引擎组件的行为。"""
from datetime import datetime, timezone
from typing import Any

import httpx

from axc_agent_engine.runtime.sandbox_models import CommandResult
from axc_agent_engine.tools.tool_output import ToolOutput

from .command_tools import BuiltinCommandTools, ensure_venv, get_command_executor, store_command_artifacts
from .file_tools import FILE_READ_LINE_WINDOW, FILE_READ_MAX_SIZE, BuiltinFileTools
from .http_tools import (
	DEFAULT_HTTP_MAX_BYTES,
	MAX_HTTP_BYTES,
	MAX_TOOL_TIMEOUT,
	BuiltinHttpPolicy,
	BuiltinHttpTools,
	is_blocked_ip,
	resolve_host_ips,
)
from .path_policy import BuiltinPathPolicy
from .presenter import BuiltinCommandPresenter
from .registry import ALL_TOOLS, DEFAULT_TOOLS, register_tool
from .result_store import ResultStoreReader
from .result_tools import BuiltinResultTools
from .support import bounded_int, truncate_by_bytes

_DEFAULT_TOOLS = DEFAULT_TOOLS
_ALL_TOOLS = ALL_TOOLS
_FILE_READ_LINE_WINDOW = FILE_READ_LINE_WINDOW
_FILE_READ_MAX_SIZE = FILE_READ_MAX_SIZE
_MAX_TOOL_TIMEOUT = MAX_TOOL_TIMEOUT
_DEFAULT_HTTP_MAX_BYTES = DEFAULT_HTTP_MAX_BYTES
_MAX_HTTP_BYTES = MAX_HTTP_BYTES

_PATH_POLICY = BuiltinPathPolicy()
_HTTP_POLICY = BuiltinHttpPolicy()
_RESULT_READER = ResultStoreReader()
_COMMAND_PRESENTER = BuiltinCommandPresenter(_RESULT_READER)
_FILE_TOOLS = BuiltinFileTools(_PATH_POLICY, _RESULT_READER)
_HTTP_TOOLS = BuiltinHttpTools(httpx, _HTTP_POLICY, _RESULT_READER)
_COMMAND_TOOLS = BuiltinCommandTools(_PATH_POLICY, _COMMAND_PRESENTER)
_RESULT_TOOLS = BuiltinResultTools(_RESULT_READER)


def _get_workspace(context: dict, tool_name: str) -> str | ToolOutput:
	return _PATH_POLICY.get_workspace(context, tool_name)


def _resolve_workspace_path(path: str, context: dict) -> str:
	return _PATH_POLICY.resolve_workspace_path(path, context)


async def _ensure_venv(venv_dir: str, context: dict) -> str:
	return await ensure_venv(venv_dir, context)


def _get_result_store(context: dict):
	return _RESULT_READER.store(context)


def _get_command_executor(context: dict):
	return get_command_executor(context)


def _is_blocked_ip(ip: str) -> bool:
	return is_blocked_ip(ip)


def _resolve_host_ips(hostname: str) -> list[str]:
	return resolve_host_ips(hostname)


async def _validate_http_url(url: str) -> str | None:
	return await _HTTP_POLICY.validate_url(url)


async def _store_command_artifacts(
	result: CommandResult,
	context: dict,
	stdout_limit: int = 1500,
	stderr_limit: int = 500,
):
	return await store_command_artifacts(result, context, stdout_limit, stderr_limit)


def _register_tool(name: str, description: str, parameters: dict, is_read_only: bool = False, capability: str = ""):
	return register_tool(name, description, parameters, is_read_only, capability)


@_register_tool(
	"get_time",
	"获取当前时间",
	{"type": "object", "properties": {"timezone": {"type": "string", "description": "时区名称", "default": "UTC"}}},
	is_read_only=True,
)
async def _get_time(args: dict, context: dict) -> ToolOutput:
	now = datetime.now(timezone.utc)
	data = {"utc": now.isoformat(), "timezone": args.get("timezone", "UTC")}
	return ToolOutput.json_output(data, summary=f"当前时间：{now.isoformat()}")


@_register_tool(
	"file_read",
	"读取文件内容（返回行窗口，完整内容写入 artifact）",
	{"type": "object", "properties": {
		"path": {"type": "string", "description": "文件路径"},
		"start_line": {"type": "integer", "description": "起始行（从 1 开始）", "default": 1},
		"end_line": {"type": "integer", "description": "结束行（0 表示自动窗口）", "default": 0},
	}, "required": ["path"]},
	is_read_only=True,
	capability="file_read",
)
async def _file_read(args: dict, context: dict) -> ToolOutput:
	return await _FILE_TOOLS.read(args, context)


@_register_tool(
	"file_list",
	"列出 workspace 内目录项",
	{"type": "object", "properties": {
		"path": {"type": "string", "description": "目录路径", "default": "."},
		"recursive": {"type": "boolean", "description": "是否递归列出", "default": False},
		"limit": {"type": "integer", "description": "最多返回条目数", "default": 200},
	}, "required": []},
	is_read_only=True,
	capability="file_read",
)
async def _file_list(args: dict, context: dict) -> ToolOutput:
	return await _FILE_TOOLS.list(args, context)


@_register_tool(
	"file_glob",
	"按 glob 模式搜索 workspace 内文件",
	{"type": "object", "properties": {
		"pattern": {"type": "string", "description": "glob 模式，如 **/*.py"},
		"limit": {"type": "integer", "description": "最多返回条目数", "default": 200},
		"include_dirs": {"type": "boolean", "description": "是否包含目录", "default": False},
	}, "required": ["pattern"]},
	is_read_only=True,
	capability="file_read",
)
async def _file_glob(args: dict, context: dict) -> ToolOutput:
	return await _FILE_TOOLS.glob(args, context)


@_register_tool(
	"file_info",
	"查看 workspace 内文件或目录元信息",
	{"type": "object", "properties": {"path": {"type": "string", "description": "文件或目录路径"}}, "required": ["path"]},
	is_read_only=True,
	capability="file_read",
)
async def _file_info(args: dict, context: dict) -> ToolOutput:
	return await _FILE_TOOLS.info(args, context)


def _file_entry(full_path: str, context: dict) -> dict[str, Any]:
	return _PATH_POLICY.file_entry(full_path, context)


@_register_tool(
	"file_write",
	"写入文件内容",
	{"type": "object", "properties": {
		"path": {"type": "string", "description": "文件路径"},
		"content": {"type": "string", "description": "文件内容"},
	}, "required": ["path", "content"]},
	capability="file_write",
)
async def _file_write(args: dict, context: dict) -> ToolOutput:
	return await _FILE_TOOLS.write(args, context)


@_register_tool(
	"file_append",
	"向文件末尾追加内容",
	{"type": "object", "properties": {
		"path": {"type": "string", "description": "文件路径"},
		"content": {"type": "string", "description": "追加内容"},
		"create": {"type": "boolean", "description": "文件不存在时是否创建", "default": True},
	}, "required": ["path", "content"]},
	capability="file_write",
)
async def _file_append(args: dict, context: dict) -> ToolOutput:
	return await _FILE_TOOLS.append(args, context)


@_register_tool(
	"file_edit",
	"精确替换文件中的字符串",
	{"type": "object", "properties": {
		"path": {"type": "string", "description": "文件路径"},
		"old_string": {"type": "string", "description": "要替换的原始文本"},
		"new_string": {"type": "string", "description": "新文本"},
		"replace_all": {"type": "boolean", "description": "是否替换所有匹配项", "default": False},
	}, "required": ["path", "old_string", "new_string"]},
	capability="file_write",
)
async def _file_edit(args: dict, context: dict) -> ToolOutput:
	return await _FILE_TOOLS.edit(args, context)


@_register_tool(
	"http_request",
	"发送 HTTP 请求",
	{"type": "object", "properties": {
		"url": {"type": "string", "description": "请求 URL"},
		"method": {"type": "string", "description": "HTTP 方法", "default": "GET"},
		"headers": {"type": "object", "description": "请求头"},
		"body": {"description": "请求体"},
		"timeout": {"type": "integer", "description": "请求超时时间（秒）", "default": 30},
		"max_bytes": {"type": "integer", "description": "响应预览最大字节数", "default": _DEFAULT_HTTP_MAX_BYTES},
	}, "required": ["url"]},
	capability="http_request",
)
async def _http_request(args: dict, context: dict) -> ToolOutput:
	return await _HTTP_TOOLS.request(args, context)


@_register_tool(
	"python_exec",
	"执行 Python 代码并返回结果",
	{"type": "object", "properties": {
		"code": {"type": "string", "description": "要执行的 Python 代码"},
		"timeout": {"type": "integer", "description": "执行超时时间（秒）", "default": 30},
	}, "required": ["code"]},
	capability="python_exec",
)
async def _python_exec(args: dict, context: dict) -> ToolOutput:
	return await _COMMAND_TOOLS.python_exec(args, context)


@_register_tool(
	"shell",
	"执行 shell 命令",
	{"type": "object", "properties": {
		"command": {"type": "string", "description": "Shell 命令"},
		"shell_type": {"type": "string", "description": "Shell 类型，可选 auto、bash、powershell", "default": "auto"},
		"timeout": {"type": "integer", "description": "执行超时时间（秒）", "default": 60},
	}, "required": ["command"]},
	capability="shell",
)
async def _shell(args: dict, context: dict) -> ToolOutput:
	return await _COMMAND_TOOLS.shell(args, context)


def _bounded_int(value: Any, minimum: int, maximum: int, default: int) -> int:
	return bounded_int(value, minimum, maximum, default)


def _truncate_by_bytes(text: str, max_bytes: int) -> str:
	return truncate_by_bytes(text, max_bytes)


@_register_tool(
	"pip_install",
	"安装 Python 包",
	{"type": "object", "properties": {"package": {"type": "string", "description": "包名"}}, "required": ["package"]},
	capability="pip_install",
)
async def _pip_install(args: dict, context: dict) -> ToolOutput:
	return await _COMMAND_TOOLS.pip_install(args, context)


@_register_tool(
	"result_read",
	"按 artifact ID 分页读取已存储的工具结果",
	{"type": "object", "properties": {
		"artifact_id": {"type": "string", "description": "上一次工具结果中的附件 ID"},
		"offset": {"type": "integer", "description": "字符偏移量", "default": 0},
		"limit": {"type": "integer", "description": "最多返回字符数", "default": 4000},
	}, "required": ["artifact_id"]},
	is_read_only=True,
)
async def _result_read(args: dict, context: dict) -> ToolOutput:
	return await _RESULT_TOOLS.read(args, context)


@_register_tool(
	"result_search",
	"在已存储的工具结果中搜索",
	{"type": "object", "properties": {
		"artifact_id": {"type": "string", "description": "附件 ID 标识"},
		"query": {"type": "string", "description": "搜索关键词"},
	}, "required": ["artifact_id", "query"]},
	is_read_only=True,
)
async def _result_search(args: dict, context: dict) -> ToolOutput:
	return await _RESULT_TOOLS.search(args, context)


@_register_tool(
	"result_page",
	"读取已存储工具结果的一页",
	{"type": "object", "properties": {
		"artifact_id": {"type": "string", "description": "附件 ID 标识"},
		"page": {"type": "integer", "description": "页码（从 1 开始）", "default": 1},
		"page_size": {"type": "integer", "description": "每页字符数", "default": 4000},
	}, "required": ["artifact_id"]},
	is_read_only=True,
)
async def _result_page(args: dict, context: dict) -> ToolOutput:
	return await _RESULT_TOOLS.page(args, context)
