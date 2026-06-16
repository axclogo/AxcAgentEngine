"""Filesystem builtin tools.
中文：文件系统内置工具。"""
import glob
import os
from typing import Any

from axc_agent_engine.tools.tool_output import ArtifactRef, ToolOutput

from .path_policy import BuiltinPathPolicy, PathValidationError
from .artifact_store import ArtifactStoreReader

FILE_READ_MAX_SIZE = 10 * 1024 * 1024
FILE_RESULT_EXTERNALIZE_BYTES = FILE_READ_MAX_SIZE
LIST_RESULT_EXTERNALIZE_COUNT = 1000
DEFAULT_TREE_IGNORES = (
	"node_modules", ".git", "dist", "build", ".turbo", ".next", ".cache",
	"__pycache__", ".pytest_cache", ".venv", "venv", "target", ".mypy_cache",
)


class BuiltinFileTools:
	def __init__(
		self,
		path_policy: BuiltinPathPolicy | None = None,
		artifact_reader: ArtifactStoreReader | None = None,
	) -> None:
		self._path_policy = path_policy or BuiltinPathPolicy()
		self._artifact_reader = artifact_reader or ArtifactStoreReader()

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
		except Exception as e:
			return ToolOutput.error(str(e))
		if size > FILE_READ_MAX_SIZE:
			return await self._externalized_file_read(path, resolved, size, context)
		try:
			with open(resolved, "r", encoding="utf-8") as f:
				full_content = f.read()
		except Exception as e:
			return ToolOutput.error(str(e))
		lines = full_content.split("\n")
		total_lines = len(lines)
		try:
			ranges = _resolve_line_ranges(args, total_lines)
		except (TypeError, ValueError) as e:
			return ToolOutput.error(str(e))
		text = _join_line_ranges(lines, ranges)
		start_line = ranges[0][0] if ranges else 1
		end_line = ranges[-1][1] if ranges else total_lines
		content_data = {
			"path": path,
			"total_lines": total_lines,
			"start_line": start_line,
			"end_line": end_line,
			"ranges": [[start, end] for start, end in ranges],
			"text": text,
			"partial": not _ranges_cover_full_file(ranges, total_lines),
			"externalized": False,
		}
		return ToolOutput(
			content=content_data,
			content_type="json",
			summary=f"file_read：{path}（共 {total_lines} 行，显示 {_range_label(ranges)}）",
			llm_view=_file_read_llm_view(path, total_lines, ranges, text),
		)

	async def list(self, args: dict[str, Any], context: dict[str, Any]) -> ToolOutput:
		path = args.get("path", ".") or "."
		recursive = bool(args.get("recursive", False))
		try:
			resolved = self._path_policy.resolve_workspace_path(path, context)
		except PathValidationError as e:
			return ToolOutput.error(str(e))
		if not os.path.isdir(resolved):
			return ToolOutput.error(f"Not a directory: {path}")
		try:
			entries = self._collect_entries(resolved, recursive, context)
		except Exception as e:
			return ToolOutput.error(str(e))
		content = {"path": path, "entries": entries, "count": len(entries), "recursive": recursive, "externalized": False}
		return await self._list_output(
			"file_list",
			content,
			_file_list_llm_view(path, entries, recursive),
			context,
			summary=f"file_list：{path}，返回 {len(entries)} 个条目",
		)

	async def glob(self, args: dict[str, Any], context: dict[str, Any]) -> ToolOutput:
		pattern = args.get("pattern", "")
		if not pattern:
			return ToolOutput.error("pattern cannot be empty")
		if os.path.isabs(pattern) or ".." in pattern.replace("\\", "/").split("/"):
			return ToolOutput.error("pattern must stay inside workspace")
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
		except Exception as e:
			return ToolOutput.error(str(e))
		content = {"pattern": pattern, "matches": matches, "count": len(matches), "externalized": False}
		return await self._list_output(
			"file_glob",
			content,
			_file_glob_llm_view(pattern, matches),
			context,
			summary=f"file_glob：{pattern}，返回 {len(matches)} 个条目",
		)

	async def tree(self, args: dict[str, Any], context: dict[str, Any]) -> ToolOutput:
		path = args.get("path", ".") or "."
		try:
			max_depth = max(1, int(args.get("max_depth", 3) or 3))
		except (TypeError, ValueError):
			return ToolOutput.error("max_depth must be an integer")
		ignore_arg = args.get("ignore", DEFAULT_TREE_IGNORES)
		ignore = {str(item) for item in ignore_arg} if isinstance(ignore_arg, list) else set(DEFAULT_TREE_IGNORES)
		try:
			resolved = self._path_policy.resolve_workspace_path(path, context)
		except PathValidationError as e:
			return ToolOutput.error(str(e))
		if not os.path.isdir(resolved):
			return ToolOutput.error(f"Not a directory: {path}")
		try:
			lines, entries = _build_tree(resolved, path, max_depth, ignore, self._path_policy, context)
		except Exception as e:
			return ToolOutput.error(str(e))
		tree_text = "\n".join(lines)
		content = {
			"path": path,
			"max_depth": max_depth,
			"ignore": sorted(ignore),
			"entries": entries,
			"tree": tree_text,
			"count": len(entries),
			"externalized": False,
		}
		return await self._list_output("file_tree", content, tree_text, context, summary=f"file_tree：{path}，返回 {len(entries)} 个节点")

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
		new_content = content.replace(old_string, new_string) if replace_all else content.replace(old_string, new_string, 1)
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

	def _collect_entries(self, resolved: str, recursive: bool, context: dict[str, Any]):
		entries: list[dict[str, Any]] = []
		if recursive:
			for root, dirs, files in os.walk(resolved):
				dirs.sort()
				files.sort()
				for name in [*dirs, *files]:
					entries.append(self._path_policy.file_entry(os.path.join(root, name), context))
		else:
			for name in sorted(os.listdir(resolved)):
				entries.append(self._path_policy.file_entry(os.path.join(resolved, name), context))
		return entries

	async def _externalized_file_read(
		self,
		path: str,
		resolved: str,
		size: int,
		context: dict[str, Any],
	) -> ToolOutput:
		artifact_store = self._artifact_reader.store(context)
		if not artifact_store:
			return ToolOutput.error(f"File is {size} bytes; artifact_store is required to externalize files larger than {FILE_READ_MAX_SIZE} bytes")
		ref = await artifact_store.put_file_ref(resolved, {"path": path}, kind="file")
		content_data = {
			"path": path,
			"bytes": size,
			"text": "",
			"partial": False,
			"externalized": True,
			"artifact_id": ref.id,
		}
		return ToolOutput(
			content=content_data,
			content_type="json",
			summary=f"file_read：{path} 已外部化到 artifact {ref.id}",
			llm_view=_externalized_llm_view("file_read", path, size, ref),
			artifacts=[ref],
		)

	async def _list_output(
		self,
		tool_name: str,
		content: dict[str, Any],
		llm_view: str,
		context: dict[str, Any],
		summary: str,
	) -> ToolOutput:
		if int(content.get("count", 0)) <= LIST_RESULT_EXTERNALIZE_COUNT:
			return ToolOutput(content=content, content_type="json", summary=summary, llm_view=llm_view)
		artifact_store = self._artifact_reader.store(context)
		if not artifact_store:
			return ToolOutput(content=content, content_type="json", summary=summary, llm_view=llm_view)
		ref = await artifact_store.put_text(llm_view, {"tool": tool_name, "path": content.get("path", content.get("pattern", ""))}, kind="text")
		externalized = dict(content)
		externalized["externalized"] = True
		externalized["artifact_id"] = ref.id
		return ToolOutput(
			content=externalized,
			content_type="json",
			summary=summary,
			llm_view=_externalized_llm_view(tool_name, str(content.get("path", content.get("pattern", ""))), len(llm_view), ref),
			artifacts=[ref],
		)


