"""Storage-neutral hybrid retrieval.
中文：此文档说明相关引擎组件的行为。"""
from __future__ import annotations

import math
import re
import hashlib
import json
import logging
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class KnowledgeDocument:
	"""A searchable document chunk.
中文：此文档说明相关引擎组件的行为。"""
	id: str
	text: str
	source: str = ""
	metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class KnowledgeFilter:
	"""Storage-neutral retrieval filter.

	Enterprise ACL systems should translate their permission model into metadata
	filters before calling the engine; the engine does not own user/org policy.
	
中文：此文档说明相关引擎组件的行为。"""

	namespace: str = ""
	metadata: dict[str, Any] = field(default_factory=dict)
	source: str = ""
	source_prefix: str = ""
	allowed_acl_tags: list[str] = field(default_factory=list)

	def matches(self, document: KnowledgeDocument) -> bool:
		metadata = document.metadata or {}
		if self.namespace and str(metadata.get("namespace", "")) != self.namespace:
			return False
		if self.source and document.source != self.source and metadata.get("source") != self.source:
			return False
		if self.source_prefix and not str(document.source or metadata.get("source", "")).startswith(self.source_prefix):
			return False
		for key, expected in self.metadata.items():
			actual = metadata.get(key)
			if isinstance(expected, list):
				if actual not in expected and not (isinstance(actual, list) and set(actual) & set(expected)):
					return False
			elif actual != expected:
				return False
		if self.allowed_acl_tags:
			tags = metadata.get("acl_tags", [])
			if isinstance(tags, str):
				tags = [tags]
			if tags and not set(tags) & set(self.allowed_acl_tags):
				return False
		return True


@dataclass(frozen=True)
class KnowledgeSearchRequest:
	"""Structured retrieval request used by stores, retrievers, and tools.
中文：此文档说明相关引擎组件的行为。"""

	query: str
	top_k: int = 5
	candidate_k: int = 30
	filters: KnowledgeFilter | None = None
	min_score: float = 0.0
	include_trace: bool = False


@dataclass(frozen=True)
class RetrievalTrace:
	"""Debug metadata for retrieval observability.
中文：此文档说明相关引擎组件的行为。"""

	query: str
	rewritten_queries: list[str] = field(default_factory=list)
	candidate_count: int = 0
	returned_count: int = 0
	filtered: bool = False
	reranked: bool = False

	def to_dict(self) -> dict[str, Any]:
		return {
			"query": self.query,
			"rewritten_queries": list(self.rewritten_queries),
			"candidate_count": self.candidate_count,
			"returned_count": self.returned_count,
			"filtered": self.filtered,
			"reranked": self.reranked,
		}


@dataclass(frozen=True)
class KnowledgeSearchResponse:
	"""Structured retrieval response with optional trace.
中文：此文档说明相关引擎组件的行为。"""

	results: list["RetrievalResult"]
	trace: RetrievalTrace | None = None

	def to_dict(self) -> dict[str, Any]:
		data = {"results": [result.to_dict() for result in self.results]}
		if self.trace:
			data["trace"] = self.trace.to_dict()
		return data


@dataclass(frozen=True)
class RetrievalResult:
	"""One retrieval hit.
中文：此文档说明相关引擎组件的行为。"""
	id: str
	text: str
	score: float
	retrieval: str
	source: str = ""
	metadata: dict[str, Any] = field(default_factory=dict)
	citation: dict[str, Any] = field(default_factory=dict)
	highlights: list[str] = field(default_factory=list)

	def to_dict(self) -> dict[str, Any]:
		data = {
			"id": self.id,
			"text": self.text,
			"score": round(self.score, 4),
			"retrieval": self.retrieval,
			"source": self.source,
			"metadata": dict(self.metadata),
		}
		if self.citation:
			data["citation"] = dict(self.citation)
		if self.highlights:
			data["highlights"] = list(self.highlights)
		if "chunk_id" in self.metadata:
			data["chunk_id"] = self.metadata["chunk_id"]
		return data


