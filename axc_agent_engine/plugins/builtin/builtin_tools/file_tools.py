"""Filesystem builtin tools."""
import glob
import os
from typing import Any

from axc_agent_engine.tools.tool_output import ToolOutput

from .path_policy import BuiltinPathPolicy, PathValidationError
from .result_store import ResultStoreReader

FILE_READ_LINE_WINDOW = 200
FILE_READ_MAX_SIZE = 10 * 1024 * 1024


class BuiltinFileTools:
	def __init__(
		self,
		path_policy: BuiltinPathPolicy | None = None,
		result_reader: ResultStoreReader | None = None,
	) -> None:
		self._path_policy = path_policy or BuiltinPathPolicy()
		self._result_reader = result_reader or ResultStoreReader()

	async def read(self, args: dict[str, Any], context: dict[str, Any]) -> ToolOutput:
		path = args.get("path", "")
		if not path:
			return ToolOutput.error("path cannot be empty")
		try:
			resolved = self._path_policy.resolve_workspace_path(path, context)
		except PathValidationError as e:
			return ToolOutput.error(str(e))
		try:
			size = os.path.getsize(resolved)
			if size > FILE_READ_MAX_SIZE:
				return ToolOutput.error(f"File too large ({size} bytes), exceeds {FILE_READ_MAX_SIZE} bytes limit")
			with open(resolved, "r", encoding="utf-8") as f:
				full_content = f.read()
		except Exception as e:
			return ToolOutput.error(str(e))
		lines = full_content.split("\n")
		total_lines = len(lines)
		start_line = max(1, args.get("start_line", 1))
		end_line = args.get("end_line", 0)
		if end_line <= 0:
			end_line = min(start_line + FILE_READ_LINE_WINDOW - 1, total_lines)
		end_line = min(end_line, total_lines)
		window_text = "\n".join(lines[start_line - 1:end_line])
		artifacts = []
		result_store = self._result_reader.store(context)
		artifact_id = ""
		if result_store and total_lines > FILE_READ_LINE_WINDOW:
			ref = await result_store.put(full_content, {"kind": "file", "path": path})
			artifacts.append(ref)
			artifact_id = ref.id
		content_data = {
			"path": path,
			"total_lines": total_lines,
			"start_line": start_line,
			"end_line": end_line,
			"text": window_text,
			"truncated": end_line < total_lines,
		}
		if artifact_id:
			content_data["artifact_id"] = artifact_id
		return ToolOutput(
			content=content_data,
			content_type="json",
			summary=f"file_read：{path}（共 {total_lines} 行，显示 {start_line}-{end_line}）",
			artifacts=artifacts,
		)

	async def list(self, args: dict[str, Any], context: dict[str, Any]) -> ToolOutput:
		path = args.get("path", ".") or "."
		recursive = bool(args.get("recursive", False))
		limit = max(1, min(int(args.get("limit", 200) or 200), 1000))
		try:
			resolved = self._path_policy.resolve_workspace_path(path, context)
		except PathValidationError as e:
			return ToolOutput.error(str(e))
		if not os.path.isdir(resolved):
			return ToolOutput.error(f"Not a directory: {path}")
		entries: list[dict[str, Any]] = []
		try:
			if recursive:
				for root, dirs, files in os.walk(resolved):
					dirs.sort()
					files.sort()
					names = [(name, True) for name in dirs] + [(name, False) for name in files]
					for name, _is_dir in names:
						full = os.path.join(root, name)
						entries.append(self._path_policy.file_entry(full, context))
						if len(entries) >= limit:
							break
					if len(entries) >= limit:
						break
			else:
				for name in sorted(os.listdir(resolved)):
					full = os.path.join(resolved, name)
					entries.append(self._path_policy.file_entry(full, context))
					if len(entries) >= limit:
						break
		except Exception as e:
			return ToolOutput.error(str(e))
		return ToolOutput.json_output(
			{"path": path, "entries": entries, "truncated": len(entries) >= limit, "limit": limit},
			summary=f"file_list：{path}，返回 {len(entries)} 个条目",
		)

	async def glob(self, args: dict[str, Any], context: dict[str, Any]) -> ToolOutput:
		pattern = args.get("pattern", "")
		if not pattern:
			return ToolOutput.error("pattern cannot be empty")
		if os.path.isabs(pattern) or ".." in pattern.replace("\\", "/").split("/"):
			return ToolOutput.error("pattern must stay inside workspace")
		limit = max(1, min(int(args.get("limit", 200) or 200), 1000))
		include_dirs = bool(args.get("include_dirs", False))
		try:
			base = self._path_policy.resolve_workspace_path(".", context)
		except PathValidationError as e:
			return ToolOutput.error(str(e))
		base_real = os.path.realpath(base)
		search_pattern = os.path.join(base_real, pattern)
		try:
			matches = []
			for full in sorted(glob.glob(search_pattern, recursive=True)):
				full_real = os.path.realpath(full)
				if not (full_real == base_real or full_real.startswith(base_real + os.sep)):
					continue
				if os.path.isdir(full_real) and not include_dirs:
					continue
				matches.append(self._path_policy.file_entry(full_real, context))
				if len(matches) >= limit:
					break
		except Exception as e:
			return ToolOutput.error(str(e))
		return ToolOutput.json_output(
			{"pattern": pattern, "matches": matches, "truncated": len(matches) >= limit, "limit": limit},
			summary=f"file_glob：{pattern}，返回 {len(matches)} 个条目",
		)

	async def info(self, args: dict[str, Any], context: dict[str, Any]) -> ToolOutput:
		path = args.get("path", "")
		if not path:
			return ToolOutput.error("path cannot be empty")
		try:
			resolved = self._path_policy.resolve_workspace_path(path, context)
		except PathValidationError as e:
			return ToolOutput.error(str(e))
		if not os.path.exists(resolved):
			return ToolOutput.error(f"Path not found: {path}")
		try:
			return ToolOutput.json_output(self._path_policy.file_entry(resolved, context), summary=f"file_info：{path}")
		except Exception as e:
			return ToolOutput.error(str(e))

	async def write(self, args: dict[str, Any], context: dict[str, Any]) -> ToolOutput:
		path = args.get("path", "")
		content = args.get("content", "")
		if not path:
			return ToolOutput.error("path cannot be empty")
		try:
			resolved = self._path_policy.resolve_workspace_path(path, context)
		except PathValidationError as e:
			return ToolOutput.error(str(e))
		try:
			os.makedirs(os.path.dirname(resolved) or ".", exist_ok=True)
			with open(resolved, "w", encoding="utf-8") as f:
				f.write(content)
			data = {"success": True, "path": path, "bytes": len(content.encode())}
			return ToolOutput.json_output(data, summary=f"已向 {path} 写入 {len(content.encode())} 字节")
		except Exception as e:
			return ToolOutput.error(str(e))

	async def append(self, args: dict[str, Any], context: dict[str, Any]) -> ToolOutput:
		path = args.get("path", "")
		content = args.get("content", "")
		create = bool(args.get("create", True))
		if not path:
			return ToolOutput.error("path cannot be empty")
		try:
			resolved = self._path_policy.resolve_workspace_path(path, context)
		except PathValidationError as e:
			return ToolOutput.error(str(e))
		if not create and not os.path.exists(resolved):
			return ToolOutput.error(f"File not found: {path}")
		try:
			os.makedirs(os.path.dirname(resolved) or ".", exist_ok=True)
			with open(resolved, "a", encoding="utf-8") as f:
				f.write(content)
			data = {"success": True, "path": path, "bytes_appended": len(content.encode())}
			return ToolOutput.json_output(data, summary=f"已向 {path} 追加 {len(content.encode())} 字节")
		except Exception as e:
			return ToolOutput.error(str(e))

	async def edit(self, args: dict[str, Any], context: dict[str, Any]) -> ToolOutput:
		path = args.get("path", "")
		old_string = args.get("old_string", "")
		new_string = args.get("new_string", "")
		replace_all = args.get("replace_all", False)
		if not path:
			return ToolOutput.error("path cannot be empty")
		if not old_string:
			return ToolOutput.error("old_string cannot be empty")
		try:
			resolved = self._path_policy.resolve_workspace_path(path, context)
		except PathValidationError as e:
			return ToolOutput.error(str(e))
		try:
			with open(resolved, "r", encoding="utf-8") as f:
				content = f.read()
		except FileNotFoundError:
			return ToolOutput.error(f"File not found: {path}")
		except Exception as e:
			return ToolOutput.error(str(e))
		normalized_old = old_string.replace("\u2018", "'").replace("\u2019", "'").replace("\u201c", '"').replace("\u201d", '"')
		normalized_content = content.replace("\u2018", "'").replace("\u2019", "'").replace("\u201c", '"').replace("\u201d", '"')
		count = content.count(old_string)
		if count == 0:
			count = normalized_content.count(normalized_old)
			if count == 0:
				return ToolOutput.error(f"No matching text found (file_length={len(content)})")
			content = normalized_content
			old_string = normalized_old
			count = content.count(old_string)
		if count > 1 and not replace_all:
			return ToolOutput.error(f"找到 {count} 处匹配，请设置 replace_all=true 或提供更精确的文本")
		if replace_all:
			new_content = content.replace(old_string, new_string)
		else:
			new_content = content.replace(old_string, new_string, 1)
		tmp_path = resolved + ".tmp"
		try:
			with open(tmp_path, "w", encoding="utf-8") as f:
				f.write(new_content)
			os.replace(tmp_path, resolved)
		except Exception as e:
			if os.path.exists(tmp_path):
				os.remove(tmp_path)
			return ToolOutput.error(str(e))
		data = {"success": True, "replacements": count if replace_all else 1, "path": path}
		return ToolOutput.json_output(data, summary=f"已编辑 {path}：{count if replace_all else 1} 处替换")