def _file_read_llm_view(path: str, total_lines: int, ranges: list[tuple[int, int]], text: str) -> str:
	lines = [
		f"File: {path}",
		f"Lines shown: {_range_label(ranges)} of {total_lines}",
		"内容:",
	]
	lines.extend(text.split("\n") if text else [])
	return "\n".join(lines)


def _file_list_llm_view(path: str, entries: list[dict[str, Any]], recursive: bool) -> str:
	lines = [f"file_list {path}（{len(entries)} 项, recursive={str(recursive).lower()}）"]
	if not entries:
		lines.append("（无条目）")
		return "\n".join(lines)
	for entry in entries:
		lines.append(_entry_line(entry, include_type=True))
	return "\n".join(lines)


def _file_glob_llm_view(pattern: str, entries: list[dict[str, Any]]) -> str:
	lines = [f"file_glob {pattern}（{len(entries)} 项）"]
	if not entries:
		lines.append("（无匹配）")
		return "\n".join(lines)
	for entry in entries:
		lines.append(str(entry.get("path") or entry.get("name") or ""))
	return "\n".join(lines)


def _externalized_llm_view(tool_name: str, target: str, size: int, artifact: ArtifactRef) -> str:
	return (
		f"{tool_name} {target} 的完整结果已外部化。\n"
		f"artifact_id: {artifact.id}\n"
		f"size: {artifact.size} bytes\n"
		f"llm_view_bytes: {size}\n"
		"内容已完整外部化；请用 artifact_read/artifact_page 按需读取。"
	)


