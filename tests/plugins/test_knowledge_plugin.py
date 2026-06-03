import pytest

from axc_agent_engine.plugins import PluginContext
from axc_agent_engine.plugins.builtin.knowledge.plugin import (
	KnowledgePlugin,
	_filter_from_config,
	_highlights,
	_merge_filters,
	_normalize_document,
	_normalize_response,
	_normalize_result,
	_normalize_trace,
	_tokenize,
)
from axc_agent_engine.plugins.builtin.knowledge.support import (
	KnowledgeDocument,
	KnowledgeFilter,
	KnowledgeSearchRequest,
	KnowledgeSearchResponse,
	RetrievalResult,
	RetrievalTrace,
)


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


class MountedIndex:
	def __init__(self, raw):
		self.raw = raw
		self.requests = []

	async def search_with_trace(self, request):
		self.requests.append(request)
		return self.raw


class SearchOnlyIndex:
	def __init__(self):
		self.calls = []

	def search(self, query, top_k=5, candidate_k=30):
		if not isinstance(query, str):
			raise TypeError("legacy signature only")
		self.calls.append((query, top_k, candidate_k))
		return [{"text": query, "score": 0.9, "source": "search-only"}]


class TraceIndex:
	async def search_with_trace(self, request):
		return KnowledgeSearchResponse(
			results=[RetrievalResult(id="trace", text="trace hit", score=0.8, retrieval="trace")],
			trace=RetrievalTrace(query=request.query, candidate_count=1, returned_count=1),
		)


@pytest.mark.asyncio
async def test_knowledge_mounted_index_normalizes_dict_trace_and_merges_filters():
	index = MountedIndex({
		"data": [
			{"id": "low", "text": "drop", "score": 0.1, "source": "s"},
			{"id": "high", "content": "keep", "relevance": 0.9, "metadata": {"source": "m"}},
		],
		"trace": {"query": "rewritten", "candidate_count": 2, "returned_count": 1, "reranked": True},
	})
	plugin = KnowledgePlugin()
	plugin.initialize({
		"namespace": "base",
		"filters": {"metadata": {"tenant": "t1"}},
	}, PluginContext(resources={"knowledge.index": index}))

	payload = await plugin._hybrid_search_payload(
		"query",
		top_k=5,
		candidate_k=0,
		filters={"source": "docs", "tenant": "override"},
		min_score=0.5,
		include_trace=True,
	)

	assert [item["id"] for item in payload["results"]] == ["high"]
	assert payload["results"][0]["source"] == "m"
	assert payload["trace"]["sources"][0]["source"] == "knowledge.index"
	assert index.requests[0].candidate_k == 30
	assert index.requests[0].filters.namespace == "base"
	assert index.requests[0].filters.metadata == {"tenant": "override"}


@pytest.mark.asyncio
async def test_knowledge_fail_fast_resource_contract_edges(tmp_path):
	class NoSearchIndex:
		pass

	class EmptyVectorStore:
		async def search(self, vector, top_k=5):
			return None

	class Embedding:
		async def embed(self, texts):
			return [[1.0]]

	with pytest.raises(RuntimeError, match="requires sources"):
		KnowledgePlugin().initialize({}, PluginContext())
	with pytest.raises(RuntimeError, match="source load failed"):
		KnowledgePlugin().initialize({"sources": ["missing.md"]}, PluginContext(workspace=str(tmp_path)))
	with pytest.raises(RuntimeError, match="query_rewrite requires"):
		KnowledgePlugin().initialize(
			{"query_rewrite": {"enabled": True}},
			PluginContext(resources={"knowledge.index": MountedIndex([])}),
		)

	plugin = KnowledgePlugin()
	plugin.initialize({}, PluginContext(resources={"knowledge.index": NoSearchIndex()}))
	with pytest.raises(RuntimeError, match="must expose"):
		await plugin._hybrid_search_payload("x")

	vector = KnowledgePlugin()
	vector.initialize({}, PluginContext(resources={
		"knowledge.embedding": Embedding(),
		"knowledge.vector_store": EmptyVectorStore(),
	}))
	with pytest.raises(RuntimeError, match="returned None"):
		await vector._hybrid_search_payload("x")

	trace = KnowledgePlugin()
	trace.initialize({}, PluginContext(resources={"knowledge.index": TraceIndex()}))
	payload = await trace._hybrid_search_payload("x", include_trace=True)
	assert payload["results"][0]["id"] == "trace"


@pytest.mark.asyncio
async def test_knowledge_search_only_index_requires_request_signature_and_tool_errors():
	plugin = KnowledgePlugin()
	index = SearchOnlyIndex()
	plugin.initialize({}, PluginContext(resources={"knowledge.index": index}))

	empty = await plugin._tool_knowledge_search({"query": "  "}, {})

	with pytest.raises(TypeError, match="legacy signature only"):
		await plugin._tool_knowledge_search({"query": "hello", "include_trace": True}, {})
	assert empty.is_error