@runtime_checkable
class EmbeddingClient(Protocol):
	"""Embeds texts for vector retrieval.
中文：此文档说明相关引擎组件的行为。"""
	async def embed(self, texts: list[str]) -> list[list[float]]: ...


@runtime_checkable
class Reranker(Protocol):
	"""Reranks retrieval candidates.
中文：此文档说明相关引擎组件的行为。"""
	async def rerank(self, query: str, results: list[RetrievalResult], top_k: int) -> list[RetrievalResult]: ...


@runtime_checkable
class QueryRewriter(Protocol):
	"""Expands or rewrites a retrieval query before search.
中文：此文档说明相关引擎组件的行为。"""
	async def rewrite(self, query: str, max_queries: int = 4) -> list[str]: ...


@runtime_checkable
class KnowledgeIndexStore(Protocol):
	"""Storage-neutral knowledge index API.
中文：此文档说明相关引擎组件的行为。"""
	async def add_documents(self, documents: list[KnowledgeDocument], embeddings: list[list[float]] | None = None) -> None: ...
	async def upsert_documents(self, documents: list[KnowledgeDocument], embeddings: list[list[float]] | None = None) -> None: ...
	async def delete_documents(self, ids: list[str]) -> int: ...
	async def delete_by_filter(self, filters: KnowledgeFilter | dict[str, Any]) -> int: ...
	async def list_documents(self) -> list[KnowledgeDocument]: ...
	async def bm25_search(self, query: str, top_k: int = 30, filters: KnowledgeFilter | dict[str, Any] | None = None) -> list[RetrievalResult]: ...
	async def vector_search(self, query: str, top_k: int = 30, filters: KnowledgeFilter | dict[str, Any] | None = None) -> list[RetrievalResult]: ...
	async def search(self, request: KnowledgeSearchRequest | str, top_k: int = 5, candidate_k: int = 30) -> list[RetrievalResult]: ...
	async def search_with_trace(self, request: KnowledgeSearchRequest | str, top_k: int = 5, candidate_k: int = 30) -> KnowledgeSearchResponse: ...


class HashEmbeddingClient:
	"""Dependency-free deterministic embedding fallback for tests and local indexes.
中文：此文档说明相关引擎组件的行为。"""

	def __init__(self, dimensions: int = 256) -> None:
		self.dimensions = max(8, dimensions)

	async def embed(self, texts: list[str]) -> list[list[float]]:
		return [_hash_embedding(text, self.dimensions) for text in texts]


class OpenAICompatibleEmbeddingClient:
	"""Small OpenAI-compatible /embeddings client used when configured by plugins.
中文：此文档说明相关引擎组件的行为。"""

	def __init__(self, base_url: str, model: str, api_key: str = "", timeout: int = 30) -> None:
		if not base_url:
			raise ValueError("base_url is required")
		if not model:
			raise ValueError("model is required")
		self.base_url = base_url.rstrip("/")
		self.model = model
		self.api_key = api_key
		self.timeout = timeout

	async def embed(self, texts: list[str]) -> list[list[float]]:
		if not texts:
			return []
		import httpx
		headers = {"Content-Type": "application/json"}
		if self.api_key:
			headers["Authorization"] = f"Bearer {self.api_key}"
		async with httpx.AsyncClient(timeout=self.timeout) as client:
			response = await client.post(
				f"{self.base_url}/embeddings",
				headers=headers,
				json={"model": self.model, "input": texts},
			)
			response.raise_for_status()
			data = response.json()
			return [item["embedding"] for item in data.get("data", [])]


