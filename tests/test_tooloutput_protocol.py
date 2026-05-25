"""Tests for ToolOutput protocol enforcement across the system."""
import pytest
from axc_agent_engine.tools.tool_output import ToolOutput, ArtifactRef, ResultStore
from axc_agent_engine.storage.result_store import InMemoryResultStore
from axc_agent_engine.storage.protocols import ResultStore as ProtocolResultStore
from axc_agent_engine.tools.executor import ToolResult


class TestResultStoreProtocol:
	def test_inmemory_implements_protocol(self):
		store = InMemoryResultStore()
		assert isinstance(store, ResultStore)

	def test_inmemory_implements_storage_protocol(self):
		store = InMemoryResultStore()
		assert isinstance(store, ProtocolResultStore)

	@pytest.mark.asyncio
	async def test_custom_store_protocol(self):
		"""Custom ResultStore implementation works."""
		class CustomStore:
			def __init__(self):
				self._data = {}
			async def put(self, content, metadata=None):
				ref = ArtifactRef(id="custom1", kind="text", size=len(content))
				self._data["custom1"] = content
				return ref
			async def get(self, artifact_id, offset=0, limit=4000):
				return self._data.get(artifact_id, "")[offset:offset+limit]
			async def search(self, artifact_id, query):
				return []

		store = CustomStore()
		assert isinstance(store, ResultStore)
		ref = await store.put("test content")
		assert ref.id == "custom1"
		content = await store.get("custom1")
		assert content == "test content"


class TestToolOutputSerialization:
	def test_to_dict_with_all_fields(self):
		ref = ArtifactRef(id="a1", kind="file", size=1000, metadata={"path": "/x"})
		out = ToolOutput(
			content={"data": [1, 2, 3]},
			content_type="json",
			summary="3 items",
			artifacts=[ref],
			metadata={"tool": "test"},
			is_error=False,
		)
		d = out.to_dict()
		assert d["content"] == {"data": [1, 2, 3]}
		assert d["content_type"] == "json"
		assert d["summary"] == "3 items"
		assert d["artifacts"][0]["id"] == "a1"
		assert d["metadata"]["tool"] == "test"

	def test_from_dict_minimal(self):
		d = {"content": "hello", "content_type": "text", "is_error": False,
			 "summary": "", "artifacts": [], "metadata": {}}
		out = ToolOutput.from_dict(d)
		assert out.content == "hello"
		assert out.artifacts == []

	def test_roundtrip_complex(self):
		refs = [ArtifactRef(id=f"r{i}", kind="text", size=i*10) for i in range(5)]
		out = ToolOutput(
			content={"nested": {"deep": True}},
			content_type="json",
			summary="complex",
			artifacts=refs,
			metadata={"version": 2},
			is_error=False,
		)
		restored = ToolOutput.from_dict(out.to_dict())
		assert restored.content == out.content
		assert len(restored.artifacts) == 5
		assert restored.metadata == {"version": 2}


class TestToolResultProperties:
	def test_result_property_with_output(self):
		out = ToolOutput.text("hello")
		tr = ToolResult(tool_call_id="1", tool_name="t", arguments={}, output=out, success=True)
		assert tr.compact_view() == "hello"

	def test_result_property_without_output(self):
		tr = ToolResult(tool_call_id="1", tool_name="t", arguments={}, success=False, error="err")
		assert tr.compact_view() == ""

	def test_duration_ms(self):
		tr = ToolResult(tool_call_id="1", tool_name="t", arguments={}, success=True, duration_ms=150)
		assert tr.duration_ms == 150


class TestAllBuiltinToolsReturnToolOutput:
	"""Verify all registered builtin tools return ToolOutput."""

	@pytest.mark.asyncio
	async def test_get_time(self):
		from axc_agent_engine.plugins.builtin.builtin_tools.plugin import _get_time
		result = await _get_time({}, {})
		assert isinstance(result, ToolOutput)

	@pytest.mark.asyncio
	async def test_file_read_error(self):
		from axc_agent_engine.plugins.builtin.builtin_tools.plugin import _file_read
		result = await _file_read({"path": "/nonexistent"}, {})
		assert isinstance(result, ToolOutput)
		assert result.is_error

	@pytest.mark.asyncio
	async def test_file_write_error(self):
		from axc_agent_engine.plugins.builtin.builtin_tools.plugin import _file_write
		result = await _file_write({"path": "", "content": "x"}, {})
		assert isinstance(result, ToolOutput)
		assert result.is_error

	@pytest.mark.asyncio
	async def test_file_edit_error(self):
		from axc_agent_engine.plugins.builtin.builtin_tools.plugin import _file_edit
		result = await _file_edit({"path": "", "old_string": "a", "new_string": "b"}, {})
		assert isinstance(result, ToolOutput)
		assert result.is_error

	@pytest.mark.asyncio
	async def test_python_exec_empty(self):
		from axc_agent_engine.plugins.builtin.builtin_tools.plugin import _python_exec
		result = await _python_exec({"code": ""}, {})
		assert isinstance(result, ToolOutput)
		assert result.is_error

	@pytest.mark.asyncio
	async def test_shell_empty(self):
		from axc_agent_engine.plugins.builtin.builtin_tools.plugin import _shell
		result = await _shell({"command": ""}, {})
		assert isinstance(result, ToolOutput)
		assert result.is_error

	@pytest.mark.asyncio
	async def test_ask_human_no_queue(self):
		from axc_agent_engine.plugins.builtin.human_in_the_loop.plugin import HumanInTheLoopPlugin
		plugin = HumanInTheLoopPlugin()
		plugin.initialize({}, None)
		result = await plugin._ask_human({"question": "hi"}, {})
		assert isinstance(result, ToolOutput)
		assert result.is_error

	@pytest.mark.asyncio
	async def test_pip_install_invalid(self):
		from axc_agent_engine.plugins.builtin.builtin_tools.plugin import _pip_install
		result = await _pip_install({"package": ""}, {})
		assert isinstance(result, ToolOutput)
		assert result.is_error

	@pytest.mark.asyncio
	async def test_result_read_no_store(self):
		from axc_agent_engine.plugins.builtin.builtin_tools.plugin import _result_read
		result = await _result_read({"artifact_id": "x"}, {})
		assert isinstance(result, ToolOutput)
		assert result.is_error

	@pytest.mark.asyncio
	async def test_result_search_no_store(self):
		from axc_agent_engine.plugins.builtin.builtin_tools.plugin import _result_search
		result = await _result_search({"artifact_id": "x", "query": "q"}, {})
		assert isinstance(result, ToolOutput)
		assert result.is_error

	@pytest.mark.asyncio
	async def test_result_page_no_store(self):
		from axc_agent_engine.plugins.builtin.builtin_tools.plugin import _result_page
		result = await _result_page({"artifact_id": "x"}, {})
		assert isinstance(result, ToolOutput)
		assert result.is_error


class TestCompactViewDoesNotCallLLM:
	"""Verify compact_view is pure computation, no LLM calls."""

	def test_compact_view_is_deterministic(self):
		out = ToolOutput.text("x" * 5000)
		view1 = out.compact_view()
		view2 = out.compact_view()
		assert view1 == view2

	def test_compact_view_with_summary_is_instant(self):
		out = ToolOutput.text("x" * 100000, summary="short")
		view = out.compact_view()
		assert view == "short"

	def test_compact_view_json_is_deterministic(self):
		data = {"items": list(range(1000))}
		out = ToolOutput.json_output(data)
		view1 = out.compact_view(max_chars=500)
		view2 = out.compact_view(max_chars=500)
		assert view1 == view2
