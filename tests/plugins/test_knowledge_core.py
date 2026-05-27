import pytest
import sys
import types

from axc_agent_engine.plugins.builtin.knowledge.support.ingestion import PdfDocumentParser, SourceDocument, TextDocumentParser
from axc_agent_engine.plugins.builtin.knowledge.support import (
	CascadeReranker,
	ExternalReranker,
	LLMQueryRewriter,
	LLMReranker,
	HashEmbeddingClient,
	HybridRetriever,
	InMemoryKnowledgeIndexStore,
	KnowledgeFilter,
	KnowledgeDocument,
	KnowledgeSearchRequest,
	LocalFileIngestionPipeline,
	RetrievalResult,
	SemanticChunker,
	ScoreReranker,
)
from axc_agent_engine.plugins import PluginContext
from axc_agent_engine.plugins.builtin.knowledge.plugin import (
	KnowledgePlugin,
	_filter_from_config,
	_merge_filters,
	_resource_name,
	_tool_filters,
)
from axc_agent_engine.plugins.builtin.knowledge.support.retrieval import (
	BM25Index,
	NoopQueryRewriter,
	_cosine,
	_dedupe_queries,
	_hash_embedding,
	_parse_llm_scores,
	_parse_query_array,
	_parse_rerank_scores,
	rrf_merge,
	normalize_filter,
	normalize_search_request,
	tokenize,
)


def test_semantic_chunker_preserves_heading_context():
	chunker = SemanticChunker(max_chunk_size=80, chunk_overlap=10)
	chunks = chunker.chunk_document("# API\n\nThis endpoint creates users. It validates input.", source="doc.md")
	assert chunks
	assert chunks[0].heading_path == "API"
	assert chunks[0].content_with_context.startswith("[API]")


def test_semantic_chunker_preamble_nested_and_fallback_split():
	chunker = SemanticChunker(max_chunk_size=128, chunk_overlap=8)
	content = "Intro paragraph before heading.\n\n# API\n\n" + ("Sentence one. " * 30) + "\n## Details\n\nSmall body"
	chunks = chunker.chunk_document(content, source="doc.md", title="Title")
	assert chunks[0].heading_path == "Title"
	assert any("API > Details" == chunk.heading_path for chunk in chunks)
	assert len(chunks) > 2
	assert all(chunk.to_dict()["metadata"]["source"] == "doc.md" for chunk in chunks)

	fallback = chunker.chunk_document("alpha beta gamma " * 40, source="plain.txt", title="Plain")
	assert fallback
	assert fallback[0].content_with_context.startswith("[Plain]")


def test_semantic_chunker_empty_and_recursive_split_edges():
	chunker = SemanticChunker(max_chunk_size=128, chunk_overlap=0)
	assert chunker.chunk_document("   ") == []
	assert chunker._recursive_split("x" * 20, [], max_size=5) == ["x" * 20]
	assert chunker._recursive_split("   ", [], max_size=5) == []


def test_hybrid_retriever_bm25_returns_ranked_results():
	docs = [
		KnowledgeDocument(id="1", text="Python vector search and BM25 retrieval"),
		KnowledgeDocument(id="2", text="Cooking recipes and ingredients"),
	]
	retriever = HybridRetriever(docs)
	results = retriever.bm25.search("vector retrieval", top_k=2)
	assert results[0].id == "1"
	assert results[0].retrieval == "bm25"


@pytest.mark.asyncio
async def test_in_memory_knowledge_index_hybrid_search_and_delete():
	store = InMemoryKnowledgeIndexStore(embedding_client=HashEmbeddingClient(), reranker=ScoreReranker())
	await store.add_documents([
		KnowledgeDocument(id="1", text="Python supports semantic chunking and vector retrieval"),
		KnowledgeDocument(id="2", text="Gardening notes about tomatoes"),
	])
	results = await store.search("semantic vector retrieval", top_k=1)
	assert results[0].id == "1"
	assert "rerank" in results[0].retrieval

	removed = await store.delete_documents(["1"])
	assert removed == 1
	assert [doc.id for doc in await store.list_documents()] == ["2"]


