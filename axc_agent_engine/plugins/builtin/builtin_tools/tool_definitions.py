"""Builtin tool definitions. 内置工具定义。"""

from datetime import UTC, datetime

import httpx

from axc_agent_engine.core.schema import ToolDefinition
from axc_agent_engine.tools.tool_output import ToolOutput

from .artifact_store import ArtifactStoreReader
from .artifact_tools import BuiltinArtifactTools
from .command_tools import BuiltinCommandTools
from .file_tools import BuiltinFileTools
from .http_tools import (
	BuiltinHttpPolicy,
	BuiltinHttpTools,
)
from .path_policy import BuiltinPathPolicy
from .presenter import BuiltinCommandPresenter

_PATH_POLICY = BuiltinPathPolicy()
_HTTP_POLICY = BuiltinHttpPolicy()
_ARTIFACT_READER = ArtifactStoreReader()
_COMMAND_PRESENTER = BuiltinCommandPresenter(_ARTIFACT_READER)
_FILE_TOOLS = BuiltinFileTools(_PATH_POLICY, _ARTIFACT_READER)
_HTTP_TOOLS = BuiltinHttpTools(httpx, _HTTP_POLICY, _ARTIFACT_READER)
_COMMAND_TOOLS = BuiltinCommandTools(_PATH_POLICY, _COMMAND_PRESENTER)
_ARTIFACT_TOOLS = BuiltinArtifactTools(_ARTIFACT_READER)


def _tool(
	name: str,
	description: str,
	parameters: dict,
	execute,
	is_read_only: bool = False,
	capability: str = "",
) -> ToolDefinition:
	return ToolDefinition(
		name=name,
		description=description,
		parameters=parameters,
		execute=execute,
		is_read_only=is_read_only,
		capability=capability,
	)


async def _get_time(args: dict, context: dict) -> ToolOutput:
	now = datetime.now(UTC)
	data = {"utc": now.isoformat(), "timezone": args.get("timezone", "UTC")}
	return ToolOutput.json_output(data, summary=f"当前时间：{now.isoformat()}")


async def _file_read(args: dict, context: dict) -> ToolOutput:
	return await _FILE_TOOLS.read(args, context)


async def _file_list(args: dict, context: dict) -> ToolOutput:
	return await _FILE_TOOLS.list(args, context)


async def _file_glob(args: dict, context: dict) -> ToolOutput:
	return await _FILE_TOOLS.glob(args, context)


async def _file_tree(args: dict, context: dict) -> ToolOutput:
	return await _FILE_TOOLS.tree(args, context)


async def _file_info(args: dict, context: dict) -> ToolOutput:
	return await _FILE_TOOLS.info(args, context)


async def _file_write(args: dict, context: dict) -> ToolOutput:
	return await _FILE_TOOLS.write(args, context)


async def _file_append(args: dict, context: dict) -> ToolOutput:
	return await _FILE_TOOLS.append(args, context)


async def _file_edit(args: dict, context: dict) -> ToolOutput:
	return await _FILE_TOOLS.edit(args, context)


async def _http_request(args: dict, context: dict) -> ToolOutput:
	return await _HTTP_TOOLS.request(args, context)


async def _python_exec(args: dict, context: dict) -> ToolOutput:
	return await _COMMAND_TOOLS.python_exec(args, context)


async def _shell(args: dict, context: dict) -> ToolOutput:
	return await _COMMAND_TOOLS.shell(args, context)


async def _pip_install(args: dict, context: dict) -> ToolOutput:
	return await _COMMAND_TOOLS.pip_install(args, context)


async def _artifact_read(args: dict, context: dict) -> ToolOutput:
	return await _ARTIFACT_TOOLS.read(args, context)


async def _artifact_search(args: dict, context: dict) -> ToolOutput:
	return await _ARTIFACT_TOOLS.search(args, context)


async def _artifact_page(args: dict, context: dict) -> ToolOutput:
	return await _ARTIFACT_TOOLS.page(args, context)


