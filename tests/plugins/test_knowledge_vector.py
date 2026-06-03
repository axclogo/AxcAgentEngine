"""Tests for official Knowledge plugin resource boundaries and hybrid retrieval."""
import pytest

from axc_agent_engine.plugins import PluginContext
from axc_agent_engine.plugins.builtin.knowledge.plugin import KnowledgePlugin
from axc_agent_engine.plugins.builtin.knowledge.support import KnowledgeDocument, RetrievalResult
from axc_agent_engine.runtime.resources import ResourceRegistry


class Embeddings:
	async def embed(self, texts):
		return [[1.0, 0.0] if "python" in text.lower() else [0.0, 1.0] for text in texts]


class VectorStore:
	def __init__(self):
		self.search_called = False

	async def search(self, embedding, top_k=5):
		self.search_called = True
		return [{
			"id": "vec1",
			"text": "Python vector retrieval guide",
			"score": 0.91,
			"retrieval": "vector",
			"source": "vector",
			"metadata": {"namespace": "tenant-a", "chunk_id": 3},
		}]


class MountedIndex:
	async def search(self, request):
		assert request.filters.namespace == "tenant-a"
		return [
			RetrievalResult(
				id="idx1",
				text="Python hybrid retrieval setup",
				score=0.95,
				retrieval="mounted",
				source="index",
				metadata={"namespace": "tenant-a", "chunk_id": 1},
			)
		]


class FailingIndex:
	async def search(self, request):
		raise RuntimeError("index down")


@pytest.mark.asyncio
async def test_sources_bm25_search_has_citations_and_trace(tmp_path):
	doc = tmp_path / "tenant_a.md"
	doc.write_text("# Help\n\nPython retrieval setup for employees.", encoding="utf-8")
	plugin = KnowledgePlugin()
	plugin.initialize({
		"sources": ["tenant_a.md"],
		"namespace": "tenant-a",
		"metadata": {"acl_tags": ["employee"]},
		"include_trace": True,
	}, PluginContext(workspace=str(tmp_path)))

	output = await plugin._tool_knowledge_search({
		"query": "Python retrieval",
		"namespace": "tenant-a",
		"allowed_acl_tags": ["employee"],
		"include_trace": True,
	}, {})

	assert not output.is_error
	assert output.content["results"]
	assert output.content["results"][0]["citation"]["source"].endswith("tenant_a.md")
	assert output.content["trace"]["filtered"] is True


@pytest.mark.asyncio
async def test_mounted_index_is_part_of_plugin_hybrid_strategy():
	plugin = KnowledgePlugin()
	plugin.initialize(
		{"namespace": "tenant-a"},
		PluginContext(resources=ResourceRegistry({"knowledge.index": MountedIndex()})),
	)

	output = await plugin._tool_knowledge_search({"query": "python", "namespace": "tenant-a"}, {})

	assert not output.is_error
	assert output.content["results"][0]["id"] == "idx1"


@pytest.mark.asyncio
async def test_vector_store_requires_mounted_embedding():
	plugin = KnowledgePlugin()
	plugin.initialize({}, PluginContext(resources=ResourceRegistry({"knowledge.vector_store": VectorStore()})))

	with pytest.raises(RuntimeError, match="knowledge.embedding"):
		await plugin._tool_knowledge_search({"query": "python"}, {})


@pytest.mark.asyncio
async def test_vector_store_and_bm25_are_fused_by_plugin(tmp_path):
	doc = tmp_path / "doc.md"
	doc.write_text("Python BM25 retrieval guide", encoding="utf-8")
	vector_store = VectorStore()
	plugin = KnowledgePlugin()
	plugin.initialize(
		{"sources": ["doc.md"]},
		PluginContext(
			workspace=str(tmp_path),
			resources=ResourceRegistry({
				"knowledge.embedding": Embeddings(),
				"knowledge.vector_store": vector_store,
			}),
		),
	)

	output = await plugin._tool_knowledge_search({"query": "python", "top_k": 3, "include_trace": True}, {})

	assert not output.is_error
	assert vector_store.search_called
	assert output.content["results"]
	assert output.content["trace"]["candidate_count"] >= 2


@pytest.mark.asyncio
async def test_mounted_index_failure_is_not_silently_downgraded(tmp_path):
	doc = tmp_path / "doc.md"
	doc.write_text("Python BM25 retrieval guide", encoding="utf-8")
	plugin = KnowledgePlugin()
	plugin.initialize(
		{"sources": ["doc.md"]},
		PluginContext(workspace=str(tmp_path), resources=ResourceRegistry({"knowledge.index": FailingIndex()})),
	)

	with pytest.raises(RuntimeError, match="index down"):
		await plugin._tool_knowledge_search({"query": "python"}, {})


@pytest.mark.asyncio
async def test_mounted_documents_feed_plugin_owned_strategy():
	plugin = KnowledgePlugin()
	plugin.initialize(
		{},
		PluginContext(resources=ResourceRegistry({
			"knowledge.documents": [
				KnowledgeDocument(id="d1", text="Mounted document retrieval", metadata={"namespace": "n"}),
			]
		})),
	)
	await plugin.on_execution_start(None)

	output = await plugin._tool_knowledge_search({"query": "mounted", "namespace": "n"}, {})

	assert not output.is_error
	assert output.content["results"][0]["id"] == "d1"
