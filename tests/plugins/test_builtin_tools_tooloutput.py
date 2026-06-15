"""Tests for builtin tools returning ToolOutput — file_read, file_write, shell, etc."""
import pytest
from axc_agent_engine.plugins.builtin.builtin_tools.plugin import BuiltinToolsPlugin
from axc_agent_engine.plugins.builtin.builtin_tools.command_tools import (
	BuiltinCommandTools,
	ensure_venv,
	get_command_executor,
	store_command_artifacts,
)
from axc_agent_engine.plugins.builtin.builtin_tools.file_tools import BuiltinFileTools
from axc_agent_engine.plugins.builtin.builtin_tools.path_policy import BuiltinPathPolicy, PathValidationError
from axc_agent_engine.plugins.builtin.builtin_tools.support import bounded_int, truncate_by_bytes
from axc_agent_engine.plugins.builtin.builtin_tools.tool_definitions import (
	_file_read, _file_write, _file_append, _file_edit, _file_list, _file_glob, _file_info, _get_time,
	_http_request, _pip_install, _python_exec, _shell,
	_result_read, _result_search, _result_page,
)
from axc_agent_engine.plugins.builtin.builtin_tools.http_tools import BuiltinHttpPolicy, is_blocked_ip
from axc_agent_engine.core.context import ExecutionContext
from axc_agent_engine.runtime.sandbox_models import CommandResult
from axc_agent_engine.tools.tool_output import ToolOutput
from axc_agent_engine.storage.result_store import InMemoryResultStore


class TestGetTime:
	@pytest.mark.asyncio
	async def test_returns_tooloutput(self):
		result = await _get_time({}, {})
		assert isinstance(result, ToolOutput)
		assert result.content_type == "json"
		assert "utc" in result.content

	@pytest.mark.asyncio
	async def test_has_summary(self):
		result = await _get_time({"timezone": "UTC"}, {})
		assert result.summary != ""


class TestFileRead:
	@pytest.mark.asyncio
	async def test_basic_read(self, tmp_path):
		f = tmp_path / "test.txt"
		f.write_text("line1\nline2\nline3")
		result = await _file_read({"path": "test.txt"}, {"workspace": str(tmp_path)})
		assert isinstance(result, ToolOutput)
		assert result.content_type == "json"
		assert result.content["total_lines"] == 3
		assert "line1" in result.content["text"]
		assert "File: test.txt" in result.context_view()
		assert "1: line1" in result.context_view()
		assert result.display_view().startswith('{"path": "test.txt"')

	@pytest.mark.asyncio
	async def test_empty_path(self):
		result = await _file_read({"path": ""}, {})
		assert result.is_error
		assert "empty" in result.content

	@pytest.mark.asyncio
	async def test_nonexistent_file(self):
		result = await _file_read({"path": "missing.txt"}, {"workspace": "/tmp"})
		assert result.is_error

	@pytest.mark.asyncio
	async def test_requires_workspace_by_default(self, tmp_path):
		f = tmp_path / "test.txt"
		f.write_text("secret")
		result = await _file_read({"path": str(f)}, {})
		assert result.is_error
		assert "workspace" in result.content.lower()

	@pytest.mark.asyncio
	async def test_line_window(self, tmp_path):
		f = tmp_path / "big.txt"
		f.write_text("\n".join(f"line{i}" for i in range(500)))
		result = await _file_read({"path": "big.txt"}, {"workspace": str(tmp_path)})
		assert result.content["truncated"] is True
		assert result.content["end_line"] == 200  # default window

	@pytest.mark.asyncio
	async def test_custom_line_range(self, tmp_path):
		f = tmp_path / "range.txt"
		f.write_text("\n".join(f"L{i}" for i in range(100)))
		result = await _file_read({"path": "range.txt", "start_line": 10, "end_line": 20}, {"workspace": str(tmp_path)})
		assert result.content["start_line"] == 10
		assert result.content["end_line"] == 20
		assert "L9" in result.content["text"]
		assert "L19" in result.content["text"]

	@pytest.mark.asyncio
	async def test_large_file_stores_artifact(self, tmp_path):
		f = tmp_path / "large.txt"
		f.write_text("\n".join(f"line{i}" for i in range(500)))
		store = InMemoryResultStore()
		result = await _file_read({"path": "large.txt"}, {"workspace": str(tmp_path), "result_store": store})
		assert len(result.artifacts) == 1
		assert result.artifacts[0].kind == "file"
		# Verify artifact content is retrievable
		content = await store.get(result.artifacts[0].id, offset=0, limit=100)
		assert "line0" in content

	@pytest.mark.asyncio
	async def test_small_file_no_artifact(self, tmp_path):
		f = tmp_path / "small.txt"
		f.write_text("short file")
		store = InMemoryResultStore()
		result = await _file_read({"path": "small.txt"}, {"workspace": str(tmp_path), "result_store": store})
		assert len(result.artifacts) == 0

	@pytest.mark.asyncio
	async def test_workspace_boundary(self, tmp_path):
		outside = tmp_path / "outside.txt"
		outside.write_text("secret")
		workspace = tmp_path / "workspace"
		workspace.mkdir()
		result = await _file_read({"path": "../outside.txt"}, {"workspace": str(workspace)})
		assert result.is_error
		assert "outside" in result.content.lower() or "boundary" in result.content.lower()

	@pytest.mark.asyncio
	async def test_file_too_large(self, tmp_path):
		f = tmp_path / "huge.txt"
		f.write_text("x" * (10 * 1024 * 1024 + 1))
		result = await _file_read({"path": "huge.txt"}, {"workspace": str(tmp_path)})
		assert result.is_error
		assert "too large" in result.content.lower()

	@pytest.mark.asyncio
	async def test_start_line_clamps_and_end_line_limits_to_total(self, tmp_path):
		f = tmp_path / "clamp.txt"
		f.write_text("a\nb\nc")
		result = await _file_read(
			{"path": "clamp.txt", "start_line": -10, "end_line": 999},
			{"workspace": str(tmp_path)},
		)
		assert result.content["start_line"] == 1
		assert result.content["end_line"] == 3


