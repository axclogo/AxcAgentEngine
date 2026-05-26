import pytest

from axc_agent_engine.plugins import PluginContext
from axc_agent_engine.plugins.builtin.knowledge.plugin import KnowledgePlugin


@pytest.mark.asyncio
async def test_knowledge_plugin_search_tool_supports_filters_trace_and_citations(tmp_path):
	tenant_a = tmp_path / "tenant_a.md"
	tenant_a.write_text("# Help\n\nVector retrieval setup for employees.", encoding="utf-8")

	plugin = KnowledgePlugin()
	plugin.initialize({
		"sources": ["tenant_a.md"],
		"namespace": "tenant-a",
		"metadata": {"acl_tags": ["employee"]},
	}, PluginContext(workspace=str(tmp_path)))

	output = await plugin._tool_knowledge_search({
		"query": "vector retrieval employees",
		"top_k": 2,
		"namespace": "tenant-a",
		"allowed_acl_tags": ["employee"],
		"include_trace": True,
	}, {})

	assert output.is_error is False
	results = output.content["results"]
	assert results
	assert output.content["trace"]["filtered"] is True
	assert results[0]["citation"]["source"].endswith("tenant_a.md")
	assert "citation" in results[0]
	assert "highlights" in results[0]
