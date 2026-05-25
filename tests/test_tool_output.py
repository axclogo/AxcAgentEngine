"""Tests for ToolOutput, ArtifactRef, and ResultStore protocol."""
import pytest
from axc_agent_engine.tools.tool_output import ToolOutput, ArtifactRef, generate_artifact_id, ResultStore
from axc_agent_engine.storage.result_store import InMemoryResultStore


class TestArtifactRef:
	def test_create(self):
		ref = ArtifactRef(id="abc123", kind="text", size=100)
		assert ref.id == "abc123"
		assert ref.kind == "text"
		assert ref.size == 100
		assert ref.metadata == {}

	def test_with_metadata(self):
		ref = ArtifactRef(id="x", kind="file", size=50, metadata={"path": "/a.txt"})
		assert ref.metadata["path"] == "/a.txt"

	def test_to_dict(self):
		ref = ArtifactRef(id="x", kind="json", size=200, metadata={"k": "v"})
		d = ref.to_dict()
		assert d == {"id": "x", "kind": "json", "size": 200, "metadata": {"k": "v"}}

	def test_from_dict(self):
		d = {"id": "y", "kind": "binary", "size": 300, "metadata": {"a": 1}}
		ref = ArtifactRef.from_dict(d)
		assert ref.id == "y"
		assert ref.kind == "binary"
		assert ref.size == 300
		assert ref.metadata == {"a": 1}

	def test_from_dict_no_metadata(self):
		d = {"id": "z", "kind": "text", "size": 10}
		ref = ArtifactRef.from_dict(d)
		assert ref.metadata == {}

	def test_roundtrip(self):
		ref = ArtifactRef(id="rt", kind="table", size=999, metadata={"x": [1, 2]})
		assert ArtifactRef.from_dict(ref.to_dict()) == ref


class TestToolOutput:
	def test_text_factory(self):
		out = ToolOutput.text("hello")
		assert out.content == "hello"
		assert out.content_type == "text"
		assert out.is_error is False
		assert out.artifacts == []

	def test_text_with_summary(self):
		out = ToolOutput.text("long content", summary="short")
		assert out.summary == "short"

	def test_json_output_factory(self):
		out = ToolOutput.json_output({"key": "val"})
		assert out.content == {"key": "val"}
		assert out.content_type == "json"

	def test_json_output_list(self):
		out = ToolOutput.json_output([1, 2, 3])
		assert out.content == [1, 2, 3]

	def test_error_factory(self):
		out = ToolOutput.error("bad thing")
		assert out.content == "bad thing"
		assert out.content_type == "error"
		assert out.is_error is True

	def test_compact_view_short_text(self):
		out = ToolOutput.text("short")
		assert out.compact_view() == "short"

	def test_compact_view_uses_summary(self):
		out = ToolOutput.text("very long " * 500, summary="brief summary")
		assert out.compact_view() == "brief summary"

	def test_compact_view_truncates_long(self):
		long_text = "x" * 5000
		out = ToolOutput.text(long_text)
		view = out.compact_view(max_chars=200)
		assert len(view) <= 250  # some overhead for marker
		assert "omitted" in view

	def test_compact_view_error(self):
		out = ToolOutput.error("oops")
		view = out.compact_view()
		assert "[Error]" in view
		assert "oops" in view

	def test_compact_view_with_artifacts(self):
		ref = ArtifactRef(id="abc", kind="text", size=5000)
		out = ToolOutput(content="data", content_type="text", artifacts=[ref])
		view = out.compact_view()
		assert "abc" in view
		assert "artifacts" in view

	def test_compact_view_json_content(self):
		out = ToolOutput.json_output({"status": 200, "body": "ok"})
		view = out.compact_view()
		assert "200" in view

	def test_to_dict(self):
		ref = ArtifactRef(id="r1", kind="text", size=10)
		out = ToolOutput(content="hi", content_type="text", summary="s", artifacts=[ref], metadata={"k": 1})
		d = out.to_dict()
		assert d["content"] == "hi"
		assert d["content_type"] == "text"
		assert d["summary"] == "s"
		assert len(d["artifacts"]) == 1
		assert d["metadata"] == {"k": 1}
		assert d["is_error"] is False

	def test_from_dict(self):
		d = {
			"content": {"x": 1},
			"content_type": "json",
			"summary": "sum",
			"artifacts": [{"id": "a", "kind": "json", "size": 5, "metadata": {}}],
			"metadata": {},
			"is_error": False,
		}
		out = ToolOutput.from_dict(d)
		assert out.content == {"x": 1}
		assert out.content_type == "json"
		assert len(out.artifacts) == 1
		assert out.artifacts[0].id == "a"

	def test_roundtrip(self):
		out = ToolOutput(content="test", content_type="text", summary="s",
						 artifacts=[ArtifactRef(id="x", kind="text", size=4)],
						 metadata={"m": True}, is_error=False)
		assert ToolOutput.from_dict(out.to_dict()).content == "test"

	def test_content_as_str_dict(self):
		out = ToolOutput.json_output({"a": 1})
		s = out._content_as_str()
		assert '"a"' in s

	def test_content_as_str_list(self):
		out = ToolOutput.json_output([1, 2])
		s = out._content_as_str()
		assert "[1, 2]" in s


class TestGenerateArtifactId:
	def test_length(self):
		aid = generate_artifact_id()
		assert len(aid) == 16

	def test_unique(self):
		ids = {generate_artifact_id() for _ in range(100)}
		assert len(ids) == 100