def test_hash_embedding_is_stable():
	assert _hash_embedding("semantic vector", 16) == _hash_embedding("semantic vector", 16)


class FakeLLM:
	def __init__(self, response: str) -> None:
		self.response = response

	async def ask(self, prompt: str) -> str:
		return self.response


@pytest.mark.asyncio
async def test_llm_query_rewriter_adds_original_and_rewrites():
	rewriter = LLMQueryRewriter(FakeLLM('["semantic search", "BM25 retrieval"]'))
	queries = await rewriter.rewrite("hybrid retrieval", max_queries=3)
	assert queries == ["hybrid retrieval", "semantic search", "BM25 retrieval"]


@pytest.mark.asyncio
async def test_llm_reranker_scores_candidates():
	reranker = LLMReranker(FakeLLM('[{"index": 1, "score": 0.9}, {"index": 0, "score": 0.1}]'))
	results = await reranker.rerank("query", [
		RetrievalResult(id="a", text="alpha", score=0.1, retrieval="bm25"),
		RetrievalResult(id="b", text="beta", score=0.2, retrieval="bm25"),
	], top_k=1)
	assert results[0].id == "b"
	assert "llm_rerank" in results[0].retrieval


@pytest.mark.asyncio
async def test_cascade_reranker_falls_back_to_score():
	class FailingReranker:
		async def rerank(self, query, results, top_k):
			raise RuntimeError("down")

	reranker = CascadeReranker([FailingReranker(), ScoreReranker()])
	results = await reranker.rerank("python retrieval", [
		RetrievalResult(id="1", text="python retrieval", score=0.1, retrieval="bm25"),
		RetrievalResult(id="2", text="cooking", score=0.2, retrieval="bm25"),
	], top_k=1)
	assert results[0].id == "1"


@pytest.mark.asyncio
async def test_knowledge_search_request_filters_trace_and_citations():
	store = InMemoryKnowledgeIndexStore(embedding_client=HashEmbeddingClient())
	await store.add_documents([
		KnowledgeDocument(
			id="public",
			text="Password reset instructions for employees",
			source="docs/public.md",
			metadata={"namespace": "tenant-a", "chunk_id": 7, "acl_tags": ["employee"], "title": "Public"},
		),
		KnowledgeDocument(
			id="private",
			text="Password reset admin backdoor",
			source="docs/private.md",
			metadata={"namespace": "tenant-b", "chunk_id": 2, "acl_tags": ["admin"], "title": "Private"},
		),
	])

	response = await store.search_with_trace(KnowledgeSearchRequest(
		query="password reset employees",
		top_k=3,
		filters=KnowledgeFilter(namespace="tenant-a", allowed_acl_tags=["employee"]),
		include_trace=True,
	))

	assert [item.id for item in response.results] == ["public"]
	assert response.trace is not None
	assert response.trace.filtered is True
	assert response.results[0].citation["chunk_id"] == 7
	assert response.results[0].highlights