def builtin_tool_definitions() -> dict[str, ToolDefinition]:
	tools = [
		_tool(
			"get_time",
			"获取当前时间",
			{
				"type": "object",
				"properties": {"timezone": {"type": "string", "description": "时区名称", "default": "UTC"}},
			},
			_get_time,
			is_read_only=True,
		),
		_tool(
			"file_read",
			"读取文件内容，默认返回完整文件；可用 ranges/start_line/end_line 显式读取片段",
			{
				"type": "object",
				"properties": {
					"path": {"type": "string", "description": "文件路径"},
					"start_line": {"type": "integer", "description": "起始行（从 1 开始）", "default": 1},
					"end_line": {"type": "integer", "description": "结束行（0 表示文件末尾）", "default": 0},
					"ranges": {
						"type": "array",
						"description": "多个行号区间，如 [[1, 50], [800, 850]]；存在时优先于 start_line/end_line",
						"items": {
							"type": "array",
							"items": {"type": "integer"},
							"minItems": 2,
							"maxItems": 2,
						},
					},
				},
				"required": ["path"],
			},
			_file_read,
			is_read_only=True,
			capability="file_read",
		),
		_tool(
			"file_list",
			"列出 workspace 内目录项",
			{
				"type": "object",
				"properties": {
					"path": {"type": "string", "description": "目录路径", "default": "."},
					"recursive": {"type": "boolean", "description": "是否递归列出", "default": False},
				},
				"required": [],
			},
			_file_list,
			is_read_only=True,
			capability="file_read",
		),
		_tool(
			"file_grep",
			"通过 ripgrep 在工作区中搜索文本，返回路径、行号和匹配行。",
			{
				"type": "object",
				"properties": {"pattern": {"type": "string", "description": "正则表达式"}},
				"required": ["pattern"],
			},
			_COMMAND_TOOLS.grep,
			is_read_only=True,
			capability="file_read",
		),
		_tool(
			"file_glob",
			"按 glob 模式搜索 workspace 内文件",
			{
				"type": "object",
				"properties": {
					"pattern": {"type": "string", "description": "glob 模式，如 **/*.py"},
					"include_dirs": {"type": "boolean", "description": "是否包含目录", "default": False},
				},
				"required": ["pattern"],
			},
			_file_glob,
			is_read_only=True,
			capability="file_read",
		),
		_tool(
			"file_tree",
			"按目录层级展示 workspace 内项目骨架",
			{
				"type": "object",
				"properties": {
					"path": {"type": "string", "description": "目录路径", "default": "."},
					"max_depth": {"type": "integer", "description": "最大递归深度", "default": 3},
					"ignore": {
						"type": "array",
						"description": "忽略的目录或文件名；默认忽略 node_modules、.git、dist、build、venv 等",
						"items": {"type": "string"},
					},
				},
				"required": [],
			},
			_file_tree,
			is_read_only=True,
			capability="file_read",
		),
		_tool(
			"file_info",
			"查看 workspace 内文件或目录元信息",
			{
				"type": "object",
				"properties": {"path": {"type": "string", "description": "文件或目录路径"}},
				"required": ["path"],
			},
			_file_info,
			is_read_only=True,
			capability="file_read",
		),
		_tool(
			"file_write",
			"写入文件内容",
			{
				"type": "object",
				"properties": {
					"path": {"type": "string", "description": "文件路径"},
					"expected_sha256": {"type": "string", "description": "file_read 返回的版本；文件变化时拒绝修改"},
					"content": {"type": "string", "description": "文件内容"},
				},
				"required": ["path", "content"],
			},
			_file_write,
			capability="file_write",
		),
		_tool(
			"file_append",
			"向文件末尾追加内容",
			{
				"type": "object",
				"properties": {
					"path": {"type": "string", "description": "文件路径"},
					"expected_sha256": {"type": "string", "description": "file_read 返回的版本；文件变化时拒绝修改"},
					"content": {"type": "string", "description": "追加内容"},
					"create": {"type": "boolean", "description": "文件不存在时是否创建", "default": True},
				},
				"required": ["path", "content"],
			},
			_file_append,
			capability="file_write",
		),
		_tool(
			"file_edit",
			"精确替换文件中的字符串",
			{
				"type": "object",
				"properties": {
					"path": {"type": "string", "description": "文件路径"},
					"expected_sha256": {"type": "string", "description": "file_read 返回的版本；文件变化时拒绝修改"},
					"old_string": {"type": "string", "description": "要替换的原始文本"},
					"new_string": {"type": "string", "description": "新文本"},
					"replace_all": {"type": "boolean", "description": "是否替换所有匹配项", "default": False},
				},
				"required": ["path", "old_string", "new_string"],
			},
			_file_edit,
			capability="file_write",
		),
		_tool(
			"http_request",
			"发送 HTTP 请求",
			{
				"type": "object",
				"properties": {
					"url": {"type": "string", "description": "请求 URL"},
					"method": {"type": "string", "description": "HTTP 方法", "default": "GET"},
					"headers": {"type": "object", "description": "请求头"},
					"body": {"description": "请求体"},
					"timeout": {"type": "integer", "description": "请求超时时间（秒）", "default": 30},
				},
				"required": ["url"],
			},
			_http_request,
			capability="http_request",
		),
		_tool(
			"python_exec",
			"执行 Python 代码并返回结果",
			{
				"type": "object",
				"properties": {
					"code": {"type": "string", "description": "要执行的 Python 代码"},
					"timeout": {"type": "integer", "description": "执行超时时间（秒）", "default": 30},
				},
				"required": ["code"],
			},
			_python_exec,
			capability="python_exec",
		),
		_tool(
			"shell",
			"执行 shell 命令",
			{
				"type": "object",
				"properties": {
					"command": {"type": "string", "description": "Shell 命令"},
					"shell_type": {
						"type": "string",
						"description": "Shell 类型，可选 auto、bash、powershell",
						"default": "auto",
					},
					"timeout": {"type": "integer", "description": "执行超时时间（秒）", "default": 60},
				},
				"required": ["command"],
			},
			_shell,
			capability="shell",
		),
		_tool(
			"pip_install",
			"安装 Python 包",
			{
				"type": "object",
				"properties": {"package": {"type": "string", "description": "包名"}},
				"required": ["package"],
			},
			_pip_install,
			capability="pip_install",
		),
		_tool(
			"artifact_read",
			"按 artifact_id 和字符偏移读取 artifact 内容",
			{
				"type": "object",
				"properties": {
					"artifact_id": {"type": "string", "description": "上一次工具结果中的附件 ID"},
					"offset": {"type": "integer", "description": "字符偏移量", "default": 0},
					"limit": {"type": "integer", "description": "最多返回字符数", "default": 4000},
				},
				"required": ["artifact_id"],
			},
			_artifact_read,
			is_read_only=True,
		),
		_tool(
			"artifact_search",
			"在 artifact 内容中搜索",
			{
				"type": "object",
				"properties": {
					"artifact_id": {"type": "string", "description": "附件 ID 标识"},
					"query": {"type": "string", "description": "搜索关键词"},
				},
				"required": ["artifact_id", "query"],
			},
			_artifact_search,
			is_read_only=True,
		),
		_tool(
			"artifact_page",
			"按页读取 artifact 内容",
			{
				"type": "object",
				"properties": {
					"artifact_id": {"type": "string", "description": "附件 ID 标识"},
					"page": {"type": "integer", "description": "页码（从 1 开始）", "default": 1},
					"page_size": {"type": "integer", "description": "每页字符数", "default": 4000},
				},
				"required": ["artifact_id"],
			},
			_artifact_page,
			is_read_only=True,
		),
	]
	return {tool.name: tool for tool in tools}