class TestFileList:
	@pytest.mark.asyncio
	async def test_basic_list(self, tmp_path):
		(tmp_path / "a.txt").write_text("a")
		(tmp_path / "dir").mkdir()
		result = await _file_list({"path": "."}, {"workspace": str(tmp_path)})
		assert not result.is_error
		names = {item["name"] for item in result.content["entries"]}
		assert {"a.txt", "dir"}.issubset(names)

	@pytest.mark.asyncio
	async def test_recursive_list(self, tmp_path):
		(tmp_path / "dir").mkdir()
		(tmp_path / "dir" / "nested.txt").write_text("nested")
		result = await _file_list({"path": ".", "recursive": True}, {"workspace": str(tmp_path)})
		assert not result.is_error
		paths = {item["path"] for item in result.content["entries"]}
		assert "dir/nested.txt" in paths

	@pytest.mark.asyncio
	async def test_limit(self, tmp_path):
		for i in range(5):
			(tmp_path / f"{i}.txt").write_text(str(i))
		result = await _file_list({"path": ".", "limit": 2}, {"workspace": str(tmp_path)})
		assert len(result.content["entries"]) == 2
		assert result.content["truncated"] is True

	@pytest.mark.asyncio
	async def test_workspace_boundary(self, tmp_path):
		outside = tmp_path / "outside"
		outside.mkdir()
		workspace = tmp_path / "workspace"
		workspace.mkdir()
		result = await _file_list({"path": "../outside"}, {"workspace": str(workspace)})
		assert result.is_error

	@pytest.mark.asyncio
	async def test_list_rejects_file_path(self, tmp_path):
		(tmp_path / "file.txt").write_text("x")
		result = await _file_list({"path": "file.txt"}, {"workspace": str(tmp_path)})
		assert result.is_error
		assert "Not a directory" in result.content


class TestFileInfo:
	@pytest.mark.asyncio
	async def test_file_info(self, tmp_path):
		(tmp_path / "info.txt").write_text("abc")
		result = await _file_info({"path": "info.txt"}, {"workspace": str(tmp_path)})
		assert not result.is_error
		assert result.content["type"] == "file"
		assert result.content["size"] == 3

	@pytest.mark.asyncio
	async def test_directory_info(self, tmp_path):
		(tmp_path / "folder").mkdir()
		result = await _file_info({"path": "folder"}, {"workspace": str(tmp_path)})
		assert not result.is_error
		assert result.content["type"] == "directory"

	@pytest.mark.asyncio
	async def test_missing_path(self, tmp_path):
		result = await _file_info({"path": "missing"}, {"workspace": str(tmp_path)})
		assert result.is_error