class ScoreReranker:
	"""Lexical reranker that keeps the engine usable without a model dependency.
中文：此文档说明相关引擎组件的行为。"""

	async def rerank(self, query: str, results: list[RetrievalResult], top_k: int) -> list[RetrievalResult]:
		query_terms = set(tokenize(query))
		reranked: list[RetrievalResult] = []
		for result in results:
			text_terms = set(tokenize(result.text))
			overlap = len(query_terms & text_terms) / max(1, len(query_terms))
			score = result.score + overlap
			reranked.append(RetrievalResult(
				id=result.id,
				text=result.text,
				score=score,
				retrieval=f"{result.retrieval}+rerank",
				source=result.source,
				metadata=result.metadata,
				citation=result.citation,
				highlights=result.highlights,
			))
		reranked.sort(key=lambda item: item.score, reverse=True)
		return reranked[:top_k]


class ExternalReranker:
	"""OpenAI-style HTTP reranker endpoint adapter.

	The endpoint is expected to accept `{model, query, documents}` and return
	either `results: [{index, score}]`, `data: [{index, score}]`, or a score list.
	
中文：此文档说明相关引擎组件的行为。"""

	def __init__(self, endpoint: str, model: str = "", api_key: str = "", timeout: float = 30.0) -> None:
		if not endpoint:
			raise ValueError("endpoint is required")
		self.endpoint = endpoint
		self.model = model
		self.api_key = api_key
		self.timeout = timeout

	async def rerank(self, query: str, results: list[RetrievalResult], top_k: int) -> list[RetrievalResult]:
		if not results:
			return []
		import httpx
		headers = {"Content-Type": "application/json"}
		if self.api_key:
			headers["Authorization"] = f"Bearer {self.api_key}"
		payload = {
			"query": query,
			"documents": [result.text for result in results],
		}
		if self.model:
			payload["model"] = self.model
		async with httpx.AsyncClient(timeout=self.timeout) as client:
			response = await client.post(self.endpoint, headers=headers, json=payload)
			response.raise_for_status()
		scores = _parse_rerank_scores(response.json(), len(results))
		if not scores:
			return []
		scored = []
		for idx, score in scores:
			if 0 <= idx < len(results):
				result = results[idx]
				scored.append(RetrievalResult(
					id=result.id,
					text=result.text,
					score=float(score),
					retrieval=f"{result.retrieval}+model_rerank",
					source=result.source,
					metadata=result.metadata,
					citation=result.citation,
					highlights=result.highlights,
				))
		scored.sort(key=lambda item: item.score, reverse=True)
		return scored[:top_k]


class LLMReranker:
	"""LLM scoring fallback for retrieval candidates.
中文：此文档说明相关引擎组件的行为。"""

	def __init__(self, utility_llm: Any, max_chars_per_doc: int = 700) -> None:
		self.utility_llm = utility_llm
		self.max_chars_per_doc = max_chars_per_doc

	async def rerank(self, query: str, results: list[RetrievalResult], top_k: int) -> list[RetrievalResult]:
		if not self.utility_llm or not results:
			return []
		prompt = _rerank_prompt(query, results, self.max_chars_per_doc)
		content = await self.utility_llm.ask(prompt)
		scores = _parse_llm_scores(content, len(results))
		if not scores:
			return []
		scored = []
		for idx, score in scores:
			if 0 <= idx < len(results):
				result = results[idx]
				scored.append(RetrievalResult(
					id=result.id,
					text=result.text,
					score=float(score),
					retrieval=f"{result.retrieval}+llm_rerank",
					source=result.source,
					metadata=result.metadata,
					citation=result.citation,
					highlights=result.highlights,
				))
		scored.sort(key=lambda item: item.score, reverse=True)
		return scored[:top_k]


class CascadeReranker:
	"""Try rerankers in order and fall back on failure or empty output.
中文：此文档说明相关引擎组件的行为。"""

	def __init__(self, rerankers: list[Reranker]) -> None:
		self.rerankers = rerankers

	async def rerank(self, query: str, results: list[RetrievalResult], top_k: int) -> list[RetrievalResult]:
		for reranker in self.rerankers:
			try:
				reranked = await reranker.rerank(query, results, top_k)
				if reranked:
					return reranked[:top_k]
			except Exception as exc:
				logger.warning("[knowledge] reranker %s failed: %s", type(reranker).__name__, exc)
		return results[:top_k]


