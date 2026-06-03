"""Tests for storage module — InMemory implementations with TTL and capacity."""
import asyncio
import time
import pytest

from axc_agent_engine.storage.in_memory import (
	InMemoryKVStore, InMemoryMessagePersistence, InMemorySpanStore,
	InMemoryVectorStore, InMemoryMessageBus,
)
from axc_agent_engine.utils.math_utils import cosine_similarity as _cosine_similarity


class TestInMemoryKVStore:
	@pytest.mark.asyncio
	async def test_set_and_get(self):
		store = InMemoryKVStore()
		await store.set("key1", {"value": "hello"})
		result = await store.get("key1")
		assert result == {"value": "hello"}

	@pytest.mark.asyncio
	async def test_get_nonexistent(self):
		store = InMemoryKVStore()
		assert await store.get("nope") is None

	@pytest.mark.asyncio
	async def test_delete(self):
		store = InMemoryKVStore()
		await store.set("key1", {"v": 1})
		await store.delete("key1")
		assert await store.get("key1") is None

	@pytest.mark.asyncio
	async def test_list_keys(self):
		store = InMemoryKVStore()
		await store.set("prefix:a", {"v": 1})
		await store.set("prefix:b", {"v": 2})
		await store.set("other:c", {"v": 3})
		keys = await store.list_keys("prefix:")
		assert len(keys) == 2
		assert "prefix:a" in keys

	@pytest.mark.asyncio
	async def test_max_size_eviction(self):
		store = InMemoryKVStore(max_size=3)
		for i in range(5):
			await store.set(f"key{i}", {"v": i})
		# Only last 3 should remain
		assert await store.get("key0") is None
		assert await store.get("key1") is None
		assert await store.get("key2") is not None
		assert await store.get("key4") is not None

	@pytest.mark.asyncio
	async def test_ttl_expiry(self):
		store = InMemoryKVStore(ttl=1)
		await store.set("key1", {"v": 1})
		# Immediately should be available
		assert await store.get("key1") is not None
		# Simulate time passing by manipulating internal state
		store._data["key1"] = ({"v": 1}, time.time() - 2)
		assert await store.get("key1") is None

	@pytest.mark.asyncio
	async def test_lru_ordering(self):
		store = InMemoryKVStore(max_size=3)
		await store.set("a", {"v": 1})
		await store.set("b", {"v": 2})
		await store.set("c", {"v": 3})
		# Access 'a' to make it recent
		await store.get("a")
		# Add new item, should evict 'b' (least recently used)
		await store.set("d", {"v": 4})
		assert await store.get("a") is not None
		assert await store.get("b") is None


class TestInMemoryMessagePersistence:
	@pytest.mark.asyncio
	async def test_save_and_load(self):
		store = InMemoryMessagePersistence()
		msgs = [{"role": "user", "content": "hi"}]
		await store.save("s1", msgs)
		loaded = await store.load("s1")
		assert loaded == msgs

	@pytest.mark.asyncio
	async def test_load_nonexistent(self):
		store = InMemoryMessagePersistence()
		assert await store.load("nope") == []

	@pytest.mark.asyncio
	async def test_delete(self):
		store = InMemoryMessagePersistence()
		await store.save("s1", [{"role": "user", "content": "hi"}])
		await store.delete("s1")
		assert await store.load("s1") == []

	@pytest.mark.asyncio
	async def test_max_sessions(self):
		store = InMemoryMessagePersistence(max_sessions=2)
		await store.save("s1", [])
		await store.save("s2", [])
		await store.save("s3", [])
		assert await store.load("s1") == []  # evicted


class TestInMemorySpanStore:
	@pytest.mark.asyncio
	async def test_save_and_query(self):
		store = InMemorySpanStore()
		await store.save_span({"trace_id": "t1", "name": "test"})
		results = await store.query_by_trace("t1")
		assert len(results) == 1

	@pytest.mark.asyncio
	async def test_max_spans(self):
		store = InMemorySpanStore(max_spans=5)
		for i in range(10):
			await store.save_span({"trace_id": f"t{i}"})
		results = await store.query_by_trace("t0")
		assert len(results) == 0  # evicted
		results = await store.query_by_trace("t9")
		assert len(results) == 1

	@pytest.mark.asyncio
	async def test_query_by_session_returns_latest_limited_spans(self):
		store = InMemorySpanStore()
		for i in range(5):
			await store.save_span({"trace_id": f"t{i}", "session_id": "s1", "idx": i})
		await store.save_span({"trace_id": "other", "session_id": "s2", "idx": 99})

		results = await store.query_by_session("s1", limit=2)

		assert [span["idx"] for span in results] == [3, 4]