class TestFileGlob:
	@pytest.mark.asyncio
	async def test_glob_files(self, tmp_path):
		(tmp_path / "a.py").write_text("a")
		(tmp_path / "b.txt").write_text("b")
		(tmp_path / "pkg").mkdir()
		(tmp_path / "pkg" / "c.py").write_text("c")
		result = await _file_glob({"pattern": "**/*.py"}, {"workspace": str(tmp_path)})
		assert not result.is_error
		paths = {item["path"] for item in result.content["matches"]}
		assert paths == {"a.py", "pkg/c.py"}

	@pytest.mark.asyncio
	async def test_glob_limit(self, tmp_path):
		for i in range(5):
			(tmp_path / f"{i}.txt").write_text(str(i))
		result = await _file_glob({"pattern": "*.txt", "limit": 2}, {"workspace": str(tmp_path)})
		assert len(result.content["matches"]) == 2
		assert result.content["truncated"] is True

	@pytest.mark.asyncio
	async def test_glob_requires_pattern(self):
		result = await _file_glob({"pattern": ""}, {})
		assert result.is_error

	@pytest.mark.asyncio
	async def test_glob_rejects_parent_pattern(self, tmp_path):
		result = await _file_glob({"pattern": "../*.txt"}, {"workspace": str(tmp_path)})
		assert result.is_error

	@pytest.mark.asyncio
	async def test_glob_can_include_directories_and_clamps_limit(self, tmp_path):
		(tmp_path / "pkg").mkdir()
		(tmp_path / "pkg" / "a.py").write_text("x")
		result = await _file_glob({"pattern": "**", "include_dirs": True, "limit": 0}, {"workspace": str(tmp_path)})
		assert not result.is_error
		assert len(result.content["matches"]) >= 1
		assert result.content["limit"] == 200
		assert any(item["type"] == "directory" for item in result.content["matches"])


class TestFileWrite:
	@pytest.mark.asyncio
	async def test_basic_write(self, tmp_path):
		f = tmp_path / "out.txt"
		result = await _file_write({"path": "out.txt", "content": "hello"}, {"workspace": str(tmp_path)})
		assert isinstance(result, ToolOutput)
		assert not result.is_error
		assert result.content["success"] is True
		assert f.read_text() == "hello"

	@pytest.mark.asyncio
	async def test_empty_path(self):
		result = await _file_write({"path": "", "content": "x"}, {})
		assert result.is_error

	@pytest.mark.asyncio
	async def test_creates_directories(self, tmp_path):
		f = tmp_path / "sub" / "dir" / "file.txt"
		result = await _file_write({"path": "sub/dir/file.txt", "content": "nested"}, {"workspace": str(tmp_path)})
		assert not result.is_error
		assert f.read_text() == "nested"

	@pytest.mark.asyncio
	async def test_summary_contains_bytes(self, tmp_path):
		result = await _file_write({"path": "s.txt", "content": "abc"}, {"workspace": str(tmp_path)})
		assert "3" in result.summary

	@pytest.mark.asyncio
	async def test_write_rejects_workspace_escape(self, tmp_path):
		result = await _file_write({"path": "../out.txt", "content": "x"}, {"workspace": str(tmp_path)})
		assert result.is_error


class TestFileAppend:
	@pytest.mark.asyncio
	async def test_append_existing_file(self, tmp_path):
		f = tmp_path / "log.txt"
		f.write_text("a")
		result = await _file_append({"path": "log.txt", "content": "b"}, {"workspace": str(tmp_path)})
		assert not result.is_error
		assert f.read_text() == "ab"

	@pytest.mark.asyncio
	async def test_append_creates_file_by_default(self, tmp_path):
		f = tmp_path / "new.txt"
		result = await _file_append({"path": "new.txt", "content": "hello"}, {"workspace": str(tmp_path)})
		assert not result.is_error
		assert f.read_text() == "hello"

	@pytest.mark.asyncio
	async def test_append_can_require_existing_file(self, tmp_path):
		result = await _file_append(
			{"path": "missing.txt", "content": "x", "create": False},
			{"workspace": str(tmp_path)},
		)
		assert result.is_error

	@pytest.mark.asyncio
	async def test_append_rejects_empty_path(self):
		result = await _file_append({"path": "", "content": "x"}, {})
		assert result.is_error