class NoopQueryRewriter:
	"""Default query rewriter that preserves the original query.
中文：此文档说明相关引擎组件的行为。"""

	async def rewrite(self, query: str, max_queries: int = 4) -> list[str]:
		return [query] if query else []


class LLMQueryRewriter:
	"""LLM-based query expansion for better recall.
中文：此文档说明相关引擎组件的行为。"""

	def __init__(self, utility_llm: Any) -> None:
		self.utility_llm = utility_llm

	async def rewrite(self, query: str, max_queries: int = 4) -> list[str]:
		if not query:
			return []
		if not self.utility_llm or max_queries <= 1:
			return [query]
		prompt = (
			"请将检索问题改写为简洁的搜索查询。"
			"只返回字符串组成的 JSON 数组。\n"
			f"最多查询数：{max_queries}\n"
			f"原始问题：{query}"
		)
		try:
			content = await self.utility_llm.ask(prompt)
			items = _parse_query_array(content)
		except Exception as exc:
			logger.warning("[knowledge] query rewrite failed: %s", exc)
			items = []
		return _dedupe_queries([query, *items], max_queries)


class InMemoryKnowledgeIndexStore:
	"""No-database knowledge index with BM25, optional embeddings, and hybrid search.
中文：此文档说明相关引擎组件的行为。"""

	def __init__(
		self,
		embedding_client: EmbeddingClient | None = None,
		reranker: Reranker | None = None,
		query_rewriter: QueryRewriter | None = None,
	) -> None:
		self.embedding_client = embedding_client
		self.reranker = reranker
		self.query_rewriter = query_rewriter
		self._documents: dict[str, KnowledgeDocument] = {}
		self._embeddings: dict[str, list[float]] = {}
		self._bm25 = BM25Index()

	def set_documents(self, documents: list[KnowledgeDocument], embeddings: list[list[float]] | None = None) -> None:
		"""Replace indexed documents without requiring async embedding calls.
中文：此文档说明相关引擎组件的行为。"""
		self._documents = {doc.id: doc for doc in documents}
		self._embeddings = {}
		if embeddings:
			for doc, embedding in zip(documents, embeddings):
				self._embeddings[doc.id] = list(embedding)
		self._rebuild()

	def set_document_embedding(self, doc_id: str, embedding: list[float]) -> None:
		"""Set or replace one document embedding.
中文：此文档说明相关引擎组件的行为。"""
		if doc_id in self._documents:
			self._embeddings[doc_id] = list(embedding)

	async def add_documents(self, documents: list[KnowledgeDocument], embeddings: list[list[float]] | None = None) -> None:
		await self.upsert_documents(documents, embeddings=embeddings)

	async def upsert_documents(self, documents: list[KnowledgeDocument], embeddings: list[list[float]] | None = None) -> None:
		for doc in documents:
			self._documents[doc.id] = doc
		if embeddings:
			for doc, embedding in zip(documents, embeddings):
				self._embeddings[doc.id] = list(embedding)
		elif self.embedding_client and documents:
			vectors = await self.embedding_client.embed([doc.text for doc in documents])
			for doc, vector in zip(documents, vectors):
				self._embeddings[doc.id] = list(vector)
		self._rebuild()

	async def delete_documents(self, ids: list[str]) -> int:
		removed = 0
		for doc_id in ids:
			if doc_id in self._documents:
				removed += 1
			self._documents.pop(doc_id, None)
			self._embeddings.pop(doc_id, None)
		if removed:
			self._rebuild()
		return removed

	async def delete_by_filter(self, filters: KnowledgeFilter | dict[str, Any]) -> int:
		knowledge_filter = normalize_filter(filters)
		ids = [doc_id for doc_id, doc in self._documents.items() if knowledge_filter.matches(doc)]
		return await self.delete_documents(ids)

	async def list_documents(self) -> list[KnowledgeDocument]:
		return list(self._documents.values())

	async def bm25_search(
		self,
		query: str,
		top_k: int = 30,
		filters: KnowledgeFilter | dict[str, Any] | None = None,
	) -> list[RetrievalResult]:
		return self._bm25.search(query, top_k=top_k, filters=filters)

	async def vector_search(
		self,
		query: str,
		top_k: int = 30,
		filters: KnowledgeFilter | dict[str, Any] | None = None,
	) -> list[RetrievalResult]:
		if not self._documents:
			return []
		knowledge_filter = normalize_filter(filters)
		query_vector: list[float] = []
		if self.embedding_client:
			vectors = await self.embedding_client.embed([query])
			query_vector = vectors[0] if vectors else []
		elif self._embeddings:
			query_vector = _hash_embedding(query, len(next(iter(self._embeddings.values()))))
		if not query_vector:
			return []
		scored: list[tuple[float, KnowledgeDocument]] = []
		for doc_id, embedding in self._embeddings.items():
			doc = self._documents.get(doc_id)
			if not doc or not knowledge_filter.matches(doc):
				continue
			score = _cosine(query_vector, embedding)
			if score > 0:
				scored.append((score, doc))
		scored.sort(key=lambda item: item[0], reverse=True)
		return [
			_result_from_doc(doc, score, "vector", query)
			for score, doc in scored[:top_k]
		]

	async def search(self, request: KnowledgeSearchRequest | str, top_k: int = 5, candidate_k: int = 30) -> list[RetrievalResult]:
		return (await self.search_with_trace(request, top_k=top_k, candidate_k=candidate_k)).results

	async def search_with_trace(
		self,
		request: KnowledgeSearchRequest | str,
		top_k: int = 5,
		candidate_k: int = 30,
	) -> KnowledgeSearchResponse:
		req = normalize_search_request(request, top_k=top_k, candidate_k=candidate_k)
		retriever = HybridRetriever(
			await self.list_documents(),
			vector_search=self.vector_search if self._embeddings or self.embedding_client else None,
			reranker=self.reranker.rerank if self.reranker else None,
			query_rewriter=self.query_rewriter.rewrite if self.query_rewriter else None,
		)
		return await retriever.search_with_trace(req)

	def _rebuild(self) -> None:
		self._bm25.build(list(self._documents.values()))


