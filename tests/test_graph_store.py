import pytest

from axc_agent_engine.observability.audit import InMemoryAuditSink
from axc_agent_engine.core.context import ExecutionContext, ExecutionServices
from axc_agent_engine.plugins.builtin.graph.support import InMemoryGraphStore
from axc_agent_engine.plugins import PluginContext
from axc_agent_engine.plugins.builtin.graph.plugin import GraphPlugin
from axc_agent_engine.storage.result_store import InMemoryResultStore


def test_graph_store_upsert_and_search_relation():
	store = InMemoryGraphStore()
	store.upsert_relation("Alice", "Acme", "WORKS_AT", "Alice works at Acme")
	results = store.search("Alice", depth=1)
	assert results
	assert results[0].relations[0]["relation_type"] == "WORKS_AT"


def test_graph_store_entity_relation_crud():
	store = InMemoryGraphStore()
	entity = store.upsert_entity("Alice", "person")
	relation = store.upsert_relation("Alice", "Acme", "WORKS_AT")
	assert store.get_entity(entity["id"])["name"] == "Alice"
	assert store.list_entities()
	assert store.get_relation(relation["id"])["relation_type"] == "WORKS_AT"
	assert store.list_relations(entity_id=entity["id"])
	assert store.delete_relation(relation["id"]) is True
	assert store.delete_entity(entity["id"]) is True


def test_graph_entity_resolution_is_type_aware_and_alias_based():
	store = InMemoryGraphStore()
	person = store.upsert_entity("Python", "person", aliases=["Py"])
	language = store.upsert_entity("Python", "language")
	again = store.upsert_entity("Py", "person")
	assert again["id"] == person["id"]
	assert language["id"] != person["id"]
	assert store.get_entity(person["id"])["mention_count"] == 2


@pytest.mark.asyncio
async def test_graph_plugin_exposes_write_tools():
	plugin = GraphPlugin()
	plugin.initialize({}, PluginContext())
	tools = {tool.name: tool for tool in plugin.get_tools()}
	assert "graph_upsert_entity" in tools
	assert "graph_upsert_relation" in tools
	assert "graph_list_entities" in tools
	assert "graph_delete_relation" in tools
	assert tools["graph_search"].capability == "graph_read"
	assert tools["graph_delete_entity"].risk_level == "dangerous"
	await plugin._tool_upsert_relation(
		{"source": "Alice", "target": "Acme", "relation_type": "WORKS_AT", "description": "employment"},
		{},
	)
	result = await plugin._tool_graph_search({"query": "Alice", "depth": 1}, {})
	assert result.content["results"]


@pytest.mark.asyncio
async def test_graph_plugin_can_disable_writes_and_deletes():
	plugin = GraphPlugin()
	plugin.initialize({"allow_writes": False, "allow_deletes": False}, PluginContext())

	write_result = await plugin._tool_upsert_entity({"name": "Alice"}, {})
	delete_result = await plugin._tool_delete_entity({"entity_id": "entity:1"}, {})

	assert write_result.is_error
	assert "disabled" in write_result.content
	assert delete_result.is_error
	assert "disabled" in delete_result.content


@pytest.mark.asyncio
async def test_graph_plugin_enforces_type_allowlists():
	plugin = GraphPlugin()
	plugin.initialize({"allowed_entity_types": ["person"], "allowed_relation_types": ["WORKS_AT"]}, PluginContext())

	entity_result = await plugin._tool_upsert_entity({"name": "Acme", "entity_type": "company"}, {})
	relation_result = await plugin._tool_upsert_relation({"source": "Alice", "target": "Acme", "relation_type": "FOUNDED"}, {})

	assert entity_result.is_error
	assert "not allowed" in entity_result.content
	assert relation_result.is_error
	assert "not allowed" in relation_result.content


@pytest.mark.asyncio
async def test_graph_plugin_status_reload_and_source_errors(tmp_path):
	missing = tmp_path / "missing.jsonl"
	source = tmp_path / "graph.jsonl"
	source.write_text(
		'{"id":"alice","name":"Alice","type":"person","description":"Engineer"}\n'
		'{"source_id":"alice","target_id":"acme","relation_type":"WORKS_AT"}\n'
		'not-json\n',
		encoding="utf-8",
	)
	plugin = GraphPlugin()
	plugin.initialize({"sources": [str(missing), str(source)]}, PluginContext())

	status = await plugin._tool_status({}, {})
	reloaded = await plugin._tool_reload_sources({"clear_existing": True}, {})

	assert status.content["entities"] >= 1
	assert status.content["load_errors"]
	assert reloaded.content["entities"] >= 1
	assert reloaded.content["source_stats"]["sources"] == 2


@pytest.mark.asyncio
async def test_graph_plugin_audits_writes_and_syncs_metadata():
	plugin = GraphPlugin()
	plugin.initialize({}, PluginContext())
	audit = InMemoryAuditSink()
	ctx = ExecutionContext(services=ExecutionServices(audit_sink=audit))
	ctx.state.metadata.update({"agent_name": "agent-a", "session_id": "sess-1"})

	result = await plugin._tool_upsert_entity(
		{"name": "Alice", "entity_type": "person", "description": "Engineer"},
		{"exec_ctx": ctx},
	)
	events = await audit.list_events()

	assert not result.is_error
	assert ctx.state.metadata["graph"]["last_action"] == "upsert_entity"
	assert events[-1].type == "graph_entity_upserted"
	assert events[-1].actor == "agent-a"


@pytest.mark.asyncio
async def test_graph_plugin_externalizes_large_export():
	store = InMemoryResultStore()
	plugin = GraphPlugin()
	plugin.initialize({"max_result_bytes": 64}, PluginContext())
	await plugin._tool_upsert_relation({"source": "Alice", "target": "Acme", "description": "x" * 200}, {})

	result = await plugin._tool_export({}, {"result_store": store})

	assert not result.is_error
	assert result.content["truncated"] is True
	assert result.artifacts
	assert "Alice" in await store.get(result.artifacts[0].id, 0, 1000)


@pytest.mark.asyncio
async def test_graph_plugin_limits_search_depth_and_limit():
	plugin = GraphPlugin()
	plugin.initialize({"max_depth": 1, "max_limit": 1}, PluginContext())
	await plugin._tool_upsert_relation({"source": "Alice", "target": "Acme"}, {})
	await plugin._tool_upsert_relation({"source": "Bob", "target": "Beta"}, {})

	result = await plugin._tool_graph_search({"query": "a", "depth": 99, "limit": 99}, {})

	assert result.content["count"] <= 1
	assert result.content["depth"] == 1
