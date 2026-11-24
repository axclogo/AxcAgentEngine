"""Tests for Memory plugin — extraction, dedup, decay, persistence."""

from datetime import UTC
from unittest.mock import AsyncMock

import pytest

from axc_agent_engine.core.context import ExecutionContext
from axc_agent_engine.observability.audit import InMemoryAuditSink
from axc_agent_engine.plugins import PluginContext
from axc_agent_engine.plugins.builtin.memory.plugin import MemoryPlugin
from axc_agent_engine.plugins.builtin.memory.support.service import MemoryLayer, char_similarity
from axc_agent_engine.storage.in_memory import InMemoryKVStore


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
		assert len(memory_plugin._scope(ctx)._service.dump()) > 0

	@pytest.mark.asyncio
	async def test_inject_context(self, memory_plugin):
		ctx = ExecutionContext()
		await memory_plugin.on_execution_start(ctx)
		await memory_plugin._scope(ctx)._add_memory("User prefers dark mode", 0.9)
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
		await memory_plugin._scope(ctx)._add_memory("User likes Python programming very much indeed", 0.8)
		# Identical content should be detected as duplicate
		assert memory_plugin._scope(ctx)._is_duplicate("User likes Python programming very much indeed") is True
		# Different content should not be duplicate
		assert memory_plugin._scope(ctx)._is_duplicate("Something completely different and unrelated") is False

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
		fact = await memory_plugin._tool_memory_add_fact(
			{"content": "User prefers Python examples", "fact_type": "preference"}, {}
		)
		lesson = await memory_plugin._tool_memory_add_lesson({"content": "Always run tests after migrations"}, {})
		assert fact.content["layer"] == "semantic"
		assert fact.content["fact_type"] == "preference"
		assert lesson.content["layer"] == "lesson"
		assert any(item["layer"] == "lesson" for item in memory_plugin._scope(ctx)._service.dump())

	@pytest.mark.asyncio
	async def test_memory_add_empty(self, memory_plugin):
		result = await memory_plugin._tool_memory_add({"content": ""}, {})
		assert result.is_error

	@pytest.mark.asyncio
	async def test_kv_persistence(self):
		store = InMemoryKVStore()
		memory_plugin = MemoryPlugin()
		memory_plugin.initialize({}, PluginContext(kv_store=store))
		ctx = ExecutionContext()
		await memory_plugin.on_execution_start(ctx)
		await memory_plugin._scope(ctx)._add_memory("Persisted fact for testing", 0.9)
		keys = await store.list_keys("memory:")
		assert len(keys) == 1

	@pytest.mark.asyncio
	async def test_decay_removes_old(self, memory_plugin):
		from datetime import datetime, timedelta

		ctx = ExecutionContext()
		await memory_plugin.on_execution_start(ctx)
		old_time = (datetime.now(UTC) - timedelta(days=365)).isoformat()
		item = memory_plugin._scope(ctx)._service.add("ancient fact", layer=MemoryLayer.EPISODIC, importance=0.01)
		item.id = "old1"
		item.created_at = old_time

		await memory_plugin.on_execution_end(ctx, "done", "")
		assert not any(m["id"] == "old1" for m in memory_plugin._scope(ctx)._service.dump())

	@pytest.mark.asyncio
	async def test_scope_isolation(self):
		store = InMemoryKVStore()
		p1 = MemoryPlugin()
		p1.initialize({"min_content_length": 3}, PluginContext(kv_store=store))
		ctx_a = ExecutionContext()
		ctx_a.state.metadata.update({"tenant_id": "t1", "user_id": "u1", "agent_name": "a"})
		await p1.on_execution_start(ctx_a)
		await p1._scope(ctx_a)._add_memory("Tenant one memory", 0.8, exec_ctx=ctx_a)

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
		await p._scope(ctx_a)._add_memory("Tenant one memory", 0.8, exec_ctx=ctx_a)
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
		assert "[REDACTED]" in p._scope(ctx)._service.dump()[0]["content"]
		assert ctx.state.metadata["memory"]["stats"]["redacted"] == 1

	@pytest.mark.asyncio
	async def test_sensitive_content_rejected(self):
		p = MemoryPlugin()
		p.initialize({"min_content_length": 3, "sensitive_policy": "reject"}, PluginContext(kv_store=InMemoryKVStore()))
		ctx = ExecutionContext()
		await p.on_execution_start(ctx)
		result = await p._tool_memory_add({"content": "Call 13812345678 later"}, {"exec_ctx": ctx})
		assert result.is_error
		assert p._scope(ctx)._service.dump() == []

	@pytest.mark.asyncio
	async def test_memory_governance_tools(self, memory_plugin):
		ctx = ExecutionContext()
		await memory_plugin.on_execution_start(ctx)
		await memory_plugin._scope(ctx)._add_memory("User prefers compact examples", 0.9, exec_ctx=ctx)
		search = await memory_plugin._tool_memory_search({"query": "compact", "top_k": 3}, {"exec_ctx": ctx})
		listed = await memory_plugin._tool_memory_list({"limit": 10}, {"exec_ctx": ctx})
		exported = await memory_plugin._tool_memory_export({}, {"exec_ctx": ctx})
		mem_id = listed.content["memories"][0]["id"]
		deleted = await memory_plugin._tool_memory_delete({"id": mem_id}, {"exec_ctx": ctx})
		assert search.content["memories"]
		assert "Memory search" in search.context_view()
		assert "User prefers compact examples" in search.context_view()
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
		result = await p._tool_memory_add_fact(
			{"content": "User does not like Python", "fact_type": "preference"}, {"exec_ctx": ctx}
		)
		assert result.content["conflict"] is True
		assert p._scope(ctx)._service.dump()[-1]["metadata"]["conflict_with"]

	@pytest.mark.asyncio
	async def test_ttl_removes_old_semantic_memory(self):
		from datetime import datetime, timedelta

		p = MemoryPlugin()
		p.initialize({"ttl_days": 1, "min_content_length": 3}, PluginContext(kv_store=InMemoryKVStore()))
		ctx = ExecutionContext()
		await p.on_execution_start(ctx)
		await p._scope(ctx)._add_memory("Old semantic memory", 0.9, exec_ctx=ctx)
		p._scope(ctx)._service.items[0].created_at = (datetime.now(UTC) - timedelta(days=3)).isoformat()

		await p.on_execution_end(ctx, "done", "")
		assert p._scope(ctx)._service.dump() == []

	@pytest.mark.asyncio
	async def test_capacity_eviction_deletes_kv(self):
		store = InMemoryKVStore()
		p = MemoryPlugin()
		p.initialize({"max_memories": 1, "min_content_length": 3}, PluginContext(kv_store=store))
		ctx = ExecutionContext()
		await p.on_execution_start(ctx)
		await p._scope(ctx)._add_memory("low value memory", 0.1, exec_ctx=ctx)
		await p._scope(ctx)._add_memory("high value memory", 0.9, exec_ctx=ctx)
		keys = await store.list_keys("memory:")
		assert len(keys) == 1
		assert p._scope(ctx)._service.dump()[0]["content"] == "high value memory"

	@pytest.mark.asyncio
	async def test_capacity_eviction_does_not_persist_new_low_value_memory(self):
		store = InMemoryKVStore()
		p = MemoryPlugin()
		p.initialize({"max_memories": 1, "min_content_length": 3}, PluginContext(kv_store=store))
		ctx = ExecutionContext()
		await p.on_execution_start(ctx)
		await p._scope(ctx)._add_memory("high value memory", 0.9, exec_ctx=ctx)
		await p._scope(ctx)._add_memory("low value memory", 0.1, exec_ctx=ctx)
		keys = await store.list_keys("memory:")
		values = [await store.get(key) for key in keys]
		assert len(keys) == 1
		assert values[0]["content"] == "high value memory"
		assert all(item["content"] != "low value memory" for item in p._scope(ctx)._service.dump())

	@pytest.mark.asyncio
	async def test_search_persists_access_metadata(self):
		store = InMemoryKVStore()
		p = MemoryPlugin()
		p.initialize({"min_content_length": 3}, PluginContext(kv_store=store))
		ctx = ExecutionContext()
		await p.on_execution_start(ctx)
		await p._scope(ctx)._add_memory("User likes durable access metadata", 0.9, exec_ctx=ctx)
		mem_id = p._scope(ctx)._service.dump()[0]["id"]
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
		await p._scope(ctx)._add_memory("User prefers durable context injection", 0.9, exec_ctx=ctx)
		mem_id = p._scope(ctx)._service.dump()[0]["id"]
		assert "durable context" in p.inject_context(ctx, "durable context")
		await p.on_execution_end(ctx, "done", "")
		record = await store.get(f"memory:{p._scope_resolver.scope_id(ctx)}:{mem_id}")
		assert record["access_count"] > 0

	@pytest.mark.asyncio
	async def test_memory_search_and_delete_use_kv_lexical_store(self):
		store = InMemoryKVStore()
		p = MemoryPlugin()
		p.initialize({"min_content_length": 3}, PluginContext(kv_store=store))
		ctx = ExecutionContext()
		ctx.state.metadata.update({"tenant_id": "t1"})
		await p.on_execution_start(ctx)
		await p._scope(ctx)._add_memory("Enterprise memory for lexical search", 0.9, exec_ctx=ctx)
		mem = p._scope(ctx)._service.dump()[0]
		search = await p._tool_memory_search({"query": "enterprise memory", "top_k": 1}, {"exec_ctx": ctx})
		assert search.content["memories"][0]["id"] == mem["id"]
		deleted = await p._tool_memory_delete({"id": mem["id"]}, {"exec_ctx": ctx})
		assert deleted.content["deleted"] is True
		assert await store.get(f"memory:{p._scope_resolver.scope_id(ctx)}:{mem['id']}") is None

	@pytest.mark.asyncio
	async def test_memory_write_and_delete_audit_events(self):
		audit_sink = InMemoryAuditSink()
		p = MemoryPlugin()
		p.initialize({"min_content_length": 3}, PluginContext(kv_store=InMemoryKVStore()))
		ctx = ExecutionContext()
		ctx.services.audit_sink = audit_sink
		ctx.state.metadata.update({"user_id": "u1", "session_id": "s1"})
		await p.on_execution_start(ctx)
		await p._scope(ctx)._add_memory("Audited memory operation", 0.8, exec_ctx=ctx)
		mem_id = p._scope(ctx)._service.dump()[0]["id"]
		await p._tool_memory_delete({"id": mem_id}, {"exec_ctx": ctx})
		events = await audit_sink.list_events()
		assert [event.type for event in events] == ["memory_added", "memory_deleted"]
		assert events[0].metadata["scope"] == p._scope_resolver.scope_id(ctx)