class TestFileEdit:
	@pytest.mark.asyncio
	async def test_basic_edit(self, tmp_path):
		f = tmp_path / "edit.txt"
		f.write_text("hello world")
		result = await _file_edit({"path": "edit.txt", "old_string": "world", "new_string": "earth"}, {"workspace": str(tmp_path)})
		assert not result.is_error
		assert f.read_text() == "hello earth"

	@pytest.mark.asyncio
	async def test_no_match(self, tmp_path):
		f = tmp_path / "edit2.txt"
		f.write_text("hello")
		result = await _file_edit({"path": "edit2.txt", "old_string": "xyz", "new_string": "abc"}, {"workspace": str(tmp_path)})
		assert result.is_error
		assert "No matching" in result.content

	@pytest.mark.asyncio
	async def test_multiple_matches_no_replace_all(self, tmp_path):
		f = tmp_path / "multi.txt"
		f.write_text("aaa bbb aaa")
		result = await _file_edit({"path": "multi.txt", "old_string": "aaa", "new_string": "ccc"}, {"workspace": str(tmp_path)})
		assert result.is_error
		assert "2 处匹配" in result.content

	@pytest.mark.asyncio
	async def test_replace_all(self, tmp_path):
		f = tmp_path / "all.txt"
		f.write_text("aaa bbb aaa")
		result = await _file_edit(
			{"path": "all.txt", "old_string": "aaa", "new_string": "ccc", "replace_all": True},
			{"workspace": str(tmp_path)},
		)
		assert not result.is_error
		assert f.read_text() == "ccc bbb ccc"
		assert result.content["replacements"] == 2

	@pytest.mark.asyncio
	async def test_empty_old_string(self, tmp_path):
		f = tmp_path / "e.txt"
		f.write_text("content")
		result = await _file_edit({"path": "e.txt", "old_string": "", "new_string": "x"}, {"workspace": str(tmp_path)})
		assert result.is_error

	@pytest.mark.asyncio
	async def test_file_not_found(self):
		result = await _file_edit({"path": "no-such-file.txt", "old_string": "a", "new_string": "b"}, {"workspace": "/tmp"})
		assert result.is_error

	@pytest.mark.asyncio
	async def test_edit_rejects_empty_path_and_normalizes_smart_quotes(self, tmp_path):
		empty = await _file_edit({"path": "", "old_string": "a", "new_string": "b"}, {"workspace": str(tmp_path)})
		f = tmp_path / "smart.txt"
		f.write_text("say \u201chello\u201d")
		normalized = await _file_edit(
			{"path": "smart.txt", "old_string": '"hello"', "new_string": '"hi"'},
			{"workspace": str(tmp_path)},
		)

		assert empty.is_error
		assert not normalized.is_error
		assert f.read_text() == 'say "hi"'


class TestPythonExec:
	@pytest.mark.asyncio
	async def test_basic_exec(self):
		result = await _python_exec({"code": "print('hello')"}, {"allow_unsafe_workspace": True})
		assert isinstance(result, ToolOutput)
		assert not result.is_error
		assert "hello" in result.content["stdout_preview"]

	@pytest.mark.asyncio
	async def test_empty_code(self):
		result = await _python_exec({"code": ""}, {})
		assert result.is_error

	@pytest.mark.asyncio
	async def test_error_code(self):
		result = await _python_exec({"code": "raise ValueError('boom')"}, {"allow_unsafe_workspace": True})
		assert not result.is_error  # exit_code != 0 but not ToolOutput.error
		assert result.content["exit_code"] != 0

	@pytest.mark.asyncio
	async def test_large_stdout_artifact(self):
		store = InMemoryResultStore()
		code = "print('x' * 5000)"
		result = await _python_exec({"code": code}, {"result_store": store, "allow_unsafe_workspace": True})
		assert not result.is_error
		if "stdout_artifact_id" in result.content:
			aid = result.content["stdout_artifact_id"]
			content = await store.get(aid, offset=0, limit=100)
			assert len(content) > 0

	@pytest.mark.asyncio
	async def test_timeout_argument_reaches_executor(self, tmp_path):
		class FakeExecutor:
			def __init__(self):
				self.spec = None

			async def run(self, spec):
				self.spec = spec
				return CommandResult(exit_code=0, stdout="ok", stderr="")

		executor = FakeExecutor()
		result = await _python_exec(
			{"code": "print('ok')", "timeout": 7},
			{"workspace": str(tmp_path), "command_executor": executor},
		)
		assert not result.is_error
		assert executor.spec.timeout == 7