class BM25Index:
	"""Small dependency-free BM25 index.
中文：此文档说明相关引擎组件的行为。"""

	def __init__(self, documents: list[KnowledgeDocument] | None = None) -> None:
		self.documents: list[KnowledgeDocument] = []
		self._doc_terms: list[Counter] = []
		self._doc_freq: Counter = Counter()
		self._avg_dl = 1.0
		if documents:
			self.build(documents)

	def build(self, documents: list[KnowledgeDocument]) -> None:
		self.documents = list(documents)
		self._doc_terms = []
		self._doc_freq = Counter()
		total_dl = 0
		for doc in self.documents:
			terms = tokenize(doc.text)
			counter = Counter(terms)
			self._doc_terms.append(counter)
			total_dl += len(terms)
			for term in set(terms):
				self._doc_freq[term] += 1
		self._avg_dl = total_dl / len(self.documents) if self.documents else 1.0

	def search(
		self,
		query: str,
		top_k: int = 30,
		filters: KnowledgeFilter | dict[str, Any] | None = None,
	) -> list[RetrievalResult]:
		query_terms = tokenize(query)
		if not query_terms or not self.documents:
			return []
		knowledge_filter = normalize_filter(filters)
		n_docs = len(self.documents)
		k1, b = 1.5, 0.75
		scored: list[tuple[float, int]] = []
		for idx, counter in enumerate(self._doc_terms):
			if not knowledge_filter.matches(self.documents[idx]):
				continue
			score = 0.0
			dl = sum(counter.values()) or 1
			for term in query_terms:
				df = self._doc_freq.get(term, 0)
				if not df:
					continue
				idf = math.log((n_docs - df + 0.5) / (df + 0.5) + 1.0)
				tf = counter.get(term, 0)
				score += idf * ((tf * (k1 + 1)) / (tf + k1 * (1 - b + b * dl / self._avg_dl)))
			if score > 0:
				scored.append((score, idx))
		scored.sort(key=lambda item: item[0], reverse=True)
		results = []
		for score, idx in scored[:top_k]:
			doc = self.documents[idx]
			results.append(_result_from_doc(doc, score, "bm25", query))
		return results


