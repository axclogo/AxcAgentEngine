import pytest

from axc_agent_engine.plugins.builtin.knowledge.support import (
	CascadeReranker,
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
from axc_agent_engine.plugins.builtin.knowledge.support.retrieval import _hash_embedding


def test_semantic_chunker_preserves_heading_context():
	chunker = SemanticChunker(max_chunk_size=80, chunk_overlap=10)
	chunks = chunker.chunk_document("# API\n\nThis endpoint creates users. It validates input.", source="doc.md")
	assert chunks
	assert chunks[0].heading_path == "API"
	assert chunks[0].content_with_context.startswith("[API]")


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


def test_local_file_ingestion_pipeline_builds_stable_documents(tmp_path):
	doc = tmp_path / "guide.md"
	doc.write_text("# Guide\n\nSemantic search setup.", encoding="utf-8")
	pipeline = LocalFileIngestionPipeline(workspace=str(tmp_path), namespace="tenant-a")

	result = pipeline.ingest(["guide.md"])

	assert not result.errors
	assert len(result.documents) == 1
	assert result.documents[0].metadata["namespace"] == "tenant-a"
	assert result.documents[0].metadata["heading_path"] == "Guide"