class TestShell:
	@pytest.mark.asyncio
	async def test_basic_shell(self, tmp_path):
		result = await _shell({"command": "echo hello"}, {"workspace": str(tmp_path)})
		assert isinstance(result, ToolOutput)
		assert not result.is_error
		assert "hello" in result.content["stdout_preview"]

	@pytest.mark.asyncio
	async def test_empty_command(self):
		result = await _shell({"command": ""}, {})
		assert result.is_error

	@pytest.mark.asyncio
	async def test_blocked_command(self):
		result = await _shell({"command": "rm -rf /"}, {"allow_unsafe_workspace": True})
		assert result.is_error
		assert "Blocked" in result.content

	@pytest.mark.asyncio
	async def test_exit_code(self, tmp_path):
		result = await _shell({"command": "exit 1"}, {"workspace": str(tmp_path)})
		assert result.content["exit_code"] == 1

	@pytest.mark.asyncio
	async def test_timeout_argument_reaches_executor(self, tmp_path):
		class FakeExecutor:
			def __init__(self):
				self.spec = None

			async def run(self, spec):
				self.spec = spec
				return CommandResult(exit_code=0, stdout="ok", stderr="")

		executor = FakeExecutor()
		result = await _shell(
			{"command": "echo ok", "timeout": 9},
			{"workspace": str(tmp_path), "command_executor": executor},
		)
		assert not result.is_error
		assert executor.spec.timeout == 9


class TestHttpRequest:
	@pytest.mark.asyncio
	async def test_blocks_localhost(self):
		result = await _http_request({"url": "http://localhost:8000"}, {})
		assert result.is_error
		assert "unsafe" in result.content.lower() or "blocked" in result.content.lower()

	@pytest.mark.asyncio
	async def test_blocks_private_ip(self):
		result = await _http_request({"url": "http://127.0.0.1:8000"}, {})
		assert result.is_error
		assert "unsafe" in result.content.lower() or "blocked" in result.content.lower()

	@pytest.mark.asyncio
	async def test_blocks_non_global_ip(self):
		result = await _http_request({"url": "http://100.64.0.1"}, {})
		assert result.is_error
		assert "unsafe" in result.content.lower() or "blocked" in result.content.lower()

	@pytest.mark.asyncio
	async def test_max_bytes_truncates_response(self, monkeypatch):
		class FakeResponse:
			status_code = 200
			text = "abcdef"
			headers = {"content-type": "text/plain"}

		class FakeClient:
			def __init__(self, timeout):
				self.timeout = timeout

			async def __aenter__(self):
				return self

			async def __aexit__(self, exc_type, exc, tb):
				return None

			async def request(self, method, url, headers, json):
				return FakeResponse()

		import axc_agent_engine.plugins.builtin.builtin_tools.tool_definitions as builtin_tools
		monkeypatch.setattr(builtin_tools.httpx, "AsyncClient", FakeClient)
		result = await _http_request({"url": "https://example.com", "max_bytes": 3, "timeout": 5}, {})
		assert not result.is_error
		assert result.content["body_preview"] == "abc"
		assert result.content["truncated"] is True

	@pytest.mark.asyncio
	async def test_empty_url_invalid_scheme_and_dns_failure(self):
		assert (await _http_request({"url": ""}, {})).is_error
		assert (await BuiltinHttpPolicy().validate_url("ftp://example.com")) == "Only http and https URLs are allowed"
		assert (await BuiltinHttpPolicy().validate_url("https:///missing-host")) == "URL hostname is required"
		assert await BuiltinHttpPolicy().validate_url("http://metadata.google.internal") is not None
		assert await BuiltinHttpPolicy().validate_url("http://example.local") is not None
		assert is_blocked_ip("not-an-ip") is True

	@pytest.mark.asyncio
	async def test_http_request_externalizes_large_body(self, monkeypatch):
		class FakeResponse:
			status_code = 201
			text = "abcdef"
			headers = {"content-type": "text/plain"}

		class FakeClient:
			def __init__(self, timeout):
				self.timeout = timeout

			async def __aenter__(self):
				return self

			async def __aexit__(self, exc_type, exc, tb):
				return None

			async def request(self, method, url, headers, json):
				assert method == "POST"
				assert json == {"x": 1}
				return FakeResponse()

		import axc_agent_engine.plugins.builtin.builtin_tools.tool_definitions as builtin_tools
		store = InMemoryResultStore()
		monkeypatch.setattr(builtin_tools.httpx, "AsyncClient", FakeClient)
		result = await _http_request(
			{"url": "https://example.com", "method": "post", "body": {"x": 1}, "max_bytes": 3},
			{"result_store": store},
		)

		assert not result.is_error
		assert result.content["body_artifact_id"]
		assert await store.get(result.content["body_artifact_id"], 0, 20) == "abcdef"