@pytest.mark.asyncio
async def test_retrieval_filter_vector_hybrid_and_helpers():
	docs = [
		KnowledgeDocument(id="1", text="alpha vector retrieval", source="docs/a.md", metadata={"namespace": "n", "tags": ["a"], "acl_tags": "user", "chunk_id": 0}),
		KnowledgeDocument(id="2", text="beta cooking", source="docs/b.md", metadata={"namespace": "n", "tags": ["b"], "acl_tags": ["admin"], "chunk_id": 1}),
	]
	assert KnowledgeFilter(namespace="n", source_prefix="docs/", metadata={"tags": ["a"]}, allowed_acl_tags=["user"]).matches(docs[0])
	assert not KnowledgeFilter(source="other").matches(docs[0])
	assert normalize_filter({"namespace": "n", "acl_tags": ["user"], "custom": "x"}).metadata["custom"] == "x"
	assert normalize_search_request("alpha", top_k=2).query == "alpha"

	store = InMemoryKnowledgeIndexStore(embedding_client=HashEmbeddingClient(dimensions=16), reranker=ScoreReranker())
	assert await store.vector_search("none") == []
	await store.upsert_documents(docs)
	vector = await store.vector_search("alpha retrieval", top_k=1, filters={"namespace": "n"})
	assert vector and vector[0].retrieval == "vector"
	assert await store.delete_by_filter({"tags": ["b"]}) == 1
	assert len(await store.list_documents()) == 1

	async def vector_search(query, top_k, filters):
		return [RetrievalResult(id="1", text="alpha vector retrieval", score=0.9, retrieval="vector", metadata={"chunk_id": 0})]

	async def reranker(query, results, top_k):
		return []

	async def bad_rewriter(query, max_queries):
		raise RuntimeError("rewrite failed")

	retriever = HybridRetriever(docs, vector_search=vector_search, reranker=reranker, query_rewriter=bad_rewriter)
	response = await retriever.search_with_trace(KnowledgeSearchRequest("alpha retrieval", top_k=1, candidate_k=2, include_trace=True))
	assert response.results
	assert response.trace.candidate_count >= 1

	merged = rrf_merge(
		[RetrievalResult(id="1", text="a", score=1, retrieval="bm25")],
		[RetrievalResult(id="1", text="a", score=1, retrieval="vector")],
		top_k=1,
	)
	assert merged[0].retrieval == "hybrid"
	assert BM25Index([]).search("alpha") == []
	assert tokenize("中文abc_1 42")
	assert _cosine([], []) == 0.0
	assert _cosine([1], [1, 2]) == 0.0
	assert _cosine([0], [0]) == 0.0


@pytest.mark.asyncio
async def test_external_reranker_and_parse_helpers(monkeypatch):
	with pytest.raises(ValueError):
		ExternalReranker("")
	assert await ExternalReranker("http://rerank").rerank("q", [], 2) == []

	class Response:
		def raise_for_status(self):
			return None
		def json(self):
			return {"results": [{"index": 1, "score": 0.8}, {"document_index": 0, "relevance_score": "0.2"}]}
	class Client:
		def __init__(self, timeout):
			self.timeout = timeout
		async def __aenter__(self):
			return self
		async def __aexit__(self, *args):
			return False
		async def post(self, endpoint, headers, json):
			assert headers["Authorization"] == "Bearer k"
			return Response()
	monkeypatch.setitem(sys.modules, "httpx", types.SimpleNamespace(AsyncClient=Client))
	results = [
		RetrievalResult(id="a", text="alpha", score=0.1, retrieval="bm25"),
		RetrievalResult(id="b", text="beta", score=0.2, retrieval="bm25"),
	]
	reranked = await ExternalReranker("http://rerank", api_key="k").rerank("q", results, 2)
	assert [item.id for item in reranked] == ["b", "a"]

	assert _parse_rerank_scores([0.1, 0.2], 2) == [(0, 0.1), (1, 0.2)]
	assert _parse_rerank_scores({"scores": [1]}, 2) == [(0, 1.0)]
	assert _parse_rerank_scores("bad", 2) == []
	assert _parse_rerank_scores([{"id": "bad", "score": "x"}, {"id": 0, "relevance": 0.3}], 2) == [(0, 0.3)]
	assert _parse_llm_scores("```json\n[{\"index\":0,\"score\":0.7}]\n```", 1) == [(0, 0.7)]
	assert _parse_llm_scores("bad", 1) == []
	assert _parse_query_array("```json\n[\"a\", \"\"]\n```") == ["a"]
	assert _parse_query_array("{}") == []
	assert _dedupe_queries([" A ", "a", "", "B"], 2) == ["A", "B"]