def _resolve_line_ranges(args: dict[str, Any], total_lines: int) -> list[tuple[int, int]]:
	if total_lines <= 0:
		return [(1, 1)]
	raw_ranges = args.get("ranges")
	if isinstance(raw_ranges, list) and raw_ranges:
		ranges = [_clamp_range(item, total_lines) for item in raw_ranges]
		return [item for item in ranges if item[0] <= item[1]]
	start_line = max(1, int(args.get("start_line", 1) or 1))
	end_line = int(args.get("end_line", 0) or 0)
	if end_line <= 0:
		end_line = total_lines
	return [_clamp_range([start_line, end_line], total_lines)]


def _clamp_range(item: Any, total_lines: int) -> tuple[int, int]:
	if not isinstance(item, (list, tuple)) or len(item) != 2:
		raise ValueError("ranges items must be [start_line, end_line]")
	start = max(1, int(item[0]))
	end = min(total_lines, int(item[1]))
	return start, end


def _join_line_ranges(lines: list[str], ranges: list[tuple[int, int]]) -> str:
	parts: list[str] = []
	for index, (start, end) in enumerate(ranges):
		if index:
			parts.append(f"--- lines {start}-{end} ---")
		for line_no in range(start, end + 1):
			parts.append(f"{line_no}: {lines[line_no - 1]}")
	return "\n".join(parts)


def _ranges_cover_full_file(ranges: list[tuple[int, int]], total_lines: int) -> bool:
	return len(ranges) == 1 and ranges[0] == (1, total_lines)


def _range_label(ranges: list[tuple[int, int]]) -> str:
	return ", ".join(f"{start}-{end}" for start, end in ranges)


def _entry_line(entry: dict[str, Any], include_type: bool = False) -> str:
	path = str(entry.get("path") or entry.get("name") or "")
	name = str(entry.get("name") or path)
	is_dir = entry.get("type") == "directory" or entry.get("is_dir") is True
	marker = "[d]" if is_dir else "[f]"
	label = path if "/" in path else name
	if is_dir and not label.endswith("/"):
		label += "/"
	if not include_type:
		return label
	size = entry.get("size")
	size_text = f" ({size}B)" if isinstance(size, int) and not is_dir else ""
	return f"{marker} {label}{size_text}"


def _build_tree(
	root: str,
	path: str,
	max_depth: int,
	ignore: set[str],
	path_policy: BuiltinPathPolicy,
	context: dict[str, Any],
) -> tuple[list[str], list[dict[str, Any]]]:
	root_label = "." if path == "." else path.rstrip("/") or "."
	lines = [root_label]
	entries: list[dict[str, Any]] = []

	def walk(directory: str, prefix: str, depth: int) -> None:
		if depth > max_depth:
			return
		children = []
		for name in sorted(os.listdir(directory)):
			if name in ignore:
				continue
			full = os.path.join(directory, name)
			children.append((name, full, os.path.isdir(full)))
		for index, (name, full, is_dir) in enumerate(children):
			last = index == len(children) - 1
			connector = "└── " if last else "├── "
			suffix = "/" if is_dir else ""
			lines.append(f"{prefix}{connector}{name}{suffix}")
			entries.append(path_policy.file_entry(full, context))
			if is_dir and depth < max_depth:
				walk(full, prefix + ("    " if last else "│   "), depth + 1)

	walk(root, "", 1)
	return lines, entries
