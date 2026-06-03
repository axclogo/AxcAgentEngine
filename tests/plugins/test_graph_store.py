import pytest

from axc_agent_engine.observability.audit import InMemoryAuditSink
from axc_agent_engine.core.context import ExecutionContext, ExecutionServices
from axc_agent_engine.plugins.builtin.graph.audit import GraphAuditRecorder
from axc_agent_engine.plugins.builtin.graph.config import GraphConfig
from axc_agent_engine.plugins.builtin.graph.presenter import GraphPresenter
from axc_agent_engine.plugins.builtin.graph.policy import GraphPolicy
from axc_agent_engine.plugins.builtin.graph.service import GraphService
from axc_agent_engine.plugins.builtin.graph.source_loader import GraphSourceLoader
from axc_agent_engine.plugins.builtin.graph.support import InMemoryGraphStore
from axc_agent_engine.plugins.builtin.graph.tool_handlers import GraphToolHandlers
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
	plugin.initialize({}, PluginContext(resources={"graph.store": InMemoryGraphStore()}))
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


def test_graph_plugin_uses_mounted_store_resource():
	store = InMemoryGraphStore()
	store.upsert_entity("Mounted", "concept")
	plugin = GraphPlugin()
	plugin.initialize({}, PluginContext(resources={"graph.store": store}))

	assert plugin._store is store
	result = plugin._store.search("Mounted")
	assert result and result[0].entity["name"] == "Mounted"


@pytest.mark.asyncio
async def test_graph_plugin_can_disable_writes_and_deletes():
	plugin = GraphPlugin()
	plugin.initialize({"allow_writes": False, "allow_deletes": False}, PluginContext(resources={"graph.store": InMemoryGraphStore()}))

	write_result = await plugin._tool_upsert_entity({"name": "Alice"}, {})
	delete_result = await plugin._tool_delete_entity({"entity_id": "entity:1"}, {})

	assert write_result.is_error
	assert "disabled" in write_result.content
	assert delete_result.is_error
	assert "disabled" in delete_result.content


@pytest.mark.asyncio
async def test_graph_plugin_enforces_type_allowlists():
	plugin = GraphPlugin()
	plugin.initialize({"allowed_entity_types": ["person"], "allowed_relation_types": ["WORKS_AT"]}, PluginContext(resources={"graph.store": InMemoryGraphStore()}))

	entity_result = await plugin._tool_upsert_entity({"name": "Acme", "entity_type": "company"}, {})
	relation_result = await plugin._tool_upsert_relation({"source": "Alice", "target": "Acme", "relation_type": "FOUNDED"}, {})

	assert entity_result.is_error
	assert "not allowed" in entity_result.content
	assert relation_result.is_error
	assert "not allowed" in relation_result.content


@pytest.mark.asyncio
async def test_graph_plugin_source_errors_raise_on_initialize(tmp_path):
	missing = tmp_path / "missing.jsonl"
	source = tmp_path / "graph.jsonl"
	source.write_text(
		'{"id":"alice","name":"Alice","type":"person","description":"Engineer"}\n'
		'{"source_id":"alice","target_id":"acme","relation_type":"WORKS_AT"}\n'
		'not-json\n',
		encoding="utf-8",
	)
	plugin = GraphPlugin()
	with pytest.raises(ValueError, match="source not found"):
		plugin.initialize({"sources": [str(missing), str(source)]}, PluginContext(resources={"graph.store": InMemoryGraphStore()}))
	with pytest.raises(ValueError, match="Expecting value"):
		plugin.initialize({"sources": [str(source)]}, PluginContext(resources={"graph.store": InMemoryGraphStore()}))

	valid = tmp_path / "valid.jsonl"
	valid.write_text(
		'{"id":"alice","name":"Alice","type":"person","description":"Engineer"}\n'
		'{"source_id":"alice","target_id":"acme","relation_type":"WORKS_AT"}\n',
		encoding="utf-8",
	)
	ok = GraphPlugin()
	ok.initialize({"sources": [str(valid)]}, PluginContext(resources={"graph.store": InMemoryGraphStore()}))
	status = await ok._tool_status({}, {})
	reloaded = await ok._tool_reload_sources({"clear_existing": True}, {})
	assert status.content["entities"] >= 1
	assert reloaded.is_error
	assert "mounted graph.store" in reloaded.content


@pytest.mark.asyncio
async def test_graph_plugin_audits_writes_and_syncs_metadata():
	plugin = GraphPlugin()
	plugin.initialize({}, PluginContext(resources={"graph.store": InMemoryGraphStore()}))
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
	plugin.initialize({"max_result_bytes": 64}, PluginContext(resources={"graph.store": InMemoryGraphStore()}))
	await plugin._tool_upsert_relation({"source": "Alice", "target": "Acme", "description": "x" * 200}, {})

	result = await plugin._tool_export({}, {"result_store": store})

	assert not result.is_error
	assert result.content["truncated"] is True
	assert result.artifacts
	assert "Alice" in await store.get(result.artifacts[0].id, 0, 1000)