class TestBigramSimilarity:
	def test_identical(self):
		assert char_similarity("hello world", "hello world") == pytest.approx(1.0)

	def test_completely_different(self):
		sim = char_similarity("abcdef", "xyz123")
		assert sim < 0.3

	def test_similar(self):
		sim = char_similarity("hello world", "hello earth")
		assert 0.3 < sim < 0.8

	def test_empty(self):
		assert char_similarity("", "hello") == 0.0
		assert char_similarity("hello", "") == 0.0

	def test_short_strings(self):
		# Single char falls back to char-level
		sim = char_similarity("a", "a")
		assert sim == 1.0

	def test_reordered_not_identical(self):
		# Bigram should differentiate reordered text
		sim = char_similarity("abcdef", "fedcba")
		assert sim < 1.0


@pytest.mark.parametrize("system", ["", "You are a test agent"])
async def test_cold_and_concurrent_scopes_reach_only_their_own_model_requests(tmp_path, system):
	import asyncio

	import yaml

	from axc_agent_engine import AgentModels, Engine, LLMStreamChunk, PluginRegistry
	from axc_agent_engine.plugins.builtin import ReActPlugin

	store = InMemoryKVStore()
	seed = MemoryPlugin()
	seed.initialize({}, PluginContext(kv_store=store))
	for tenant in ["team", "team:other"]:
		ctx = ExecutionContext()
		ctx.state.metadata.update(tenant_id=tenant, agent_name="host")
		await seed._tool_memory_add_lesson({"content": f"private {tenant} lesson"}, {"exec_ctx": ctx})

	release = asyncio.Event()
	entered = asyncio.Event()
	requests = {}

	class Model:
		model = "offline"
		tool_name_mapping = None

		async def stream(self, messages, tools=None, **kwargs):
			text = messages[-1]["content"]
			requests[text] = messages
			if text == "first":
				entered.set()
				await release.wait()
			yield LLMStreamChunk(content_delta="ok")

		async def close(self):
			pass

	registry = PluginRegistry()
	registry.register_many([ReActPlugin, MemoryPlugin])
	path = tmp_path / "host.yaml"
	path.write_text(
		yaml.safe_dump(
			{
				"name": "host",
				"system_prompt": system,
				"plugins": {"react": {"enabled": True}, "memory": {"enabled": True, "auto_extract": False}},
			}
		)
	)
	engine = Engine(kv_store=store, plugin_registry=registry)
	try:
		agent = engine.load_agent_template(str(path)).instantiate(models=AgentModels(default=Model()))

		async def turn(message, tenant):
			return [e async for e in agent.stream(message, metadata={"tenant_id": tenant})]

		async with asyncio.timeout(4):
			first = asyncio.create_task(turn("first", "team"))
			await entered.wait()
			await turn("second", "team:other")
			release.set()
			await first
			await turn("third", "fresh")
		assert "private team lesson" in str(requests["first"]) and "private team:other lesson" not in str(
			requests["first"]
		)
		assert "private team:other lesson" in str(requests["second"]) and "private team lesson" not in str(
			requests["second"]
		)
		assert "private" not in str(requests["third"])
	finally:
		release.set()
		await engine.close()


