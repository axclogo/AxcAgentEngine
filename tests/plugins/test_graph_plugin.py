from __future__ import annotations

import pytest

from axc_agent_engine.plugins import PluginContext
from axc_agent_engine.plugins.builtin.graph.plugin import GraphPlugin
from axc_agent_engine.runtime.resources import ResourceRegistry


class Store:
	pass


def test_graph_plugin_requires_mounted_store():
	plugin = GraphPlugin()

	with pytest.raises(ValueError, match="graph.store"):
		plugin.initialize({}, PluginContext(resources=ResourceRegistry()))


@pytest.mark.asyncio
async def test_graph_plugin_delegates_tools_helpers_and_reload_updates_state(monkeypatch):
	calls = []

	class Service:
		def __init__(self, config, store):
			self.store = store
			self.load_errors = ["old"]
			self.source_stats = {"old": 1}
		def inject_context(self, topic):
			return f"context:{topic}"
		def load_sources(self):
			self.source_stats = {"loaded": 1}
		def read_source(self, source):
			return [{"id": source}], []
		def validate_entity_type(self, entity_type):
			return entity_type.upper()
		def validate_relation_type(self, relation_type):
			return relation_type.upper()
		def limit(self, value):
			return int(value)
		def page(self, args):
			return int(args["offset"]), int(args["limit"])
		def entity_count(self):
			return 3
		def relation_count(self):
			return 4
		def status_payload(self):
			return {"ok": True}

	class Handlers:
		def __init__(self, service, presenter, audit):
			self.service = service
		def tools(self):
			return {"graph_search": self.graph_search}
		async def graph_search(self, args, context):
			calls.append(("search", args))
			return "search"
		async def upsert_entity(self, args, context):
			return "entity"
		async def upsert_relation(self, args, context):
			return "relation"
		async def get_entity(self, args, context):
			return "get_entity"
		async def get_relation(self, args, context):
			return "get_relation"
		async def list_entities(self, args, context):
			return "list_entities"
		async def delete_entity(self, args, context):
			return "delete_entity"
		async def list_relations(self, args, context):
			return "list_relations"
		async def delete_relation(self, args, context):
			return "delete_relation"
		async def status(self, args, context):
			return "status"
		async def reload_sources(self, args, context):
			self.service.store = "new-store"
			self.service.load_errors = []
			self.service.source_stats = {"new": 2}
			return "reload"
		async def export(self, args, context):
			return "export"

	class Factory:
		def __init__(self, config, tools):
			self.tools_arg = tools
		def tools(self):
			return ["tool"]

	monkeypatch.setattr("axc_agent_engine.plugins.builtin.graph.plugin.GraphService", Service)
	monkeypatch.setattr("axc_agent_engine.plugins.builtin.graph.plugin.GraphToolHandlers", Handlers)
	monkeypatch.setattr("axc_agent_engine.plugins.builtin.graph.plugin.GraphToolFactory", Factory)
	plugin = GraphPlugin()
	plugin.initialize({}, PluginContext(resources=ResourceRegistry({"graph.store": Store()})))

	assert plugin.inject_context(None, "topic") == "context:topic"
	assert plugin.get_tools() == ["tool"]
	assert await plugin._tool_graph_search({"q": "x"}, {}) == "search"
	assert await plugin._tool_upsert_entity({}, {}) == "entity"
	assert await plugin._tool_upsert_relation({}, {}) == "relation"
	assert await plugin._tool_get_entity({}, {}) == "get_entity"
	assert await plugin._tool_get_relation({}, {}) == "get_relation"
	assert await plugin._tool_list_entities({}, {}) == "list_entities"
	assert await plugin._tool_delete_entity({}, {}) == "delete_entity"
	assert await plugin._tool_list_relations({}, {}) == "list_relations"
	assert await plugin._tool_delete_relation({}, {}) == "delete_relation"
	assert await plugin._tool_status({}, {}) == "status"
	assert await plugin._tool_export({}, {}) == "export"
	assert await plugin._tool_reload_sources({}, {}) == "reload"
	assert plugin._store == "new-store"
	assert plugin._load_errors == []
	assert plugin._source_stats == {"new": 2}
	plugin._load_sources()
	assert plugin._source_stats == {"loaded": 1}
	assert plugin._read_source("s") == ([{"id": "s"}], [])
	assert plugin._validate_entity_type("person") == "PERSON"
	assert plugin._validate_relation_type("knows") == "KNOWS"
	assert plugin._limit("5") == 5
	assert plugin._page({"offset": "1", "limit": "2"}) == (1, 2)
	assert plugin._entity_count() == 3
	assert plugin._relation_count() == 4
	assert plugin._status_payload() == {"ok": True}