class HybridRetriever:
	"""BM25 + vector + optional rerank retrieval pipeline.
中文：此文档说明相关引擎组件的行为。"""

	def __init__(
		self,
		documents: list[KnowledgeDocument],
		vector_search: Callable[[str, int, KnowledgeFilter | None], Awaitable[list[RetrievalResult]]] | None = None,
		reranker: Callable[[str, list[RetrievalResult], int], Awaitable[list[RetrievalResult]]] | None = None,
		query_rewriter: Callable[[str, int], Awaitable[list[str]]] | None = None,
	) -> None:
		self.documents = list(documents)
		self.bm25 = BM25Index(documents)
		self.vector_search = vector_search
		self.reranker = reranker
		self.query_rewriter = query_rewriter

	async def search(self, query: KnowledgeSearchRequest | str, top_k: int = 5, candidate_k: int = 30) -> list[RetrievalResult]:
		return (await self.search_with_trace(query, top_k=top_k, candidate_k=candidate_k)).results

	async def search_with_trace(
		self,
		query: KnowledgeSearchRequest | str,
		top_k: int = 5,
		candidate_k: int = 30,
	) -> KnowledgeSearchResponse:
		request = normalize_search_request(query, top_k=top_k, candidate_k=candidate_k)
		queries = [request.query]
		if self.query_rewriter:
			try:
				rewritten = await self.query_rewriter(request.query, 4)
				queries = _dedupe_queries([request.query, *(rewritten or [])], 4)
			except Exception as exc:
				logger.warning("[knowledge] query rewriter failed: %s", exc)
		merged_inputs: list[list[RetrievalResult]] = []
		for current_query in queries:
			bm25 = self.bm25.search(current_query, top_k=request.candidate_k, filters=request.filters)
			vector: list[RetrievalResult] = []
			if self.vector_search:
				vector = await self.vector_search(current_query, request.candidate_k, request.filters)
			merged_inputs.append(rrf_merge(bm25, vector, top_k=max(request.candidate_k, request.top_k)))
		merged = rrf_merge(*merged_inputs, top_k=max(request.candidate_k, request.top_k))
		candidate_count = len(merged)
		merged = [result for result in merged if result.score >= request.min_score]
		reranked = False
		if self.reranker and merged:
			reranked_results = await self.reranker(request.query, merged, request.top_k)
			if reranked_results:
				reranked = True
				merged = reranked_results
		results = merged[:request.top_k]
		trace = RetrievalTrace(
			query=request.query,
			rewritten_queries=queries,
			candidate_count=candidate_count,
			returned_count=len(results),
			filtered=request.filters != KnowledgeFilter(),
			reranked=reranked,
		) if request.include_trace else None
		return KnowledgeSearchResponse(results=results, trace=trace)