async def test_scope_separators_and_legacy_prefixes():
	p = MemoryPlugin()
	kv = InMemoryKVStore()
	p.initialize({"namespace": "n", "scope_keys": ["tenant_id", "user_id"]}, PluginContext(kv_store=kv))
	ctx = ExecutionContext()
	ctx.state.metadata.update(tenant_id="a|user_id=b")
	await p._tool_memory_add_lesson({"content": "one owner"}, {"exec_ctx": ctx})
	other = ExecutionContext()
	other.state.metadata.update(tenant_id="a", user_id="b")
	assert (await p._tool_memory_list({}, {"exec_ctx": other})).content["count"] == 0
	await kv.set("memory:n|tenant_id=a:extra:id", {"id": "id", "content": "legacy from a different tenant"})
	await kv.set(
		"memory:n|tenant_id=a:id", {"id": "id", "content": "legacy from a", "metadata": {"scope": "n|tenant_id=a"}}
	)
	normal = ExecutionContext()
	normal.state.metadata.update(tenant_id="a")
	rows = (await p._tool_memory_list({}, {"exec_ctx": normal})).content["memories"]
	assert [row["content"] for row in rows] == ["legacy from a"]


@pytest.mark.parametrize("mode", ["parallel_por", "resume", "legacy_resume"])
async def test_memory_tools_keep_owner_through_parallel_steps_and_restart(tmp_path, mode, monkeypatch):
	import json
	from dataclasses import replace

	import yaml

	from axc_agent_engine import (
		AgentModels,
		Engine,
		EventType,
		InMemoryCheckpointStore,
		LLMMessage,
		LLMResponse,
		LLMStreamChunk,
		PluginRegistry,
	)
	from axc_agent_engine.plugins.builtin import PORPlugin, ReActPlugin

	kv, checkpoints = InMemoryKVStore(), InMemoryCheckpointStore()
	config = {"scope_keys": ["region", "tenant_id"], "auto_extract": False}
	seed = MemoryPlugin()
	seed.initialize(config, PluginContext(kv_store=kv))
	records = {}
	for tenant in ["red", "blue"]:
		ctx = ExecutionContext()
		ctx.state.metadata.update(region="east", tenant_id=tenant)
		records[tenant] = ctx
		await seed._tool_memory_add_lesson({"content": f"{tenant} remembered"}, {"exec_ctx": ctx})

	results = []

	class Model:
		model = "offline"
		tool_name_mapping = None

		def __init__(self, interrupt=False):
			self.interrupt = interrupt

		async def chat(self, messages, tools=None, **kwargs):
			steps = [
				{"step_id": i, "description": text, "depends_on": []} for i, text in enumerate(["reader", "writer"], 1)
			]
			return LLMResponse(message=LLMMessage(content=json.dumps({"goal": "review", "steps": steps})))

		async def stream(self, messages, tools=None, **options):
			if self.interrupt:
				yield LLMStreamChunk(content_delta="tick")
				return
			outputs = [m["content"] for m in messages if m.get("role") == "tool"]
			if outputs:
				results.extend(outputs)
				yield LLMStreamChunk(content_delta="done")
				return
			prompt = messages[-1]["content"]
			steps = [("memory_list", {}), ("memory_add_lesson", {"content": "written by red"})]
			if mode == "parallel_por":
				steps = steps[:1] if "1: reader" in prompt else steps[1:] if "2: writer" in prompt else []
			if not steps:
				yield LLMStreamChunk(content_delta="done")
			for i, (name, args) in enumerate(steps):
				yield LLMStreamChunk(
					tool_call_delta={"id": name, "index": i, "function": {"name": name, "arguments": json.dumps(args)}}
				)

		async def ask(self, prompt, **options):
			return '{"action":"done","step_ok":true,"goal_achieved":true}'

		async def close(self):
			pass

	strategy = PORPlugin if mode == "parallel_por" else ReActPlugin
	registry = PluginRegistry()
	registry.register_many([strategy, MemoryPlugin])
	path = tmp_path / "scope.yaml"
	path.write_text(
		yaml.safe_dump(
			{
				"name": "scope",
				"runtime": {"allowed_capabilities": ["memory_read", "memory_write"]},
				"plugins": {strategy.name: {"enabled": True}, "memory": {"enabled": True, **config}},
			}
		)
	)

	def make(interrupt=False):
		engine = Engine(plugin_registry=registry, kv_store=kv, checkpoint_store=checkpoints)
		return engine, engine.load_agent_template(str(path)).instantiate(models=AgentModels(default=Model(interrupt)))

	identity = {"region": "east", "tenant_id": "red"}
	if mode != "parallel_por":
		engine, agent = make(interrupt=True)
		try:
			stream = agent.stream("run", metadata={"run_id": "r", **identity})
			async for event in stream:
				if event.type == EventType.STREAM_DELTA:
					await stream.aclose()
					break
			saved = await checkpoints.latest("r")
			assert saved.state["plugin_scopes"] == {"memory": "default|region=east|tenant_id=red"}
			if mode == "legacy_resume":
				await checkpoints.save(
					replace(
						saved,
						sequence=saved.sequence + 1,
						state={k: v for k, v in saved.state.items() if k != "plugin_scopes"},
					)
				)
		finally:
			await engine.close()
	engine, agent = make()
	try:
		if mode == "legacy_resume":
			before = await checkpoints.list("r")
			with monkeypatch.context() as guard:

				def unexpected(*args, **kwargs):
					pytest.fail("Unowned resume reached a plugin hook or model")

				guard.setattr(MemoryPlugin, "on_execution_start", unexpected)
				guard.setattr(Model, "stream", unexpected)
				for incomplete in [None, {"region": "east"}, {"region": "east", "tenant_id": "  "}]:
					with pytest.raises(ValueError, match="original resume metadata"):
						await agent.resume("r", metadata=incomplete)
			assert await checkpoints.list("r") == before
		stream = (
			agent.stream("run", metadata=identity)
			if mode == "parallel_por"
			else agent.resume_stream("r", metadata=identity if mode == "legacy_resume" else None)
		)
		events = [event async for event in stream]
		assert events[-1].type == EventType.DONE
	finally:
		await engine.close()
	assert "red remembered" in str(results) and "blue remembered" not in str(results)
	loaded = MemoryPlugin()
	loaded.initialize(config, PluginContext(kv_store=kv))
	for tenant, ctx in records.items():
		listed = (await loaded._tool_memory_list({}, {"exec_ctx": ctx})).content["memories"]
		assert any(row["content"] == "written by red" for row in listed) == (tenant == "red")
	assert (await loaded._tool_memory_list({}, {})).content["count"] == 0


