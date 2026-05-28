import pytest

from axc_agent_engine.core.context import ExecutionContext, ExecutionServices
from axc_agent_engine.observability.audit import InMemoryAuditSink
from axc_agent_engine.plugins import PluginContext
from axc_agent_engine.plugins.builtin.memory.plugin import (
	MemoryPlugin,
	MemoryPrivacyPolicy,
	MemoryRepository,
	MemoryScopeResolver,
	_access_snapshot,
	_changed_access_ids,
	_is_negative_fact,
	_layer_counts,
	_normalize_fact_text,
	_public_memory,
	_resource_name,
)
from axc_agent_engine.plugins.builtin.memory.support.graph import DefaultEntityResolver, GraphMemory
from axc_agent_engine.plugins.builtin.memory.support.service import MemoryLayer, MemoryService


def test_graph_memory_entities_relations_and_search():
	graph = GraphMemory()
	entity = graph.upsert_entity(" Alice ", "person", aliases=["A"])
	same = graph.upsert_entity("A", "person")
	assert same.id == entity.id
	assert same.mention_count == 2
	with pytest.raises(ValueError):
		graph.upsert_entity(" ")
	relation = graph.upsert_relation("Alice", "Bob", "KNOWS", "short", "m1")
	relation2 = graph.upsert_relation("Alice", "Bob", "KNOWS", "longer description", "m1")
	assert relation2.id == relation.id
	assert relation2.description == "longer description"
	matches = graph.search("Alice Bob", limit=1)
	assert matches[0]["relation_type"] == "KNOWS"
	assert graph.search("missing") == []
	resolver = DefaultEntityResolver()
	assert resolver.resolve("Alice", "organization", [], graph.entities) is None


async def test_memory_repository_scope_privacy_and_helpers():
	class Store:
		def __init__(self):
			self.values = {}
		async def list_keys(self, prefix):
			return [key for key in self.values if key.startswith(prefix)]
		async def get(self, key):
			return self.values.get(key)
		async def set(self, key, value):
			self.values[key] = value
		async def delete(self, key):
			self.values.pop(key, None)

	scope = MemoryScopeResolver("ns", ["tenant_id"], include_session_scope=True)
	ctx = ExecutionContext()
	ctx.state.metadata.update({"tenant_id": "t", "session_id": "s"})
	assert scope.scope_id(ctx) == "ns|tenant_id=t|session_id=s"
	assert scope.scope_id(None) == "ns"
	assert scope.key_prefix("x") == "memory:x:"

	policy = MemoryPrivacyPolicy("redact", [__import__("re").compile(r"secret")])
	assert policy.sanitize("has secret")[0] == "has [REDACTED]"
	reject = MemoryPrivacyPolicy("reject", [__import__("re").compile(r"secret")])
	assert reject.sanitize("has secret")[1]["rejected"] is True

	store = Store()
	repo = MemoryRepository(store, scope)
	await repo.save_memory(scope.scope_id(ctx), {"id": "m1", "content": "x"})
	assert await repo.get_memory(scope.scope_id(ctx), "m1")
	assert await repo.load_scope(scope.scope_id(ctx))
	await repo.persist_memories(scope.scope_id(ctx), [{"id": "m2", "content": "y"}], ["m2", "missing"])
	await repo.delete_memories(scope.scope_id(ctx), ["m1"])
	assert await repo.get_memory(scope.scope_id(ctx), "m1") is None

	before = {"m": ("old", 0)}
	after = [{"id": "m", "last_accessed_at": "new", "access_count": 1}]
	assert _changed_access_ids(before, after) == ["m"]
	assert _access_snapshot(after)["m"] == ("new", 1)
	assert _public_memory({"id": "m", "extra": "hidden"}) == {
		"id": "m", "layer": "", "content": "", "fact_type": "", "importance": 0.0,
		"confidence": 0.0, "source": "", "created_at": "", "last_accessed_at": "",
		"access_count": 0, "metadata": {},
	}
	assert _layer_counts([{"layer": "semantic"}, {"layer": "semantic"}]) == {"semantic": 2}
	assert _is_negative_fact("I do not like this")
	assert _normalize_fact_text("I do not like this") == "i do like this"
	assert _resource_name(None, "d") == "d"
	assert _resource_name(False, "d") == ""
	assert _resource_name("x", "d") == "x"


async def test_memory_plugin_loading_capacity_audit_and_extraction_paths():
	class LLM:
		def __init__(self, response):
			self.response = response
		async def ask(self, prompt):
			if isinstance(self.response, Exception):
				raise self.response
			return self.response

	audit = InMemoryAuditSink()
	plugin = MemoryPlugin()
	plugin.initialize({"min_content_length": 3, "max_memories": 1, "auto_extract": True}, PluginContext(kv_store=None, utility_model=None))
	ctx = ExecutionContext(services=ExecutionServices(audit_sink=audit))
	ctx.state.metadata["agent_name"] = "agent"
	await plugin.on_execution_start(ctx)
	assert plugin._loaded_scopes
	await plugin.on_round_end(ctx, "long enough memory", "", [])
	await plugin._add_memory("second memory", 0.2, exec_ctx=ctx)
	assert len(plugin._memories) == 1
	assert (await audit.list_events())[-1].type == "memory_added"
	assert plugin._compute_score({"importance": 1, "created_at": "bad", "content": "second memory"}, "second") > 0
	assert plugin._valid_extracted_fact({"content": "x", "importance": "bad"}) is False
	assert plugin._valid_extracted_fact({"content": "", "importance": 0.5}) is False
	assert plugin._ttl_expired_ids() == []
	plugin._schedule_persist([plugin._memories[0]["id"]], ctx)
	await plugin._flush_background_tasks()

	with_llm = MemoryPlugin()
	with_llm.initialize({"min_content_length": 3}, PluginContext(kv_store=None, utility_model=LLM('[{"content":"json fact","importance":0.9,"type":"episodic"}]')))
	await with_llm.on_round_end(ExecutionContext(), "hello", "world", [])
	assert with_llm._memories[0]["layer"] == MemoryLayer.EPISODIC

	failing = MemoryPlugin()
	failing.initialize({"min_content_length": 3}, PluginContext(kv_store=None, utility_model=LLM(RuntimeError("down"))))
	fail_ctx = ExecutionContext()
	await failing.on_round_end(fail_ctx, "hello", "world", [])
	assert fail_ctx.state.metadata["memory"]["stats"]["extraction_failures"] == 1

	assert (await plugin._add_memory_tool_result("x", 0.1, "bad-layer", "fact", ctx)).is_error