def rrf_merge(*ranked_lists: list[RetrievalResult], top_k: int = 10, k: int = 60) -> list[RetrievalResult]:
	"""Reciprocal rank fusion.
中文：此文档说明相关引擎组件的行为。"""
	scores: dict[str, float] = {}
	items: dict[str, RetrievalResult] = {}
	retrievals: dict[str, set[str]] = {}
	for ranked in ranked_lists:
		for rank, item in enumerate(ranked):
			scores[item.id] = scores.get(item.id, 0.0) + 1.0 / (k + rank + 1)
			items[item.id] = item
			retrievals.setdefault(item.id, set()).add(item.retrieval)
	results = []
	for item_id, score in sorted(scores.items(), key=lambda pair: pair[1], reverse=True)[:top_k]:
		item = items[item_id]
		labels = retrievals.get(item_id, set())
		retrieval = "hybrid" if len(labels) > 1 or "vector" in labels else item.retrieval
		results.append(RetrievalResult(
			id=item.id,
			text=item.text,
			source=item.source,
			score=score,
			retrieval=retrieval,
			metadata=item.metadata,
			citation=item.citation,
			highlights=item.highlights,
		))
	return results


def normalize_filter(filters: KnowledgeFilter | dict[str, Any] | None) -> KnowledgeFilter:
	if filters is None:
		return KnowledgeFilter()
	if isinstance(filters, KnowledgeFilter):
		return filters
	metadata = dict(filters.get("metadata", {})) if isinstance(filters.get("metadata"), dict) else {}
	for key, value in filters.items():
		if key not in {"namespace", "source", "source_prefix", "acl_tags", "allowed_acl_tags", "metadata"}:
			metadata[key] = value
	return KnowledgeFilter(
		namespace=str(filters.get("namespace") or ""),
		source=str(filters.get("source") or ""),
		source_prefix=str(filters.get("source_prefix") or ""),
		allowed_acl_tags=[str(item) for item in filters.get("allowed_acl_tags", filters.get("acl_tags", []))],
		metadata=metadata,
	)


def normalize_search_request(
	request: KnowledgeSearchRequest | str,
	top_k: int = 5,
	candidate_k: int = 30,
) -> KnowledgeSearchRequest:
	if isinstance(request, KnowledgeSearchRequest):
		return request
	return KnowledgeSearchRequest(query=str(request), top_k=top_k, candidate_k=candidate_k)


def _result_from_doc(doc: KnowledgeDocument, score: float, retrieval: str, query: str) -> RetrievalResult:
	return RetrievalResult(
		id=doc.id,
		text=doc.text,
		source=doc.source,
		score=score,
		retrieval=retrieval,
		metadata=doc.metadata,
		citation=_citation_for(doc),
		highlights=_highlights(query, doc.text),
	)


def _citation_for(doc: KnowledgeDocument) -> dict[str, Any]:
	metadata = doc.metadata or {}
	citation = {
		"source": doc.source or metadata.get("source", ""),
		"chunk_id": metadata.get("chunk_id", doc.id),
	}
	for key in ("document_id", "title", "url", "page", "start_line", "end_line", "heading_path", "version", "updated_at"):
		if key in metadata and metadata[key] not in ("", None):
			citation[key] = metadata[key]
	return citation


