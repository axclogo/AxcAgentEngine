"""Tests for Memory plugin — extraction, dedup, decay, persistence."""
import pytest
from unittest.mock import AsyncMock

from axc_agent_engine.plugins.builtin.memory.plugin import MemoryPlugin, _char_similarity
from axc_agent_engine.plugins.builtin.memory.support.service import MemoryLayer
from axc_agent_engine.core.context import ExecutionContext
from axc_agent_engine.observability.audit import InMemoryAuditSink
from axc_agent_engine.storage.in_memory import InMemoryKVStore
from axc_agent_engine.plugins import PluginContext
from axc_agent_engine.runtime.resources import ResourceRegistry


class MockVectorStore:
	def __init__(self):
		self.added_texts: list[str] = []
		self.added_metadata: list[dict] = []
		self.search_results: list[dict] = []
		self.deleted_ids: list[str] = []

	async def add(self, texts: list[str], embeddings: list[list[float]], metadata: list[dict]) -> list[str]:
		self.added_texts.extend(texts)
		self.added_metadata.extend(metadata)
		start = len(self.added_texts) - len(texts)
		return [f"vec_{start + index}" for index in range(len(texts))]

	async def search(self, embedding: list[float], top_k: int = 5) -> list[dict]:
		return self.search_results[:top_k]

	async def delete(self, ids: list[str]) -> None:
		self.deleted_ids.extend(ids)


@pytest.fixture
def memory_plugin():
	llm = AsyncMock()
	llm.ask = AsyncMock(return_value="0.8|Important fact about the user")
	ctx = PluginContext(utility_model=llm, kv_store=InMemoryKVStore())
	p = MemoryPlugin()
	p.initialize({"min_content_length": 10}, ctx)
	return p


