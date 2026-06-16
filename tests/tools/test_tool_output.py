"""Tests for ToolOutput, ArtifactRef, and ArtifactStore protocol."""
import pytest
from axc_agent_engine.tools.tool_output import ToolOutput, ArtifactRef, generate_artifact_id
from axc_agent_engine.storage.artifact_store import ArtifactStore
from axc_agent_engine.storage.artifact_store import InMemoryArtifactStore


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

	def test_direct_creation_copies_metadata(self):
		metadata = {"nested": {"value": "original"}}
		ref = ArtifactRef(id="x", kind="json", size=200, metadata=metadata)

		metadata["nested"]["value"] = "mutated"

		assert ref.metadata == {"nested": {"value": "original"}}

	def test_to_dict(self):
		ref = ArtifactRef(id="x", kind="json", size=200, metadata={"k": "v"})
		d = ref.to_dict()
		assert d == {"id": "x", "kind": "json", "size": 200, "metadata": {"k": "v"}}

	def test_to_dict_copies_metadata(self):
		ref = ArtifactRef(id="x", kind="json", size=200, metadata={"k": "v"})
		d = ref.to_dict()

		ref.metadata["k"] = "mutated"

		assert d["metadata"] == {"k": "v"}

	def test_from_dict(self):
		d = {"id": "y", "kind": "binary", "size": 300, "metadata": {"a": 1}}
		ref = ArtifactRef.from_dict(d)
		assert ref.id == "y"
		assert ref.kind == "binary"
		assert ref.size == 300
		assert ref.metadata == {"a": 1}

	def test_from_dict_copies_metadata(self):
		d = {"id": "y", "kind": "binary", "size": 300, "metadata": {"a": 1}}
		ref = ArtifactRef.from_dict(d)

		d["metadata"]["a"] = 2

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

	def test_factories_accept_llm_view(self):
		assert ToolOutput.text("full", llm_view="llm").context_view() == "llm"
		assert ToolOutput.json_output({"full": True}, llm_view="llm").context_view() == "llm"

	def test_error_factory(self):
		out = ToolOutput.error("bad thing")
		assert out.content == "bad thing"
		assert out.content_type == "error"
		assert out.is_error is True

	def test_context_view_short_text(self):
		out = ToolOutput.text("short")
		assert out.context_view() == "short"

	def test_context_view_prefers_durable_summary_over_llm_view_and_content(self):
		out = ToolOutput.text("full", summary="summary").with_metadata({"durable_summary": "durable"})
		assert out.context_view() == "durable"
		assert ToolOutput.text("full", summary="summary").context_view() == "full"

	def test_context_view_prefers_explicit_llm_view_after_durable_summary(self):
		out = ToolOutput(content={"rows": [{"id": 1}]}, content_type="json", summary="summary", llm_view="row 1")
		assert out.context_view() == "row 1"
		assert out.display_view() == '{"rows": [{"id": 1}]}'

		durable = out.with_metadata({"durable_summary": "durable"})
		assert durable.context_view() == "durable"
		assert durable.llm_view == "row 1"

	def test_llm_view_is_copied(self):
		out = ToolOutput(content="full", llm_view=123)
		assert out.llm_view == "123"

	def test_display_view_returns_full_content(self):
		long_text = "x" * 5000
		out = ToolOutput.text(long_text, summary="summary")
		assert out.display_view() == long_text

	def test_context_view_does_not_use_summary_as_default_llm_content(self):
		out = ToolOutput.text("very long " * 500, summary="brief summary")
		assert out.context_view() == "very long " * 500

	def test_context_view_does_not_truncate_by_default(self):
		long_text = "x" * 5000
		out = ToolOutput.text(long_text)
		assert out.context_view() == long_text

	def test_context_view_ignores_max_chars(self):
		long_text = "x" * 5000
		out = ToolOutput.text(long_text)
		view = out.context_view(max_chars=200)
		assert view == long_text

	def test_context_view_error(self):
		out = ToolOutput.error("oops")
		view = out.context_view()
		assert "[错误]" in view
		assert "oops" in view

	def test_context_view_with_artifacts(self):
		ref = ArtifactRef(id="abc", kind="text", size=5000)
		out = ToolOutput(content="data", content_type="text", artifacts=[ref])
		view = out.context_view()
		assert "abc" in view
		assert "附件" in view

	def test_direct_creation_copies_content_metadata_and_artifacts(self):
		ref = ArtifactRef(id="r1", kind="text", size=10, metadata={"source": {"id": "s1"}})
		content = {"rows": [{"id": 1}]}
		metadata = {"nested": {"value": "original"}}
		out = ToolOutput(content=content, content_type="json", artifacts=[ref], metadata=metadata)

		content["rows"][0]["id"] = 2
		metadata["nested"]["value"] = "mutated"
		ref.metadata["source"]["id"] = "mutated"

		assert out.content == {"rows": [{"id": 1}]}
		assert out.metadata == {"nested": {"value": "original"}}
		assert out.artifacts[0].metadata == {"source": {"id": "s1"}}

	def test_with_metadata_copies_content_metadata_and_artifacts(self):
		ref = ArtifactRef(id="a", kind="text", size=1, metadata={"source": {"id": "s1"}})
		out = ToolOutput(
			content={"rows": [{"id": 1}]},
			content_type="json",
			artifacts=[ref],
			metadata={"existing": {"value": 1}},
		)
		extra = {"new": {"value": 2}}

		merged = out.with_metadata(extra)
		out.content["rows"][0]["id"] = 2
		out.metadata["existing"]["value"] = 3
		ref.metadata["source"]["id"] = "mutated"
		extra["new"]["value"] = 4

		assert merged.content == {"rows": [{"id": 1}]}
		assert merged.metadata == {"existing": {"value": 1}, "new": {"value": 2}}
		assert merged.artifacts[0].metadata == {"source": {"id": "s1"}}

	def test_context_view_json_content(self):
		out = ToolOutput.json_output({"status": 200, "body": "ok"})
		view = out.context_view()
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

	def test_to_dict_copies_content_metadata_and_artifacts(self):
		ref = ArtifactRef(id="r1", kind="text", size=10, metadata={"source": "s1"})
		out = ToolOutput(
			content={"rows": [{"id": 1}]},
			content_type="json",
			artifacts=[ref],
			metadata={"k": {"nested": 1}},
		)
		d = out.to_dict()

		out.content["rows"][0]["id"] = 2
		out.metadata["k"]["nested"] = 2
		ref.metadata["source"] = "mutated"

		assert d["content"] == {"rows": [{"id": 1}]}
		assert d["metadata"] == {"k": {"nested": 1}}
		assert d["artifacts"][0]["metadata"] == {"source": "s1"}

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

	def test_from_dict_copies_content_and_metadata(self):
		d = {
			"content": {"x": [1]},
			"content_type": "json",
			"metadata": {"k": {"nested": 1}},
			"artifacts": [{"id": "a", "kind": "json", "size": 5, "metadata": {"source": "s1"}}],
		}
		out = ToolOutput.from_dict(d)

		d["content"]["x"].append(2)
		d["metadata"]["k"]["nested"] = 2
		d["artifacts"][0]["metadata"]["source"] = "mutated"

		assert out.content == {"x": [1]}
		assert out.metadata == {"k": {"nested": 1}}
		assert out.artifacts[0].metadata == {"source": "s1"}

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