@pytest.mark.asyncio
async def test_graph_plugin_limits_search_depth_and_limit():
	plugin = GraphPlugin()
	plugin.initialize({"max_depth": 1, "max_limit": 1}, PluginContext(resources={"graph.store": InMemoryGraphStore()}))
	await plugin._tool_upsert_relation({"source": "Alice", "target": "Acme"}, {})
	await plugin._tool_upsert_relation({"source": "Bob", "target": "Beta"}, {})

	result = await plugin._tool_graph_search({"query": "a", "depth": 99, "limit": 99}, {})

	assert result.content["count"] <= 1
	assert result.content["depth"] == 1


def test_graph_service_read_source_and_payload_branches(tmp_path):
	source = tmp_path / "graph.jsonl"
	source.write_text(
		'{"id":"alice","name":"Alice","type":"person","metadata":{"secret":1}}\n'
		'{"source":"Alice","target":"Bob","relation_type":"KNOWS"}\n'
		'{"bad": true}\n'
		'{bad-json}\n',
		encoding="utf-8",
	)
	config = GraphConfig.from_dict({"sources": [], "include_metadata": False, "namespace": "ns", "max_limit": 2})
	service = GraphService(config, InMemoryGraphStore())
	with pytest.raises(ValueError, match="unknown graph record"):
		service.read_source(str(source))
	valid = tmp_path / "valid_graph.jsonl"
	valid.write_text(
		'{"id":"alice","name":"Alice","type":"person","metadata":{"secret":1}}\n'
		'{"source":"Alice","target":"Bob","relation_type":"KNOWS"}\n',
		encoding="utf-8",
	)
	entities, relations = service.read_source(str(valid))
	assert len(entities) == 1
	assert len(relations) == 1
	assert len(service.load_errors) == 1
	with pytest.raises(OSError):
		service.read_source(str(source / "missing"))

	prepared = service.upsert_entity({"name": " Alice ", "entity_type": "person", "metadata": {"x": 1}})
	assert "metadata" not in service.get_entity(prepared["entity"]["id"])
	relation = service.upsert_relation({"source": "Alice", "target": "Bob", "relation_type": "KNOWS"})
	assert service.get_relation(relation["relation"]["id"])
	assert service.inject_context("Alice").startswith("[相关实体与关系]")
	search_payload, search_limit = service.search("Alice", "bad", 99)
	assert search_limit == 2
	assert search_payload["depth"] == 0
	assert service.page({"limit": 99, "offset": "bad"}) == (2, 0)
	exported = service.export_payload()
	assert exported["namespace"] == "ns"
	assert service.status_payload()["config"]["max_limit"] == 2
	with pytest.raises(ValueError):
		service.reload_sources(clear_existing=True)
	assert service.metadata_payload("x")["last_action"] == "x"


def test_graph_source_loader_limits_validation_and_store_errors(tmp_path):
	source = tmp_path / "graph.jsonl"
	source.write_text("{}", encoding="utf-8")
	config = GraphConfig.from_dict({
		"sources": [str(source)],
		"max_entities": 1,
		"max_relations": 1,
		"allowed_entity_types": ["person"],
		"allowed_relation_types": ["KNOWS"],
	})
	class Store:
		def __init__(self):
			self.entities = []
			self.relations = []

		def upsert_entity(self, name, entity_type, aliases, **metadata):
			entity = {"name": name}
			self.entities.append(entity)
			return entity

		def upsert_relation(self, *args, **kwargs):
			relation = {"id": kwargs.get("external_id", "")}
			self.relations.append(relation)
			return relation

	store = Store()
	errors = []
	items = {
		"entities": [
			{"id": "bad-type", "name": "Secret", "type": "secret"},
			{"id": "alice", "name": "Alice", "type": "person", "aliases": [" A ", ""], "description": "d"},
			{"id": "overflow", "name": "Overflow", "type": "person"},
		],
		"relations": [
			{"id": "bad-rel", "source": "Alice", "target": "Bob", "relation_type": "BLOCKED"},
			{"id": "missing", "relation_type": "KNOWS"},
			{"id": "r1", "source_id": "alice", "target": "Bob", "relation_type": "KNOWS"},
			{"id": "r2", "source": "Alice", "target": "Carol", "relation_type": "KNOWS"},
		],
	}
	loader = GraphSourceLoader(
		config,
		store,
		GraphPolicy({"person"}, set(), {"KNOWS"}, set()),
		errors,
		lambda path: (items["entities"], items["relations"]),
		lambda: len(store.entities),
		lambda: len(store.relations),
	)
	with pytest.raises(ValueError, match="not allowed"):
		loader.load()
	assert any("not allowed" in item["error"] for item in errors)