class TestPipInstall:
	@pytest.mark.asyncio
	async def test_uses_command_executor(self, tmp_path):
		class FakeExecutor:
			def __init__(self):
				self.spec = None

			async def run(self, spec):
				self.spec = spec
				return CommandResult(exit_code=0, stdout="installed", stderr="")

		executor = FakeExecutor()
		result = await _pip_install(
			{"package": "example-package"},
			{"workspace": str(tmp_path), "command_executor": executor},
		)
		assert not result.is_error
		assert result.content["returncode"] == 0
		assert executor.spec is not None
		assert executor.spec.argv[-2:] == ["install", "example-package"]

	@pytest.mark.asyncio
	async def test_empty_invalid_package_and_executor_failure(self, tmp_path):
		class FailingExecutor:
			async def run(self, spec):
				raise RuntimeError("pip down")

		assert (await _pip_install({"package": ""}, {"workspace": str(tmp_path)})).is_error
		assert (await _pip_install({"package": "bad;rm"}, {"workspace": str(tmp_path)})).is_error
		result = await _pip_install(
			{"package": "pkg"},
			{"workspace": str(tmp_path), "command_executor": FailingExecutor()},
		)
		assert result.is_error


class TestResultRead:
	@pytest.mark.asyncio
	async def test_basic_read(self):
		store = InMemoryResultStore()
		ref = await store.put("hello world content")
		result = await _result_read({"artifact_id": ref.id}, {"result_store": store})
		assert isinstance(result, ToolOutput)
		assert "hello world" in result.content

	@pytest.mark.asyncio
	async def test_empty_id(self):
		result = await _result_read({"artifact_id": ""}, {"result_store": InMemoryResultStore()})
		assert result.is_error

	@pytest.mark.asyncio
	async def test_no_store(self):
		result = await _result_read({"artifact_id": "x"}, {})
		assert result.is_error
		assert "not available" in result.content.lower()

	@pytest.mark.asyncio
	async def test_not_found(self):
		store = InMemoryResultStore()
		result = await _result_read({"artifact_id": "nonexistent"}, {"result_store": store})
		assert result.is_error

	@pytest.mark.asyncio
	async def test_with_offset_limit(self):
		store = InMemoryResultStore()
		ref = await store.put("0123456789")
		result = await _result_read({"artifact_id": ref.id, "offset": 3, "limit": 4}, {"result_store": store})
		assert result.content == "3456"


class TestResultSearch:
	@pytest.mark.asyncio
	async def test_basic_search(self):
		store = InMemoryResultStore()
		ref = await store.put("line1\nfoo bar\nline3")
		result = await _result_search({"artifact_id": ref.id, "query": "foo"}, {"result_store": store})
		assert not result.is_error
		assert len(result.content["matches"]) == 1
		assert "Search results for: foo" in result.context_view()
		assert "Matches: 1" in result.context_view()

	@pytest.mark.asyncio
	async def test_no_matches(self):
		store = InMemoryResultStore()
		ref = await store.put("nothing here")
		result = await _result_search({"artifact_id": ref.id, "query": "xyz"}, {"result_store": store})
		assert not result.is_error
		assert result.content["matches"] == []
		assert result.context_view() == "No matches for query: xyz"

	@pytest.mark.asyncio
	async def test_empty_query(self):
		result = await _result_search({"artifact_id": "x", "query": ""}, {"result_store": InMemoryResultStore()})
		assert result.is_error

	@pytest.mark.asyncio
	async def test_no_store(self):
		result = await _result_search({"artifact_id": "x", "query": "q"}, {})
		assert result.is_error