class TestMemoryPlugin:
	@pytest.mark.asyncio
	async def test_extract_facts(self, memory_plugin):
		ctx = ExecutionContext()
		await memory_plugin.on_execution_start(ctx)
		await memory_plugin.on_round_end(ctx, "I prefer Python over Java", "Noted!", [])
		assert len(memory_plugin._memories) > 0

	@pytest.mark.asyncio
	async def test_inject_context(self, memory_plugin):
		ctx = ExecutionContext()
		await memory_plugin.on_execution_start(ctx)
		await memory_plugin._add_memory("User prefers dark mode", 0.9)
		result = memory_plugin.inject_context(ctx, "preferences")
		assert "dark mode" in result

	@pytest.mark.asyncio
	async def test_inject_context_empty(self, memory_plugin):
		ctx = ExecutionContext()
		result = memory_plugin.inject_context(ctx)
		assert result == ""

	@pytest.mark.asyncio
	async def test_dedup(self, memory_plugin):
		ctx = ExecutionContext()
		await memory_plugin.on_execution_start(ctx)
		await memory_plugin._add_memory("User likes Python programming very much indeed", 0.8)
		# Identical content should be detected as duplicate
		assert memory_plugin._is_duplicate("User likes Python programming very much indeed") is True
		# Different content should not be duplicate
		assert memory_plugin._is_duplicate("Something completely different and unrelated") is False

	@pytest.mark.asyncio
	async def test_memory_add_tool(self, memory_plugin):
		ctx = ExecutionContext()
		await memory_plugin.on_execution_start(ctx)
		result = await memory_plugin._tool_memory_add({"content": "Remember this important fact"}, {})
		assert result.content["status"] == "ok"

	@pytest.mark.asyncio
	async def test_memory_add_fact_and_lesson_tools(self, memory_plugin):
		ctx = ExecutionContext()
		await memory_plugin.on_execution_start(ctx)
		fact = await memory_plugin._tool_memory_add_fact({"content": "User prefers Python examples", "fact_type": "preference"}, {})
		lesson = await memory_plugin._tool_memory_add_lesson({"content": "Always run tests after migrations"}, {})
		assert fact.content["layer"] == "semantic"
		assert fact.content["fact_type"] == "preference"
		assert lesson.content["layer"] == "lesson"
		assert any(item["layer"] == "lesson" for item in memory_plugin._memories)

	@pytest.mark.asyncio
	async def test_memory_add_empty(self, memory_plugin):
		result = await memory_plugin._tool_memory_add({"content": ""}, {})
		assert result.is_error

	@pytest.mark.asyncio
	async def test_kv_persistence(self, memory_plugin):
		ctx = ExecutionContext()
		await memory_plugin.on_execution_start(ctx)
		await memory_plugin._add_memory("Persisted fact for testing", 0.9)
		store = memory_plugin._store
		keys = await store.list_keys("memory:")
		assert len(keys) == 1

	@pytest.mark.asyncio
	async def test_decay_removes_old(self, memory_plugin):
		from datetime import datetime, timezone, timedelta
		ctx = ExecutionContext()
		await memory_plugin.on_execution_start(ctx)
		old_time = (datetime.now(timezone.utc) - timedelta(days=365)).isoformat()
		item = memory_plugin._service.add("ancient fact", layer=MemoryLayer.EPISODIC, importance=0.01)
		item.id = "old1"
		item.created_at = old_time
		memory_plugin._sync_memories_view()
		await memory_plugin.on_execution_end(ctx, "done", "")
		assert not any(m["id"] == "old1" for m in memory_plugin._memories)

	@pytest.mark.asyncio
	async def test_scope_isolation(self):
		store = InMemoryKVStore()
		p1 = MemoryPlugin()
		p1.initialize({"min_content_length": 3}, PluginContext(kv_store=store))
		ctx_a = ExecutionContext()
		ctx_a.state.metadata.update({"tenant_id": "t1", "user_id": "u1", "agent_name": "a"})
		await p1.on_execution_start(ctx_a)
		await p1._add_memory("Tenant one memory", 0.8, exec_ctx=ctx_a)

		p2 = MemoryPlugin()
		p2.initialize({"min_content_length": 3}, PluginContext(kv_store=store))
		ctx_b = ExecutionContext()
		ctx_b.state.metadata.update({"tenant_id": "t2", "user_id": "u1", "agent_name": "a"})
		await p2.on_execution_start(ctx_b)

		assert p2.inject_context(ctx_b, "Tenant") == ""

	@pytest.mark.asyncio
	async def test_scope_switch_on_same_plugin_does_not_leak(self):
		store = InMemoryKVStore()
		p = MemoryPlugin()
		p.initialize({"min_content_length": 3}, PluginContext(kv_store=store))
		ctx_a = ExecutionContext()
		ctx_a.state.metadata.update({"tenant_id": "t1", "user_id": "u1", "agent_name": "a"})
		ctx_b = ExecutionContext()
		ctx_b.state.metadata.update({"tenant_id": "t2", "user_id": "u1", "agent_name": "a"})
		await p.on_execution_start(ctx_a)
		await p._add_memory("Tenant one memory", 0.8, exec_ctx=ctx_a)
		await p.on_execution_start(ctx_b)
		assert p.inject_context(ctx_b, "Tenant") == ""
		await p.on_execution_start(ctx_a)
		assert "Tenant one memory" in p.inject_context(ctx_a, "Tenant")

	@pytest.mark.asyncio
	async def test_sensitive_content_redacted(self):
		p = MemoryPlugin()
		p.initialize({"min_content_length": 3}, PluginContext(kv_store=InMemoryKVStore()))
		ctx = ExecutionContext()
		await p.on_execution_start(ctx)
		result = await p._tool_memory_add({"content": "Email alice@example.com should be private"}, {"exec_ctx": ctx})
		assert result.content["redacted"] is True
		assert "[REDACTED]" in p._memories[0]["content"]
		assert ctx.state.metadata["memory"]["stats"]["redacted"] == 1

	@pytest.mark.asyncio
	async def test_sensitive_content_rejected(self):
		p = MemoryPlugin()
		p.initialize({"min_content_length": 3, "sensitive_policy": "reject"}, PluginContext(kv_store=InMemoryKVStore()))
		ctx = ExecutionContext()
		await p.on_execution_start(ctx)
		result = await p._tool_memory_add({"content": "Call 13812345678 later"}, {"exec_ctx": ctx})
		assert result.is_error
		assert p._memories == []

	@pytest.mark.asyncio
	async def test_memory_governance_tools(self, memory_plugin):
		ctx = ExecutionContext()
		await memory_plugin.on_execution_start(ctx)
		await memory_plugin._add_memory("User prefers compact examples", 0.9, exec_ctx=ctx)
		search = await memory_plugin._tool_memory_search({"query": "compact", "top_k": 3}, {"exec_ctx": ctx})
		listed = await memory_plugin._tool_memory_list({"limit": 10}, {"exec_ctx": ctx})
		exported = await memory_plugin._tool_memory_export({}, {"exec_ctx": ctx})
		mem_id = listed.content["memories"][0]["id"]
		deleted = await memory_plugin._tool_memory_delete({"id": mem_id}, {"exec_ctx": ctx})
		assert search.content["memories"]
		assert listed.content["count"] == 1
		assert exported.content["count"] == 1
		assert deleted.content["deleted"] is True

	def test_memory_tool_risk_metadata(self, memory_plugin):
		tools = {tool.name: tool for tool in memory_plugin.get_tools()}
		assert tools["memory_add"].capability == "memory_write"
		assert tools["memory_search"].capability == "memory_read"
		assert tools["memory_delete"].risk_level == "dangerous"

	@pytest.mark.asyncio
	async def test_conflict_marker(self):
		p = MemoryPlugin()
		p.initialize({"min_content_length": 3}, PluginContext(kv_store=InMemoryKVStore()))
		ctx = ExecutionContext()
		await p.on_execution_start(ctx)
		await p._tool_memory_add_fact({"content": "User likes Python", "fact_type": "preference"}, {"exec_ctx": ctx})
		result = await p._tool_memory_add_fact({"content": "User does not like Python", "fact_type": "preference"}, {"exec_ctx": ctx})
		assert result.content["conflict"] is True
		assert p._memories[-1]["metadata"]["conflict_with"]

	@pytest.mark.asyncio
	async def test_ttl_removes_old_semantic_memory(self):
		from datetime import datetime, timezone, timedelta
		p = MemoryPlugin()
		p.initialize({"ttl_days": 1, "min_content_length": 3}, PluginContext(kv_store=InMemoryKVStore()))
		ctx = ExecutionContext()
		await p.on_execution_start(ctx)
		await p._add_memory("Old semantic memory", 0.9, exec_ctx=ctx)
		p._service.items[0].created_at = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
		p._sync_memories_view()
		await p.on_execution_end(ctx, "done", "")
		assert p._memories == []

	@pytest.mark.asyncio
	async def test_capacity_eviction_deletes_kv(self):
		store = InMemoryKVStore()
		p = MemoryPlugin()
		p.initialize({"max_memories": 1, "min_content_length": 3}, PluginContext(kv_store=store))
		ctx = ExecutionContext()
		await p.on_execution_start(ctx)
		await p._add_memory("low value memory", 0.1, exec_ctx=ctx)
		await p._add_memory("high value memory", 0.9, exec_ctx=ctx)
		keys = await store.list_keys("memory:")
		assert len(keys) == 1
		assert p._memories[0]["content"] == "high value memory"

	@pytest.mark.asyncio
	async def test_capacity_eviction_does_not_persist_new_low_value_memory(self):
		store = InMemoryKVStore()
		p = MemoryPlugin()
		p.initialize({"max_memories": 1, "min_content_length": 3}, PluginContext(kv_store=store))
		ctx = ExecutionContext()
		await p.on_execution_start(ctx)
		await p._add_memory("high value memory", 0.9, exec_ctx=ctx)
		await p._add_memory("low value memory", 0.1, exec_ctx=ctx)
		keys = await store.list_keys("memory:")
		values = [await store.get(key) for key in keys]
		assert len(keys) == 1
		assert values[0]["content"] == "high value memory"
		assert all(item["content"] != "low value memory" for item in p._memories)

	@pytest.mark.asyncio
	async def test_search_persists_access_metadata(self):
		store = InMemoryKVStore()
		p = MemoryPlugin()
		p.initialize({"min_content_length": 3}, PluginContext(kv_store=store))
		ctx = ExecutionContext()
		await p.on_execution_start(ctx)
		await p._add_memory("User likes durable access metadata", 0.9, exec_ctx=ctx)
		mem_id = p._memories[0]["id"]
		result = await p._tool_memory_search({"query": "durable access", "top_k": 1}, {"exec_ctx": ctx})
		assert result.content["memories"]
		record = await store.get(f"memory:{p._scope_resolver.scope_id(ctx)}:{mem_id}")
		assert record["access_count"] > 0
		assert record["last_accessed_at"]

	@pytest.mark.asyncio
	async def test_inject_context_schedules_access_metadata_persist(self):
		store = InMemoryKVStore()
		p = MemoryPlugin()
		p.initialize({"min_content_length": 3}, PluginContext(kv_store=store))
		ctx = ExecutionContext()
		await p.on_execution_start(ctx)
		await p._add_memory("User prefers durable context injection", 0.9, exec_ctx=ctx)
		mem_id = p._memories[0]["id"]
		assert "durable context" in p.inject_context(ctx, "durable context")
		await p.on_execution_end(ctx, "done", "")
		record = await store.get(f"memory:{p._scope_resolver.scope_id(ctx)}:{mem_id}")
		assert record["access_count"] > 0

	@pytest.mark.asyncio
	async def test_vector_store_upsert_search_and_delete(self):
		store = InMemoryKVStore()
		vector_store = MockVectorStore()
		resources = ResourceRegistry({"memory_vector": vector_store})
		p = MemoryPlugin()
		p.initialize({"min_content_length": 3, "vector_store": "memory_vector"}, PluginContext(kv_store=store, resources=resources))
		ctx = ExecutionContext()
		ctx.state.metadata.update({"tenant_id": "t1"})
		await p.on_execution_start(ctx)
		await p._add_memory("Vector backed enterprise memory", 0.9, exec_ctx=ctx)
		mem = p._memories[0]
		vector_id = mem["metadata"]["vector_id"]
		vector_store.search_results = [{
			"id": vector_id,
			"text": mem["content"],
			"score": 0.99,
			"metadata": vector_store.added_metadata[0],
		}]
		search = await p._tool_memory_search({"query": "enterprise memory", "top_k": 1}, {"exec_ctx": ctx})
		assert vector_store.added_texts == ["Vector backed enterprise memory"]
		assert search.content["memories"][0]["id"] == mem["id"]
		deleted = await p._tool_memory_delete({"id": mem["id"]}, {"exec_ctx": ctx})
		assert deleted.content["deleted"] is True
		assert vector_id in vector_store.deleted_ids

	@pytest.mark.asyncio
	async def test_memory_write_and_delete_audit_events(self):
		audit_sink = InMemoryAuditSink()
		p = MemoryPlugin()
		p.initialize({"min_content_length": 3}, PluginContext(kv_store=InMemoryKVStore()))
		ctx = ExecutionContext()
		ctx.services.audit_sink = audit_sink
		ctx.state.metadata.update({"user_id": "u1", "session_id": "s1"})
		await p.on_execution_start(ctx)
		await p._add_memory("Audited memory operation", 0.8, exec_ctx=ctx)
		mem_id = p._memories[0]["id"]
		await p._tool_memory_delete({"id": mem_id}, {"exec_ctx": ctx})
		events = await audit_sink.list_events()
		assert [event.type for event in events] == ["memory_added", "memory_deleted"]
		assert events[0].metadata["scope"] == p._scope_resolver.scope_id(ctx)


class TestBigramSimilarity:
	def test_identical(self):
		assert _char_similarity("hello world", "hello world") == pytest.approx(1.0)

	def test_completely_different(self):
		sim = _char_similarity("abcdef", "xyz123")
		assert sim < 0.3

	def test_similar(self):
		sim = _char_similarity("hello world", "hello earth")
		assert 0.3 < sim < 0.8

	def test_empty(self):
		assert _char_similarity("", "hello") == 0.0
		assert _char_similarity("hello", "") == 0.0

	def test_short_strings(self):
		# Single char falls back to char-level
		sim = _char_similarity("a", "a")
		assert sim == 1.0

	def test_reordered_not_identical(self):
		# Bigram should differentiate reordered text
		sim = _char_similarity("abcdef", "fedcba")
		assert sim < 1.0