@pytest.mark.asyncio
async def test_knowledge_mounted_documents_sequence_and_provider_errors():
	class Provider:
		def list_documents(self):
			return [
				{"id": "a", "text": "alpha beta", "namespace": "n1", "metadata": {"source": "p"}},
				{"id": "b", "text": "plain beta"},
			]

	plugin = KnowledgePlugin()
	plugin.initialize({}, PluginContext(resources={"knowledge.documents": Provider()}))
	await plugin.on_execution_start(None)

	payload = await plugin._hybrid_search_payload("beta", top_k=5, include_trace=True)
	bad = KnowledgePlugin()
	bad.initialize({}, PluginContext(resources={"knowledge.documents": object()}))
	malformed = KnowledgePlugin()
	malformed.initialize({}, PluginContext(resources={"knowledge.documents": ["plain beta"]}))

	assert len(payload["results"]) == 2
	assert payload["trace"]["sources"][0]["source"] == "local"
	with pytest.raises(RuntimeError, match="list_documents"):
		await bad._hybrid_search_payload("x")
	with pytest.raises(RuntimeError, match="unsupported knowledge document"):
		await malformed._hybrid_search_payload("x")


@pytest.mark.asyncio
async def test_knowledge_vector_store_requires_embedding_and_searches_when_mounted():
	class Embedding:
		def __init__(self, vectors):
			self.vectors = vectors

		async def embed(self, texts):
			return self.vectors

	class VectorStore:
		def __init__(self):
			self.calls = []

		async def search(self, vector, top_k=5):
			self.calls.append((vector, top_k))
			return [{"text": "vector hit", "score": 0.8}]

	missing_embedding = KnowledgePlugin()
	missing_embedding.initialize({}, PluginContext(resources={"knowledge.vector_store": VectorStore()}))
	with pytest.raises(RuntimeError, match="requires mounted knowledge.embedding"):
		await missing_embedding._hybrid_search_payload("q")

	store = VectorStore()
	plugin = KnowledgePlugin()
	plugin.initialize({}, PluginContext(resources={
		"knowledge.vector_store": store,
		"knowledge.embedding": Embedding([[0.1, 0.2]]),
	}))
	payload = await plugin._hybrid_search_payload("q", candidate_k=3, include_trace=True)
	empty = KnowledgePlugin()
	empty.initialize({}, PluginContext(resources={
		"knowledge.vector_store": store,
		"knowledge.embedding": Embedding([]),
	}))

	assert payload["results"][0]["text"] == "vector hit"
	assert store.calls == [([0.1, 0.2], 3)]
	with pytest.raises(RuntimeError, match="returned no vectors"):
		await empty._hybrid_search_payload("q")


def test_knowledge_normalizers_filters_tokenize_and_highlights():
	request = KnowledgeSearchRequest(query="alpha", top_k=2, include_trace=True)

	response = _normalize_response([KnowledgeDocument(id="d", text="doc", source="s")], request, "idx")
	assert isinstance(response, KnowledgeSearchResponse)
	assert response.trace.candidate_count == 1
	with pytest.raises(RuntimeError, match="returned None"):
		_normalize_response(None, request, "idx")
	with pytest.raises(RuntimeError, match="unsupported"):
		_normalize_response(object(), request, "idx")

	assert _normalize_result(RetrievalResult(id="r", text="x", score=1, retrieval="test"), "fb").id == "r"
	with pytest.raises(RuntimeError, match="unsupported knowledge search result"):
		_normalize_result("raw", "fb")
	assert _normalize_document(KnowledgeDocument(id="d", text="x"), 3).id == "d"
	with pytest.raises(RuntimeError, match="unsupported knowledge document"):
		_normalize_document("raw", 3)
	assert _normalize_document({"content": "x", "namespace": "n"}, 3).metadata["namespace"] == "n"
	assert _normalize_trace(RetrievalTrace(query="q"), request, 1).query == "q"
	assert _normalize_trace({}, request, 3).returned_count == 3
	assert _normalize_trace(None, request, 3).returned_count == 2
	assert _normalize_trace({}, KnowledgeSearchRequest(query="q", include_trace=False), 1) is None

	base = _filter_from_config({"namespace": "n", "acl_tags": ["a"], "k": "v"})
	merged = _merge_filters(base, KnowledgeFilter(source="s", metadata={"k": "override"}))
	assert merged.namespace == "n"
	assert merged.source == "s"
	assert merged.allowed_acl_tags == ["a"]
	assert merged.metadata == {"k": "override"}
	assert _filter_from_config(KnowledgeFilter(), namespace="tenant").namespace == "tenant"
	assert _filter_from_config("bad", namespace="tenant").namespace == "tenant"

	assert "向量" in _tokenize("向量检索 abc_1")
	assert _highlights("alpha beta", "xx alpha yy beta zz", max_items=1) == ["xx alpha yy beta zz"]