class TestInMemoryArtifactStore:
	@pytest.mark.asyncio
	async def test_put_and_get(self):
		store = InMemoryArtifactStore()
		ref = await store.put_text("hello world")
		assert ref.size == 11
		content = await store.read(ref.id)
		assert content.content == "hello world"

	@pytest.mark.asyncio
	async def test_get_with_offset(self):
		store = InMemoryArtifactStore()
		ref = await store.put_text("0123456789")
		content = await store.read(ref.id, offset=5)
		assert content.content == "56789"

	@pytest.mark.asyncio
	async def test_get_with_limit(self):
		store = InMemoryArtifactStore()
		ref = await store.put_text("0123456789")
		content = await store.read(ref.id, offset=0, limit=3)
		assert content.content == "012"

	@pytest.mark.asyncio
	async def test_get_with_offset_and_limit(self):
		store = InMemoryArtifactStore()
		ref = await store.put_text("abcdefghij")
		content = await store.read(ref.id, offset=2, limit=4)
		assert content.content == "cdef"

	@pytest.mark.asyncio
	async def test_get_nonexistent(self):
		store = InMemoryArtifactStore()
		content = await store.read("nonexistent")
		assert content.content == ""

	@pytest.mark.asyncio
	async def test_search_found(self):
		store = InMemoryArtifactStore()
		ref = await store.put_text("line1\nline2 hello\nline3\nline4 hello world")
		results = await store.search(ref.id, "hello")
		assert len(results) == 2
		assert results[0].line == 2
		assert results[1].line == 4

	@pytest.mark.asyncio
	async def test_search_not_found(self):
		store = InMemoryArtifactStore()
		ref = await store.put_text("nothing here")
		results = await store.search(ref.id, "xyz")
		assert results == []

	@pytest.mark.asyncio
	async def test_search_nonexistent_artifact(self):
		store = InMemoryArtifactStore()
		results = await store.search("nope", "query")
		assert results == []

	@pytest.mark.asyncio
	async def test_search_case_insensitive(self):
		store = InMemoryArtifactStore()
		ref = await store.put_text("Hello World\nGoodbye")
		results = await store.search(ref.id, "hello")
		assert len(results) == 1

	@pytest.mark.asyncio
	async def test_search_max_results(self):
		store = InMemoryArtifactStore()
		content = "\n".join(f"match line {i}" for i in range(50))
		ref = await store.put_text(content)
		results = await store.search(ref.id, "match")
		assert len(results) == 20  # capped at 20

	@pytest.mark.asyncio
	async def test_eviction(self):
		store = InMemoryArtifactStore(max_entries=3)
		refs = []
		for i in range(5):
			refs.append(await store.put_text(f"content_{i}"))
		# First 2 should be evicted
		assert (await store.read(refs[0].id)).content == ""
		assert (await store.read(refs[1].id)).content == ""
		assert (await store.read(refs[4].id)).content == "content_4"

	@pytest.mark.asyncio
	async def test_byte_size_eviction(self):
		store = InMemoryArtifactStore(max_entries=10, max_bytes=8)
		ref1 = await store.put_text("12345")
		ref2 = await store.put_text("67890")
		assert (await store.read(ref1.id)).content == ""
		assert (await store.read(ref2.id)).content == "67890"
		assert store.stats()["inline_bytes"] <= 8

	@pytest.mark.asyncio
	async def test_ttl_expiry(self):
		store = InMemoryArtifactStore(default_ttl=1)
		ref = await store.put_text("short lived", expires_at=0)
		assert (await store.read(ref.id)).content == ""
		assert store.has(ref.id) is False

	@pytest.mark.asyncio
	async def test_delete(self):
		store = InMemoryArtifactStore()
		ref = await store.put_text("delete me")
		await store.delete(ref.id)
		assert (await store.read(ref.id)).content == ""
		assert store.stats()["entries"] == 0

	def test_stats(self):
		store = InMemoryArtifactStore(max_entries=7, max_bytes=1024, default_ttl=9)
		stats = store.stats()
		assert stats["entries"] == 0
		assert stats["max_entries"] == 7
		assert stats["max_bytes"] == 1024
		assert stats["default_ttl"] == 9

	@pytest.mark.asyncio
	async def test_has(self):
		store = InMemoryArtifactStore()
		ref = await store.put_text("data")
		assert store.has(ref.id) is True
		assert store.has("nonexistent") is False

	@pytest.mark.asyncio
	async def test_put_bytes(self):
		store = InMemoryArtifactStore()
		ref = await store.put_bytes(b"binary data")
		assert ref.kind == "binary"
		content = await store.read(ref.id)
		assert content.content == "binary data"

	@pytest.mark.asyncio
	async def test_put_with_metadata(self):
		store = InMemoryArtifactStore()
		ref = await store.put_text("content", {"path": "/a.txt"}, kind="file")
		assert ref.kind == "file"

	@pytest.mark.asyncio
	async def test_protocol_compliance(self):
		"""InMemoryArtifactStore satisfies ArtifactStore protocol."""
		store = InMemoryArtifactStore()
		assert isinstance(store, ArtifactStore)


class TestToolOutputContextViewEdgeCases:
	def test_empty_content(self):
		out = ToolOutput.text("")
		assert out.context_view() == ""

	def test_none_like_content(self):
		out = ToolOutput(content="", content_type="text")
		assert out.context_view() == ""

	def test_max_chars_zero(self):
		out = ToolOutput.text("hello")
		view = out.context_view(max_chars=0)
		assert view == "hello"

	def test_multiple_artifacts(self):
		refs = [
			ArtifactRef(id="a1", kind="text", size=100),
			ArtifactRef(id="a2", kind="file", size=200),
		]
		out = ToolOutput(content="data", content_type="text", artifacts=refs)
		view = out.context_view()
		assert "a1" in view
		assert "a2" in view