def test_legacy_memory_scope_uses_original_metadata_and_preserves_unisolated_agents():
	plugin = MemoryPlugin()
	plugin.initialize({"scope_keys": ["agent_name", "tenant_id", "region"]}, PluginContext())
	saved = {"agent_name": "host", "tenant_id": "red"}
	assert plugin.restore_legacy_scope(saved, {"region": "east"}) == "default|agent_name=host|tenant_id=red|region=east"
	with pytest.raises(ValueError, match="conflicts.*tenant_id"):
		plugin.restore_legacy_scope(saved, {"tenant_id": "blue", "region": "east"})

	plugin.initialize({"scope_keys": ["agent_name"], "session_scope": True}, PluginContext())
	assert (
		plugin.restore_legacy_scope({"agent_name": "host", "session_id": "s"}, {})
		== "default|agent_name=host|session_id=s"
	)


async def test_memory_tools_deduplicate_and_report_empty_results(memory_plugin):
	ctx = {"exec_ctx": ExecutionContext()}
	reply = await memory_plugin._tool_memory_search({"query": "absent"}, ctx)
	assert "Memories: none" in reply.context_view()
	args = {"content": "Remember this unique preference"}
	assert (await memory_plugin._tool_memory_add(args, ctx)).content["status"] == "ok"
	assert (await memory_plugin._tool_memory_add(args, ctx)).content["status"] == "duplicate"
	assert (await memory_plugin._tool_memory_delete({"id": ""}, ctx)).is_error