def _highlights(query: str, text: str, max_items: int = 3, window: int = 80) -> list[str]:
	terms = [term for term in tokenize(query) if len(term) > 1]
	if not terms:
		return []
	text_lower = text.lower()
	highlights: list[str] = []
	for term in terms:
		idx = text_lower.find(term.lower())
		if idx < 0:
			continue
		start = max(0, idx - window // 2)
		end = min(len(text), idx + len(term) + window // 2)
		snippet = text[start:end].strip()
		if snippet and snippet not in highlights:
			highlights.append(snippet)
		if len(highlights) >= max_items:
			break
	return highlights


def tokenize(text: str) -> list[str]:
	"""Tokenize Chinese, English, numbers, and identifiers.
中文：此文档说明相关引擎组件的行为。"""
	text = text.lower()
	tokens: list[str] = []
	tokens.extend(re.findall(r"[a-z_][a-z0-9_]*", text))
	tokens.extend(re.findall(r"\d+", text))
	for word in re.findall(r"[\u4e00-\u9fff]+", text):
		tokens.extend(list(word))
		tokens.extend(word[i:i + 2] for i in range(len(word) - 1))
	return [token for token in tokens if token]


def _parse_rerank_scores(data: Any, size: int) -> list[tuple[int, float]]:
	if isinstance(data, dict):
		raw = data.get("results", data.get("data", data.get("scores", [])))
	else:
		raw = data
	if isinstance(raw, list) and all(isinstance(item, (int, float)) for item in raw):
		return [(idx, float(score)) for idx, score in enumerate(raw[:size])]
	if not isinstance(raw, list):
		return []
	scores: list[tuple[int, float]] = []
	for rank, item in enumerate(raw):
		if not isinstance(item, dict):
			continue
		idx = item.get("index", item.get("document_index", item.get("id", rank)))
		score = item.get("score", item.get("relevance_score", item.get("relevance", 0.0)))
		try:
			scores.append((int(idx), float(score)))
		except (TypeError, ValueError):
			continue
	return scores


def _rerank_prompt(query: str, results: list[RetrievalResult], max_chars: int) -> str:
	docs = []
	for idx, result in enumerate(results):
		text = result.text.replace("\n", " ")[:max_chars]
		docs.append(f"{idx}. {text}")
	return (
		"请按 0 到 1 给每个文档与问题的相关性打分。"
		"只返回 JSON 数组，每项格式为 {\"index\": number, \"score\": number}。\n"
		f"问题：{query}\n文档：\n" + "\n".join(docs)
	)


def _parse_llm_scores(content: str, size: int) -> list[tuple[int, float]]:
	if not content:
		return []
	text = content.strip()
	if text.startswith("```"):
		lines = text.splitlines()
		text = "\n".join(lines[1:-1] if lines and lines[-1].strip() == "```" else lines[1:])
	start = text.find("[")
	end = text.rfind("]")
	if start >= 0 and end > start:
		text = text[start:end + 1]
	try:
		return _parse_rerank_scores(json.loads(text), size)
	except (json.JSONDecodeError, TypeError):
		return []


def _parse_query_array(content: str) -> list[str]:
	if not content:
		return []
	text = content.strip()
	if text.startswith("```"):
		lines = text.splitlines()
		text = "\n".join(lines[1:-1] if lines and lines[-1].strip() == "```" else lines[1:])
	start = text.find("[")
	end = text.rfind("]")
	if start >= 0 and end > start:
		text = text[start:end + 1]
	try:
		raw = json.loads(text)
	except (json.JSONDecodeError, TypeError):
		return []
	if not isinstance(raw, list):
		return []
	return [str(item).strip() for item in raw if str(item).strip()]


def _dedupe_queries(queries: list[str], max_queries: int) -> list[str]:
	seen: set[str] = set()
	output: list[str] = []
	for query in queries:
		cleaned = " ".join(str(query).strip().split())
		key = cleaned.lower()
		if not cleaned or key in seen:
			continue
		seen.add(key)
		output.append(cleaned)
		if len(output) >= max(1, max_queries):
			break
	return output


def _hash_embedding(text: str, dimensions: int) -> list[float]:
	vector = [0.0] * dimensions
	for token in tokenize(text):
		digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
		idx = int.from_bytes(digest, "big") % dimensions
		vector[idx] += 1.0
	norm = math.sqrt(sum(value * value for value in vector))
	return [value / norm for value in vector] if norm else vector


def _cosine(a: list[float], b: list[float]) -> float:
	if not a or not b or len(a) != len(b):
		return 0.0
	dot = sum(x * y for x, y in zip(a, b))
	norm_a = math.sqrt(sum(x * x for x in a))
	norm_b = math.sqrt(sum(y * y for y in b))
	if norm_a <= 0 or norm_b <= 0:
		return 0.0
	return dot / (norm_a * norm_b)