@pytest.mark.asyncio
async def test_query_rewriters_and_knowledge_plugin_helpers(tmp_path):
	assert await NoopQueryRewriter().rewrite("") == []
	assert await LLMQueryRewriter(None).rewrite("query", max_queries=1) == ["query"]
	assert await LLMQueryRewriter(FakeLLM("not-json")).rewrite("query", max_queries=3) == ["query"]
	assert _resource_name({"resource": "r"}, "d") == "r"
	assert _resource_name(12, "d") == "d"
	base = _filter_from_config(KnowledgeFilter(metadata={"a": 1}), namespace="n")
	assert base.namespace == "n"
	assert _filter_from_config("bad", namespace="n").namespace == "n"
	merged = _merge_filters(KnowledgeFilter(namespace="base", metadata={"a": 1}), {"metadata": {"b": 2}, "source": "s"})
	assert merged.namespace == "base" and merged.metadata == {"a": 1, "b": 2}
	assert _tool_filters({"namespace": "n", "allowed_acl_tags": ["u"], "filters": {"source": "s"}}).allowed_acl_tags == ["u"]

	doc = tmp_path / "doc.md"
	doc.write_text("alpha plugin search", encoding="utf-8")
	plugin = KnowledgePlugin()
	plugin.initialize({"sources": ["doc.md"], "namespace": "n", "include_trace": True}, PluginContext(workspace=str(tmp_path)))
	assert plugin.get_tools()[0].name == "knowledge_search"
	assert plugin.inject_context(None, "alpha").startswith("[相关知识]")
	assert (await plugin._tool_knowledge_search({"query": ""}, {})).is_error
	result = await plugin._tool_knowledge_search({"query": "alpha", "include_trace": True, "filters": {"namespace": "n"}}, {})
	assert result.content["results"]
	assert result.content["trace"]["query"] == "alpha"


def test_local_file_ingestion_pipeline_builds_stable_documents(tmp_path):
	doc = tmp_path / "guide.md"
	doc.write_text("# Guide\n\nSemantic search setup.", encoding="utf-8")
	pipeline = LocalFileIngestionPipeline(workspace=str(tmp_path), namespace="tenant-a")

	result = pipeline.ingest(["guide.md"])

	assert not result.errors
	assert len(result.documents) == 1
	assert result.documents[0].metadata["namespace"] == "tenant-a"
	assert result.documents[0].metadata["heading_path"] == "Guide"


def test_local_file_ingestion_pipeline_directory_errors_and_parser_failures(tmp_path):
	class ExplodingParser:
		def supports(self, path):
			return path.endswith(".boom")

		def parse(self, path):
			raise RuntimeError("cannot parse")

	class EmptyParser:
		def supports(self, path):
			return path.endswith(".empty")

		def parse(self, path):
			return SourceDocument(id=path, text="   ", source=path)

	(tmp_path / "a.txt").write_text("hello world", encoding="utf-8")
	(tmp_path / "skip.bin").write_text("ignored", encoding="utf-8")
	(tmp_path / "bad.boom").write_text("bad", encoding="utf-8")
	(tmp_path / "blank.empty").write_text("", encoding="utf-8")
	pipeline = LocalFileIngestionPipeline(
		workspace=str(tmp_path),
		namespace="ns",
		default_metadata={"tenant": "t"},
		parsers=[ExplodingParser(), EmptyParser(), TextDocumentParser()],
	)

	result = pipeline.ingest([".", "../outside"])

	assert [doc.metadata["tenant"] for doc in result.documents] == ["t"]
	assert result.sources == [str(tmp_path / "a.txt")]
	assert any("cannot parse" in error for error in result.errors)
	assert any("outside workspace" in error for error in result.errors)


def test_text_and_pdf_parsers(tmp_path, monkeypatch):
	text_path = tmp_path / "doc.md"
	text_path.write_text("content", encoding="utf-8")
	text_parser = TextDocumentParser()
	assert text_parser.supports(str(text_path))
	parsed = text_parser.parse(str(text_path))
	assert parsed.text == "content"
	assert parsed.metadata["title"] == "doc.md"

	pdf = PdfDocumentParser()
	assert pdf.supports("x.pdf")
	monkeypatch.setitem(__import__("sys").modules, "fitz", None)
	assert pdf.parse("x.pdf") is None
