"""Tests for ToolOutput protocol enforcement across the system."""
import pytest
from axc_agent_engine.tools.tool_output import ToolOutput, ArtifactRef
from axc_agent_engine.storage.artifact_store import ArtifactStore
from axc_agent_engine.storage.artifact_store import InMemoryArtifactStore
from axc_agent_engine.storage.protocols import ArtifactStore as ProtocolArtifactStore
from axc_agent_engine.tools.executor import ToolResult


class TestArtifactStoreProtocol:
	def test_inmemory_implements_protocol(self):
		store = InMemoryArtifactStore()
		assert isinstance(store, ArtifactStore)

	def test_inmemory_implements_storage_protocol(self):
		store = InMemoryArtifactStore()
		assert isinstance(store, ProtocolArtifactStore)

	@pytest.mark.asyncio
	async def test_custom_store_protocol(self):
		"""Custom ArtifactStore implementation works."""
		class CustomStore:
			def __init__(self):
				self._data = {}
			async def put_text(self, content, metadata=None, *, kind="text", run_id="", durable=False, expires_at=None):
				ref = ArtifactRef(id="custom1", kind="text", size=len(content))
				self._data["custom1"] = content
				return ref
			async def put_bytes(self, content, metadata=None, *, kind="binary", run_id="", durable=False, expires_at=None):
				return ArtifactRef(id="custom-bytes", kind=kind, size=len(content))
			async def put_file_ref(self, path, metadata=None, *, kind="file", run_id="", durable=False, expires_at=None):
				return ArtifactRef(id="custom-file", kind=kind, size=0)
			async def read(self, artifact_id, offset=0, limit=4000):
				from axc_agent_engine.storage.artifact_store import ArtifactRead
				content = self._data.get(artifact_id, "")[offset:offset+limit]
				return ArtifactRead(content=content, artifact_id=artifact_id, offset=offset, limit=limit, size=len(content))
			async def read_page(self, artifact_id, page=1, page_size=4000):
				return await self.read(artifact_id, (page - 1) * page_size, page_size)
			async def search(self, artifact_id, query, max_results=20):
				return []
			async def stat(self, artifact_id):
				return ArtifactRef(id=artifact_id, kind="text", size=len(self._data.get(artifact_id, "")))
			async def delete(self, artifact_id):
				self._data.pop(artifact_id, None)
			async def delete_run(self, run_id):
				return None
			async def gc(self, now=None):
				return {"deleted": 0}

		store = CustomStore()
		assert isinstance(store, ArtifactStore)
		ref = await store.put_text("test content")
		assert ref.id == "custom1"
		content = await store.read("custom1")
		assert content.content == "test content"


def test_tool_result_copies_mutable_arguments_and_output():
	arguments = {"nested": {"value": "original"}}
	output = ToolOutput(
		content={"rows": [{"id": 1}]},
		content_type="json",
		metadata={"source": {"id": "s1"}},
	)
	result = ToolResult(tool_call_id="1", tool_name="t", arguments=arguments, output=output)

	arguments["nested"]["value"] = "mutated"
	output.content["rows"][0]["id"] = 2
	output.metadata["source"]["id"] = "mutated"

	assert result.arguments == {"nested": {"value": "original"}}
	assert result.output.content == {"rows": [{"id": 1}]}
	assert result.output.metadata == {"source": {"id": "s1"}}


class TestToolOutputSerialization:
	def test_to_dict_with_all_fields(self):
		ref = ArtifactRef(id="a1", kind="file", size=1000, metadata={"path": "/x"})
		out = ToolOutput(
			content={"data": [1, 2, 3]},
			content_type="json",
			summary="3 items",
			llm_view="three items for llm",
			artifacts=[ref],
			metadata={"tool": "test"},
			is_error=False,
		)
		d = out.to_dict()
		assert d["content"] == {"data": [1, 2, 3]}
		assert d["content_type"] == "json"
		assert d["summary"] == "3 items"
		assert d["llm_view"] == "three items for llm"
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
			llm_view="complex for llm",
			artifacts=refs,
			metadata={"version": 2},
			is_error=False,
		)
		restored = ToolOutput.from_dict(out.to_dict())
		assert restored.content == out.content
		assert restored.llm_view == "complex for llm"
		assert len(restored.artifacts) == 5
		assert restored.metadata == {"version": 2}

	def test_views_handle_durable_summary_artifacts_and_tiny_limits(self):
		ref = ArtifactRef(id="a1", kind="text", size=12)
		out = ToolOutput(
			content="full content",
			summary="summary",
			artifacts=[ref],
			metadata={"durable_summary": "D" * 20},
		)
		assert out.context_view(max_chars=5).startswith("DDD")
		assert "text:a1(12B)" in out.context_view(max_chars=5)
		assert out.display_view(max_chars=4).startswith("ful")
		assert out.is_durable() is True
		assert out.durable_summary(max_chars=3).startswith("DD")

	def test_error_context_view_and_from_dict_defaults(self):
		out = ToolOutput.error("boom" * 100)
		assert out.context_view(max_chars=8) == "[错误] " + "boom" * 100
		restored = ToolOutput.from_dict({"content": "x"})
		assert restored.content_type == "text"
		assert restored.artifacts == []
		assert restored.metadata == {}


