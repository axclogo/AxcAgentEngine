import os
import time

import pytest

from axc_agent_engine.storage.artifact_store import ArtifactStore, InMemoryArtifactStore


class TestInMemoryArtifactStore:
	@pytest.mark.asyncio
	async def test_put_text_read_page_search_and_stat(self):
		store = InMemoryArtifactStore(default_ttl=0)
		ref = await store.put_text("alpha\nbeta\nalphabet", {"source": "test"}, run_id="run-1")

		assert isinstance(store, ArtifactStore)
		assert ref.kind == "text"
		assert ref.metadata["run_id"] == "run-1"
		assert (await store.read(ref.id, offset=0, limit=5)).content == "alpha"
		assert (await store.read_page(ref.id, page=2, page_size=5)).content == "\nbeta"
		matches = await store.search(ref.id, "alpha")
		assert [match.to_dict()["line"] for match in matches] == [1, 3]
		assert (await store.stat(ref.id)).metadata["source"] == "test"

	@pytest.mark.asyncio
	async def test_put_bytes_reads_as_utf8_replacement_text(self):
		store = InMemoryArtifactStore(default_ttl=0)
		ref = await store.put_bytes(b"abc\xffdef", kind="binary")

		result = await store.read(ref.id, limit=20)

		assert result.content == "abc\ufffddef"
		assert result.eof is True
		assert result.next_offset is None

	@pytest.mark.asyncio
	async def test_put_file_ref_does_not_copy_file_content(self, tmp_path):
		path = tmp_path / "large.log"
		path.write_text("line one\nneedle line\nlast", encoding="utf-8")
		store = InMemoryArtifactStore(default_ttl=0)

		ref = await store.put_file_ref(str(path), {"logical_path": "large.log"}, run_id="run-file")
		path.write_text("changed\nneedle after register", encoding="utf-8")

		assert ref.size == len("line one\nneedle line\nlast".encode())
		assert (await store.read(ref.id, limit=7)).content == "changed"
		matches = await store.search(ref.id, "needle")
		assert matches[0].text == "needle after register"
		assert store.stats()["inline_bytes"] == 0

	@pytest.mark.asyncio
	async def test_missing_artifact_returns_empty_read_and_none_stat(self):
		store = InMemoryArtifactStore(default_ttl=0)

		assert (await store.read("missing")).content == ""
		assert await store.search("missing", "x") == []
		assert await store.stat("missing") is None

	@pytest.mark.asyncio
	async def test_ttl_gc_expires_non_durable_artifacts(self):
		store = InMemoryArtifactStore(default_ttl=0)
		ref = await store.put_text("old", expires_at=time.time() - 1)

		report = await store.gc()

		assert report["deleted"] == 1
		assert await store.stat(ref.id) is None

	@pytest.mark.asyncio
	async def test_durable_artifact_survives_ttl_and_delete_run(self):
		store = InMemoryArtifactStore(default_ttl=0)
		ref = await store.put_text("keep", run_id="run-1", durable=True, expires_at=time.time() - 1)
		temp = await store.put_text("drop", run_id="run-1")

		await store.gc()
		await store.delete_run("run-1")

		assert await store.stat(ref.id) is not None
		assert await store.stat(temp.id) is None

	@pytest.mark.asyncio
	async def test_capacity_eviction_skips_durable_when_possible(self):
		store = InMemoryArtifactStore(max_entries=2, default_ttl=0)
		durable = await store.put_text("durable", durable=True)
		first = await store.put_text("first")
		second = await store.put_text("second")

		assert await store.stat(durable.id) is not None
		assert await store.stat(first.id) is None
		assert await store.stat(second.id) is not None

	@pytest.mark.asyncio
	async def test_delete_removes_inline_byte_accounting(self):
		store = InMemoryArtifactStore(default_ttl=0)
		ref = await store.put_text("abcdef")
		assert store.stats()["inline_bytes"] == 6

		await store.delete(ref.id)

		assert store.stats()["inline_bytes"] == 0
		assert not store.has(ref.id)

	@pytest.mark.asyncio
	async def test_file_ref_requires_existing_file(self, tmp_path):
		store = InMemoryArtifactStore(default_ttl=0)

		with pytest.raises(FileNotFoundError):
			await store.put_file_ref(os.fspath(tmp_path / "missing.txt"))
