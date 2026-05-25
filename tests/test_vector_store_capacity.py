"""Tests for #20 InMemoryVectorStore capacity limit change."""
import pytest
from axc_agent_engine.storage.in_memory import InMemoryVectorStore


class TestVectorStoreCapacity:
	@pytest.mark.asyncio
	async def test_default_max_entries_is_1000(self):
		store = InMemoryVectorStore()
		assert store._max_entries == 1000

	@pytest.mark.asyncio
	async def test_custom_max_entries(self):
		store = InMemoryVectorStore(max_entries=500)
		assert store._max_entries == 500

	@pytest.mark.asyncio
	async def test_eviction_on_overflow(self):
		store = InMemoryVectorStore(max_entries=3)
		for i in range(5):
			await store.add([f"text{i}"], [[float(i)]], [{"idx": i}])
		assert len(store._entries) == 3
		# Should keep the most recent entries
		texts = [e["text"] for e in store._entries]
		assert "text4" in texts
		assert "text3" in texts

	@pytest.mark.asyncio
	async def test_search_returns_top_k(self):
		store = InMemoryVectorStore(max_entries=100)
		await store.add(["a", "b", "c"], [[1.0, 0.0], [0.0, 1.0], [0.7, 0.7]], [{}, {}, {}])
		results = await store.search([1.0, 0.0], top_k=2)
		assert len(results) == 2
		assert results[0]["text"] == "a"  # most similar

	@pytest.mark.asyncio
	async def test_search_empty_store(self):
		store = InMemoryVectorStore()
		results = await store.search([1.0, 0.0], top_k=5)
		assert results == []

	@pytest.mark.asyncio
	async def test_delete_entries(self):
		store = InMemoryVectorStore()
		ids = await store.add(["a", "b"], [[1.0], [2.0]], [{}, {}])
		await store.delete([ids[0]])
		assert len(store._entries) == 1
		assert store._entries[0]["text"] == "b"

	@pytest.mark.asyncio
	async def test_add_returns_ids(self):
		store = InMemoryVectorStore()
		ids = await store.add(["text1", "text2"], [[1.0], [2.0]], [{"k": "v"}, {}])
		assert len(ids) == 2
		assert all(isinstance(i, str) for i in ids)