class TestResultPage:
	@pytest.mark.asyncio
	async def test_basic_page(self):
		store = InMemoryResultStore()
		ref = await store.put("a" * 10000)
		result = await _result_page({"artifact_id": ref.id, "page": 1, "page_size": 100}, {"result_store": store})
		assert not result.is_error
		assert len(result.content) == 100

	@pytest.mark.asyncio
	async def test_page_2(self):
		store = InMemoryResultStore()
		ref = await store.put("0123456789" * 100)
		result = await _result_page({"artifact_id": ref.id, "page": 2, "page_size": 10}, {"result_store": store})
		assert result.content == "0123456789"

	@pytest.mark.asyncio
	async def test_page_beyond_content(self):
		store = InMemoryResultStore()
		ref = await store.put("short")
		result = await _result_page({"artifact_id": ref.id, "page": 100, "page_size": 100}, {"result_store": store})
		assert result.is_error

	@pytest.mark.asyncio
	async def test_no_store(self):
		result = await _result_page({"artifact_id": "x"}, {})
		assert result.is_error


class TestBuiltinToolsPluginDefaults:
	def test_default_loads_only_get_time(self):
		plugin = BuiltinToolsPlugin()
		plugin.initialize({}, None)
		tools = plugin.get_tools()
		assert [tool.name for tool in tools] == ["get_time"]

	def test_can_explicitly_load_file_helpers(self):
		plugin = BuiltinToolsPlugin()
		plugin.initialize({"load": ["file_list", "file_glob", "file_info", "file_append"]}, None)
		assert {tool.name for tool in plugin.get_tools()} == {"file_list", "file_glob", "file_info", "file_append"}

	@pytest.mark.asyncio
	async def test_deferred_tool_search_activates_and_post_call_deactivates(self):
		plugin = BuiltinToolsPlugin()
		plugin.initialize({"load": ["get_time", "file_read"], "defer": ["file_read"]}, None)
		ctx = ExecutionContext()
		await plugin.on_execution_start(ctx)
		tools = {tool.name: tool for tool in plugin.get_tools()}

		assert tools["file_read"].deferred is True
		search = await tools["tool_search"].execute({"query": "read"}, {"exec_ctx": ctx})
		messages, schemas = plugin.pre_llm_call(ctx, [], [tools["get_time"].to_openai_schema()])
		await plugin.post_tool_call(ctx, "file_read", {}, ToolOutput.json_output({}), 1)
		_, cleared = plugin.pre_llm_call(ctx, [], [tools["get_time"].to_openai_schema()])

		assert search.content["tools"][0]["name"] == "file_read"
		assert messages == []
		assert any(schema["function"]["name"] == "file_read" for schema in schemas)
		assert not any(schema["function"]["name"] == "file_read" for schema in cleared)

	@pytest.mark.asyncio
	async def test_tool_search_without_exec_ctx_reports_no_match(self):
		plugin = BuiltinToolsPlugin()
		plugin.initialize({"defer": ["file_read"]}, None)
		tool = {tool.name: tool for tool in plugin.get_tools()}["tool_search"]

		result = await tool.execute({"query": "missing"}, {})

		assert result.content["tools"] == []


class TestBuiltinPathPolicy:
	def test_workspace_required_and_unsafe_path_resolution(self, tmp_path):
		policy = BuiltinPathPolicy()

		assert policy.get_workspace({}, "tool").is_error
		assert policy.get_workspace({"allow_unsafe_workspace": True}, "tool") == ""
		assert policy.resolve_workspace_path(str(tmp_path / "x"), {"allow_unsafe_workspace": True}).endswith("x")
		with pytest.raises(PathValidationError):
			policy.resolve_workspace_path("../x", {"workspace": str(tmp_path)})