class TestInMemoryResultStore:
	@pytest.mark.asyncio
	async def test_put_and_get(self):
		store = InMemoryResultStore()
		ref = await store.put("hello world")
		assert ref.size == 11
		content = await store.get(ref.id)
		assert content == "hello world"

	@pytest.mark.asyncio
	async def test_get_with_offset(self):
		store = InMemoryResultStore()
		ref = await store.put("0123456789")
		content = await store.get(ref.id, offset=5)
		assert content == "56789"

	@pytest.mark.asyncio
	async def test_get_with_limit(self):
		store = InMemoryResultStore()
		ref = await store.put("0123456789")
		content = await store.get(ref.id, offset=0, limit=3)
		assert content == "012"

	@pytest.mark.asyncio
	async def test_get_with_offset_and_limit(self):
		store = InMemoryResultStore()
		ref = await store.put("abcdefghij")
		content = await store.get(ref.id, offset=2, limit=4)
		assert content == "cdef"

	@pytest.mark.asyncio
	async def test_get_nonexistent(self):
		store = InMemoryResultStore()
		content = await store.get("nonexistent")
		assert content == ""

	@pytest.mark.asyncio
	async def test_search_found(self):
		store = InMemoryResultStore()
		ref = await store.put("line1\nline2 hello\nline3\nline4 hello world")
		results = await store.search(ref.id, "hello")
		assert len(results) == 2
		assert results[0]["line"] == 2
		assert results[1]["line"] == 4

	@pytest.mark.asyncio
	async def test_search_not_found(self):
		store = InMemoryResultStore()
		ref = await store.put("nothing here")
		results = await store.search(ref.id, "xyz")
		assert results == []

	@pytest.mark.asyncio
	async def test_search_nonexistent_artifact(self):
		store = InMemoryResultStore()
		results = await store.search("nope", "query")
		assert results == []

	@pytest.mark.asyncio
	async def test_search_case_insensitive(self):
		store = InMemoryResultStore()
		ref = await store.put("Hello World\nGoodbye")
		results = await store.search(ref.id, "hello")
		assert len(results) == 1

	@pytest.mark.asyncio
	async def test_search_max_results(self):
		store = InMemoryResultStore()
		content = "\n".join(f"match line {i}" for i in range(50))
		ref = await store.put(content)
		results = await store.search(ref.id, "match")
		assert len(results) == 20  # capped at 20

	@pytest.mark.asyncio
	async def test_eviction(self):
		store = InMemoryResultStore(max_entries=3)
		refs = []
		for i in range(5):
			refs.append(await store.put(f"content_{i}"))
		# First 2 should be evicted
		assert await store.get(refs[0].id) == ""
		assert await store.get(refs[1].id) == ""
		assert await store.get(refs[4].id) == "content_4"

	@pytest.mark.asyncio
	async def test_byte_size_eviction(self):
		store = InMemoryResultStore(max_entries=10, max_bytes=8)
		ref1 = await store.put("12345")
		ref2 = await store.put("67890")
		assert await store.get(ref1.id) == ""
		assert await store.get(ref2.id) == "67890"
		assert store.stats()["total_bytes"] <= 8

	@pytest.mark.asyncio
	async def test_ttl_expiry(self):
		store = InMemoryResultStore(ttl=1)
		ref = await store.put("short lived")
		store._store[ref.id]["created_at"] -= 2
		assert await store.get(ref.id) == ""
		assert store.has(ref.id) is False

	@pytest.mark.asyncio
	async def test_delete(self):
		store = InMemoryResultStore()
		ref = await store.put("delete me")
		await store.delete(ref.id)
		assert await store.get(ref.id) == ""
		assert store.stats()["entries"] == 0

	def test_stats(self):
		store = InMemoryResultStore(max_entries=7, max_bytes=1024, ttl=9)
		stats = store.stats()
		assert stats["entries"] == 0
		assert stats["max_entries"] == 7
		assert stats["max_bytes"] == 1024
		assert stats["ttl"] == 9

	@pytest.mark.asyncio
	async def test_has(self):
		store = InMemoryResultStore()
		ref = await store.put("data")
		assert store.has(ref.id) is True
		assert store.has("nonexistent") is False

	@pytest.mark.asyncio
	async def test_put_bytes(self):
		store = InMemoryResultStore()
		ref = await store.put(b"binary data")
		assert ref.kind == "binary"
		content = await store.get(ref.id)
		assert content == "binary data"

	@pytest.mark.asyncio
	async def test_put_with_metadata(self):
		store = InMemoryResultStore()
		ref = await store.put("content", {"kind": "file", "path": "/a.txt"})
		assert ref.kind == "file"

	@pytest.mark.asyncio
	async def test_protocol_compliance(self):
		"""InMemoryResultStore satisfies ResultStore protocol."""
		store = InMemoryResultStore()
		assert isinstance(store, ResultStore)


class TestToolOutputCompactViewEdgeCases:
	def test_empty_content(self):
		out = ToolOutput.text("")
		assert out.compact_view() == ""

	def test_none_like_content(self):
		out = ToolOutput(content="", content_type="text")
		assert out.compact_view() == ""

	def test_max_chars_zero(self):
		out = ToolOutput.text("hello")
		view = out.compact_view(max_chars=0)
		assert "omitted" in view or view == ""

	def test_multiple_artifacts(self):
		refs = [
			ArtifactRef(id="a1", kind="text", size=100),
			ArtifactRef(id="a2", kind="file", size=200),
		]
		out = ToolOutput(content="data", content_type="text", artifacts=refs)
		view = out.compact_view()
		assert "a1" in view
		assert "a2" in view