class TestToolResultProperties:
	def test_result_property_with_output(self):
		out = ToolOutput.text("hello")
		tr = ToolResult(tool_call_id="1", tool_name="t", arguments={}, output=out, success=True)
		assert tr.context_view() == "hello"

	def test_result_property_without_output(self):
		tr = ToolResult(tool_call_id="1", tool_name="t", arguments={}, success=False, error="err")
		assert tr.context_view() == ""

	def test_duration_ms(self):
		tr = ToolResult(tool_call_id="1", tool_name="t", arguments={}, success=True, duration_ms=150)
		assert tr.duration_ms == 150


class TestAllBuiltinToolsReturnToolOutput:
	"""Verify all registered builtin tools return ToolOutput."""

	@pytest.mark.asyncio
	async def test_get_time(self):
		from axc_agent_engine.plugins.builtin.builtin_tools.tool_definitions import _get_time
		result = await _get_time({}, {})
		assert isinstance(result, ToolOutput)

	@pytest.mark.asyncio
	async def test_file_read_error(self):
		from axc_agent_engine.plugins.builtin.builtin_tools.tool_definitions import _file_read
		result = await _file_read({"path": "/nonexistent"}, {})
		assert isinstance(result, ToolOutput)
		assert result.is_error

	@pytest.mark.asyncio
	async def test_file_write_error(self):
		from axc_agent_engine.plugins.builtin.builtin_tools.tool_definitions import _file_write
		result = await _file_write({"path": "", "content": "x"}, {})
		assert isinstance(result, ToolOutput)
		assert result.is_error

	@pytest.mark.asyncio
	async def test_file_edit_error(self):
		from axc_agent_engine.plugins.builtin.builtin_tools.tool_definitions import _file_edit
		result = await _file_edit({"path": "", "old_string": "a", "new_string": "b"}, {})
		assert isinstance(result, ToolOutput)
		assert result.is_error

	@pytest.mark.asyncio
	async def test_python_exec_empty(self):
		from axc_agent_engine.plugins.builtin.builtin_tools.tool_definitions import _python_exec
		result = await _python_exec({"code": ""}, {})
		assert isinstance(result, ToolOutput)
		assert result.is_error

	@pytest.mark.asyncio
	async def test_shell_empty(self):
		from axc_agent_engine.plugins.builtin.builtin_tools.tool_definitions import _shell
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
		from axc_agent_engine.plugins.builtin.builtin_tools.tool_definitions import _pip_install
		result = await _pip_install({"package": ""}, {})
		assert isinstance(result, ToolOutput)
		assert result.is_error

	@pytest.mark.asyncio
	async def test_artifact_read_no_store(self):
		from axc_agent_engine.plugins.builtin.builtin_tools.tool_definitions import _artifact_read
		result = await _artifact_read({"artifact_id": "x"}, {})
		assert isinstance(result, ToolOutput)
		assert result.is_error

	@pytest.mark.asyncio
	async def test_artifact_search_no_store(self):
		from axc_agent_engine.plugins.builtin.builtin_tools.tool_definitions import _artifact_search
		result = await _artifact_search({"artifact_id": "x", "query": "q"}, {})
		assert isinstance(result, ToolOutput)
		assert result.is_error

	@pytest.mark.asyncio
	async def test_artifact_page_no_store(self):
		from axc_agent_engine.plugins.builtin.builtin_tools.tool_definitions import _artifact_page
		result = await _artifact_page({"artifact_id": "x"}, {})
		assert isinstance(result, ToolOutput)
		assert result.is_error


class TestContextViewDoesNotCallLLM:
	"""Verify context_view is pure computation, no LLM calls."""

	def test_context_view_is_deterministic(self):
		out = ToolOutput.text("x" * 5000)
		view1 = out.context_view()
		view2 = out.context_view()
		assert view1 == view2

	def test_context_view_with_summary_is_instant(self):
		out = ToolOutput.text("x" * 100000, summary="short")
		view = out.context_view()
		assert view == "x" * 100000

	def test_context_view_json_is_deterministic(self):
		data = {"items": list(range(1000))}
		out = ToolOutput.json_output(data)
		view1 = out.context_view(max_chars=500)
		view2 = out.context_view(max_chars=500)
		assert view1 == view2