class TestBuiltinCommandHelpers:
	@pytest.mark.asyncio
	async def test_python_exec_workspace_error_and_runtime_error(self):
		class Policy:
			def get_workspace(self, context, tool_name):
				return ToolOutput.error("no workspace")

		class FailingPolicy:
			def get_workspace(self, context, tool_name):
				return ""

		class Presenter:
			async def store_artifacts(self, result, context):
				raise RuntimeError("present failed")

		no_workspace = await BuiltinCommandTools(path_policy=Policy()).python_exec({"code": "print(1)"}, {})
		failing = await BuiltinCommandTools(path_policy=FailingPolicy(), presenter=Presenter()).python_exec(
			{"code": "print(1)"},
			{"command_executor": object()},
		)

		assert no_workspace.is_error
		assert failing.is_error

	@pytest.mark.asyncio
	async def test_shell_workspace_error_powershell_and_executor_error(self):
		class Policy:
			def get_workspace(self, context, tool_name):
				return ToolOutput.error("no workspace")

		class FailingExecutor:
			async def run(self, spec):
				raise RuntimeError("shell down")

		no_workspace = await BuiltinCommandTools(path_policy=Policy()).shell({"command": "echo x"}, {})
		failing = await _shell(
			{"command": "echo x", "shell_type": "powershell", "timeout": "bad"},
			{"allow_unsafe_workspace": True, "command_executor": FailingExecutor()},
		)

		assert no_workspace.is_error
		assert failing.is_error

	@pytest.mark.asyncio
	async def test_ensure_venv_existing_failure_and_helpers(self, tmp_path):
		venv = tmp_path / "venv"
		python_path = venv / "bin" / "python"
		python_path.parent.mkdir(parents=True)
		python_path.write_text("", encoding="utf-8")
		assert await ensure_venv(str(venv), {}) == str(python_path)

		class FailingExecutor:
			async def run(self, spec):
				return CommandResult(exit_code=1, stderr="venv failed")

		with pytest.raises(RuntimeError, match="venv failed"):
			await ensure_venv(str(tmp_path / "badvenv"), {"command_executor": FailingExecutor()})
		assert get_command_executor({"command_executor": "x"}) == "x"
		content, artifacts = await store_command_artifacts(
			CommandResult(exit_code=0, stdout="abcdef", stderr="ghijkl", stdout_truncated=True, stderr_truncated=True),
			{"result_store": InMemoryResultStore()},
			stdout_limit=3,
			stderr_limit=3,
		)
		assert content["stdout_artifact_id"]
		assert content["stderr_artifact_id"]
		assert len(artifacts) == 2
		assert bounded_int("bad", 1, 3, 2) == 2
		assert bounded_int(9, 1, 3, 2) == 3
		assert truncate_by_bytes("你好abc", 4) == "你"


class TestBuiltinFileToolExceptionBranches:
	@pytest.mark.asyncio
	async def test_path_policy_and_filesystem_exceptions(self, tmp_path, monkeypatch):
		class BadPolicy:
			def resolve_workspace_path(self, path, context):
				raise PathValidationError("bad path")

			def file_entry(self, full, context):
				raise RuntimeError("entry failed")

		tools = BuiltinFileTools(path_policy=BadPolicy())
		assert (await tools.read({"path": "x"}, {})).is_error
		assert (await tools.list({"path": "."}, {})).is_error
		assert (await tools.glob({"pattern": "*"}, {})).is_error
		assert (await tools.info({"path": "x"}, {})).is_error
		assert (await tools.write({"path": "x"}, {})).is_error
		assert (await tools.append({"path": "x"}, {})).is_error
		assert (await tools.edit({"path": "x", "old_string": "a"}, {})).is_error

		file_path = tmp_path / "readonly.txt"
		file_path.write_text("a", encoding="utf-8")
		monkeypatch.setattr("axc_agent_engine.plugins.builtin.builtin_tools.file_tools.os.replace", lambda src, dst: (_ for _ in ()).throw(RuntimeError("replace failed")))
		result = await _file_edit({"path": "readonly.txt", "old_string": "a", "new_string": "b"}, {"workspace": str(tmp_path)})
		assert result.is_error
		assert not (tmp_path / "readonly.txt.tmp").exists()