class TestInMemoryVectorStore:
	@pytest.mark.asyncio
	async def test_add_and_search(self):
		store = InMemoryVectorStore()
		await store.add(["hello"], [[1.0, 0.0, 0.0]], [{"source": "test"}])
		results = await store.search([1.0, 0.0, 0.0], top_k=1)
		assert len(results) == 1
		assert results[0]["text"] == "hello"
		assert results[0]["score"] > 0.99

	@pytest.mark.asyncio
	async def test_search_ranking(self):
		store = InMemoryVectorStore()
		await store.add(
			["a", "b"],
			[[1.0, 0.0], [0.0, 1.0]],
			[{}, {}],
		)
		results = await store.search([1.0, 0.0], top_k=2)
		assert results[0]["text"] == "a"

	@pytest.mark.asyncio
	async def test_delete(self):
		store = InMemoryVectorStore()
		ids = await store.add(["x"], [[1.0]], [{}])
		await store.delete(ids)
		results = await store.search([1.0], top_k=1)
		assert len(results) == 0

	@pytest.mark.asyncio
	async def test_max_entries(self):
		store = InMemoryVectorStore(max_entries=3)
		for i in range(5):
			await store.add([f"text{i}"], [[float(i)]], [{}])
		results = await store.search([4.0], top_k=10)
		assert len(results) <= 3

	@pytest.mark.asyncio
	async def test_add_ignores_unpaired_text_embedding_metadata_rows(self):
		store = InMemoryVectorStore()

		ids = await store.add(["kept", "dropped"], [[1.0]], [{"source": "only-one-meta"}, {"ignored": True}])
		results = await store.search([1.0], top_k=10)

		assert len(ids) == 1
		assert [item["text"] for item in results] == ["kept"]

	@pytest.mark.asyncio
	async def test_search_filters_zero_or_negative_similarity(self):
		store = InMemoryVectorStore()
		await store.add(["orthogonal", "negative"], [[0.0, 1.0], [-1.0, 0.0]], [{}, {}])

		assert await store.search([1.0, 0.0], top_k=5) == []


class TestCosineSimlarity:
	def test_identical(self):
		assert _cosine_similarity([1, 0, 0], [1, 0, 0]) == pytest.approx(1.0)

	def test_orthogonal(self):
		assert _cosine_similarity([1, 0], [0, 1]) == pytest.approx(0.0)

	def test_empty(self):
		assert _cosine_similarity([], []) == 0.0

	def test_different_lengths(self):
		assert _cosine_similarity([1, 0], [1, 0, 0]) == 0.0

	def test_zero_vector(self):
		assert _cosine_similarity([0, 0], [1, 0]) == 0.0


class TestInMemoryMessageBus:
	@pytest.mark.asyncio
	async def test_publish_subscribe(self):
		bus = InMemoryMessageBus()
		received = []

		async def subscriber():
			async for msg in bus.subscribe("test"):
				received.append(msg)
				break

		task = asyncio.create_task(subscriber())
		await asyncio.sleep(0.01)
		await bus.publish("test", {"data": "hello"})
		await asyncio.wait_for(task, timeout=1.0)
		assert received == [{"data": "hello"}]

	@pytest.mark.asyncio
	async def test_publish_no_subscribers(self):
		bus = InMemoryMessageBus()
		# Should not raise
		await bus.publish("empty", {"data": "hello"})

	@pytest.mark.asyncio
	async def test_request_round_trip_and_reply_channel_cleanup(self):
		bus = InMemoryMessageBus()
		seen = []

		async def responder():
			async for msg in bus.subscribe("rpc"):
				seen.append(dict(msg))
				await bus.publish(msg["_reply_to"], {"ok": True, "echo": msg["value"]})
				break

		task = asyncio.create_task(responder())
		while "rpc" not in bus._channels:
			await asyncio.sleep(0)
		response = await bus.request("rpc", {"value": 42}, timeout=1)
		await asyncio.wait_for(task, timeout=1)

		assert response == {"ok": True, "echo": 42}
		assert seen[0]["_reply_to"].startswith("_reply_")
		assert seen[0]["_reply_to"] not in bus._channels

	@pytest.mark.asyncio
	async def test_request_timeout_cleans_reply_channel(self):
		bus = InMemoryMessageBus()

		with pytest.raises(asyncio.TimeoutError):
			await bus.request("missing", {"value": 42}, timeout=0.01)

		assert bus._channels == {}

	@pytest.mark.asyncio
	async def test_subscribe_removes_queue_on_close(self):
		bus = InMemoryMessageBus(max_idle_rounds=1)

		async def subscriber():
			async for _ in bus.subscribe("close-me"):
				pass

		task = asyncio.create_task(subscriber())
		while "close-me" not in bus._channels:
			await asyncio.sleep(0)
		task.cancel()
		with pytest.raises(asyncio.CancelledError):
			await task

		assert bus._channels["close-me"] == []