def test_graph_source_loader_records_store_exceptions(tmp_path):
	source = tmp_path / "graph.jsonl"
	source.write_text("{}", encoding="utf-8")
	config = GraphConfig.from_dict({"sources": [str(source)]})
	errors = []

	class Store:
		def upsert_entity(self, *args, **kwargs):
			raise RuntimeError("entity boom")

		def upsert_relation(self, *args, **kwargs):
			raise RuntimeError("relation boom")

	loader = GraphSourceLoader(
		config,
		Store(),
		GraphPolicy(set(), set(), set(), set()),
		errors,
		lambda path: ([{"id": "e", "name": "E"}], [{"id": "r", "source": "A", "target": "B"}]),
		lambda: 0,
		lambda: 0,
	)
	with pytest.raises(RuntimeError, match="entity boom"):
		loader.load()


@pytest.mark.asyncio
async def test_graph_tool_handlers_error_and_crud_paths():
	config = GraphConfig.from_dict({"max_entities": 1, "max_relations": 1, "allow_deletes": True})
	service = GraphService(config, InMemoryGraphStore())
	handlers = GraphToolHandlers(service, GraphPresenter(config, PluginContext()), GraphAuditRecorder(config))
	ctx = ExecutionContext()
	context = {"exec_ctx": ctx}
	assert set(handlers.tools())
	assert (await handlers.graph_search({"query": ""}, context)).is_error
	entity = await handlers.upsert_entity({"name": "Alice", "entity_type": "person"}, context)
	assert not entity.is_error
	assert (await handlers.upsert_entity({"name": "Bob"}, context)).is_error
	assert ctx.state.metadata["graph"]["last_action"] == "upsert_entity"
	assert (await handlers.get_entity({"entity_id": "missing"}, context)).is_error
	assert (await handlers.get_entity({"entity_id": entity.content["entity"]["id"]}, context)).content["entity"]["name"] == "Alice"

	relation = await handlers.upsert_relation({"source": "Alice", "target": "Bob", "relation_type": "KNOWS"}, context)
	assert not relation.is_error
	assert (await handlers.upsert_relation({"source": "Bob", "target": "Alice"}, context)).is_error
	assert (await handlers.upsert_relation({"source": "", "target": ""}, context)).is_error
	relation_id = relation.content["relation"]["id"]
	assert (await handlers.get_relation({"relation_id": "missing"}, context)).is_error
	assert (await handlers.get_relation({"relation_id": relation_id}, context)).content["relation"]["id"] == relation_id
	assert (await handlers.list_entities({"limit": 10}, context)).content["count"] >= 1
	assert (await handlers.list_relations({"limit": 10}, context)).content["count"] >= 1
	assert (await handlers.status({}, context)).content["entities"] >= 1
	assert (await handlers.export({}, context)).content["entities"]
	assert (await handlers.delete_relation({"relation_id": relation_id}, context)).content["deleted"] is True
	assert (await handlers.delete_entity({"entity_id": entity.content["entity"]["id"]}, context)).content["deleted"] is True
	assert (await handlers.delete_entity({"entity_id": "missing"}, context)).content["deleted"] is False


@pytest.mark.asyncio
async def test_graph_tool_handlers_policy_rejections_and_reload():
	config = GraphConfig.from_dict({
		"allow_writes": False,
		"allow_deletes": False,
		"denied_entity_types": ["secret"],
		"denied_relation_types": ["BLOCKED"],
	})
	service = GraphService(config, InMemoryGraphStore())
	handlers = GraphToolHandlers(service, GraphPresenter(config, PluginContext()), GraphAuditRecorder(config))
	context = {"exec_ctx": ExecutionContext()}
	assert (await handlers.upsert_entity({"name": "Alice"}, context)).is_error
	assert (await handlers.upsert_relation({"source": "Alice", "target": "Bob"}, context)).is_error
	assert (await handlers.delete_relation({"relation_id": "x"}, context)).is_error
	assert (await handlers.delete_entity({"entity_id": "x"}, context)).is_error
	assert (await handlers.reload_sources({}, context)).is_error

	write_config = GraphConfig.from_dict({"denied_entity_types": ["secret"], "denied_relation_types": ["BLOCKED"]})
	write_service = GraphService(write_config, InMemoryGraphStore())
	write_handlers = GraphToolHandlers(write_service, GraphPresenter(write_config, PluginContext()), GraphAuditRecorder(write_config))
	assert (await write_handlers.upsert_entity({"name": "Alice", "entity_type": "secret"}, context)).is_error
	assert (await write_handlers.upsert_relation({"source": "Alice", "target": "Bob", "relation_type": "BLOCKED"}, context)).is_error
	assert (await write_handlers.reload_sources({"clear_existing": True}, context)).is_error
