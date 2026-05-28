"""Knowledge plugin with host-injected retrieval resources.
中文：知识库插件使用宿主注入资源执行混合检索。

Official plugin boundary:
- retrieval strategy lives here;
- external clients, keys, endpoints, and stores are injected by the host;
- resource failures are surfaced as tool errors.
"""
from __future__ import annotations

import inspect
import logging
import math
import re
from collections import Counter
from typing import TYPE_CHECKING, Any

from axc_agent_engine.core.schema import ToolDefinition
from axc_agent_engine.plugins.base import BasePlugin
from axc_agent_engine.plugins.builtin.config_schemas import KNOWLEDGE_CONFIG_SCHEMA
from axc_agent_engine.plugins.builtin.knowledge.support import (
	HybridRetriever,
	InMemoryKnowledgeIndexStore,
	KnowledgeDocument,
	KnowledgeFilter,
	KnowledgeSearchRequest,
	KnowledgeSearchResponse,
	LLMQueryRewriter,
	LLMReranker,
	LocalFileIngestionPipeline,
	NoopQueryRewriter,
	RetrievalResult,
	RetrievalTrace,
	ScoreReranker,
	SemanticChunker,
	rrf_merge,
)

if TYPE_CHECKING:
	from axc_agent_engine.core.context import ExecutionContext
	from axc_agent_engine.plugins import PluginContext

logger = logging.getLogger(__name__)

_INDEX_RESOURCE = "knowledge.index"
_DOCUMENTS_RESOURCE = "knowledge.documents"
_EMBEDDING_RESOURCE = "knowledge.embedding"
_VECTOR_STORE_RESOURCE = "knowledge.vector_store"
_RERANKER_RESOURCE = "knowledge.reranker"


class KnowledgePlugin(BasePlugin):
	name = "knowledge"
	display_name = "知识库"
	priority = 20
	version = "2.0.0"
	config_schema = KNOWLEDGE_CONFIG_SCHEMA

	def initialize(self, config: dict, plugin_ctx: "PluginContext") -> None:
		super().initialize(config, plugin_ctx)
		self._sources = [str(item) for item in config.get("sources", [])]
		self._chunk_size = int(config.get("chunk_size", 512))
		self._chunk_overlap = int(config.get("chunk_overlap", 50))
		self._namespace = str(config.get("namespace", ""))
		self._default_filter = _filter_from_config(config.get("filters", {}), namespace=self._namespace)
		self._default_candidate_k = int(config.get("candidate_k", 30))
		self._include_trace_default = bool(config.get("include_trace", False))
		self._metadata = dict(config.get("metadata", {}) or {})
		self._query_rewrite_config = dict(config.get("query_rewrite", {}) or {})
		self._rerank_config = dict(config.get("rerank", {}) or {})
		self._workspace = plugin_ctx.workspace or ""
		self._resources = plugin_ctx.resources
		self._mounted_index = self._resources.get(_INDEX_RESOURCE)
		self._mounted_documents = self._resources.get(_DOCUMENTS_RESOURCE)
		self._embedding = self._resources.get(_EMBEDDING_RESOURCE)
		self._vector_store = self._resources.get(_VECTOR_STORE_RESOURCE)
		self._mounted_reranker = self._resources.get(_RERANKER_RESOURCE)
		self._documents: list[KnowledgeDocument] = []
		self._chunks: list[dict[str, Any]] = []
		self._doc_terms: list[Counter] = []
		self._doc_freq: Counter = Counter()
		self._avg_dl = 1.0
		self._retriever: HybridRetriever | None = None
		self._local_index: InMemoryKnowledgeIndexStore | None = None
		self._local_index_ready = False
		self._local_index_lock = None
		self._chunker = SemanticChunker(max_chunk_size=self._chunk_size, chunk_overlap=self._chunk_overlap)
		self._ingestion_pipeline = LocalFileIngestionPipeline(
			chunker=self._chunker,
			workspace=self._workspace,
			namespace=self._namespace,
			default_metadata=self._metadata,
		)
		self._reranker = self._build_reranker()
		self._query_rewriter = self._build_query_rewriter()
		self._load_sources()
		self._build_bm25_index()

	def inject_context(self, exec_ctx: "ExecutionContext", topic: str = "") -> str:
		if not self._chunks or not topic:
			return ""
		results = self._format_bm25_results(self._bm25_search(topic, top_k=5, filters=self._default_filter), topic)
		if not results:
			return ""
		lines = ["[相关知识]"]
		for item in results:
			lines.append(f"---\n{item['text']}\n(source: {item.get('source', 'unknown')})")
		return "\n".join(lines)

	async def on_execution_start(self, exec_ctx: "ExecutionContext") -> None:
		if self._documents or self._mounted_documents:
			await self._ensure_local_index()

	def get_tools(self) -> list[ToolDefinition]:
		if not self._has_retrieval_source():
			return []
		return [
			ToolDefinition(
				name="knowledge_search",
				description="搜索知识库中的相关内容",
				parameters={
					"type": "object",
					"properties": {
						"query": {"type": "string", "description": "搜索关键词或问题"},
						"top_k": {"type": "integer", "description": "返回结果数量", "default": 5},
						"candidate_k": {"type": "integer", "description": "候选召回数量", "default": self._default_candidate_k},
						"namespace": {"type": "string", "description": "知识库命名空间"},
						"filters": {"type": "object", "description": "元数据过滤条件，如 {\"document_id\": \"...\"}"},
						"allowed_acl_tags": {"type": "array", "items": {"type": "string"}, "description": "调用方已授权的 ACL tag"},
						"min_score": {"type": "number", "description": "最低分过滤", "default": 0},
						"include_trace": {"type": "boolean", "description": "是否返回检索 trace", "default": False},
					},
					"required": ["query"],
				},
				is_read_only=True,
				capability="knowledge_read",
				risk_level="safe",
				execute=self._tool_knowledge_search,
			)
		]

	async def _tool_knowledge_search(self, args: dict, context: dict):
		from axc_agent_engine.tools.tool_output import ToolOutput

		query = str(args.get("query", "")).strip()
		if not query:
			return ToolOutput.error("query 不能为空")
		try:
			payload = await self._hybrid_search_payload(
				query,
				top_k=int(args.get("top_k", 5)),
				candidate_k=int(args.get("candidate_k", self._default_candidate_k)),
				filters=_tool_filters(args),
				min_score=float(args.get("min_score", 0.0) or 0.0),
				include_trace=bool(args.get("include_trace", self._include_trace_default)),
			)
		except Exception as exc:
			logger.exception("[knowledge] search failed")
			return ToolOutput.error(f"knowledge_search failed: {exc}")
		return ToolOutput.json_output(
			payload,
			summary=f"knowledge_search：为 '{query[:50]}' 找到 {len(payload.get('results', []))} 条结果",
		)

	async def _hybrid_search_async(
		self,
		query: str,
		top_k: int = 5,
		candidate_k: int | None = None,
		filters: KnowledgeFilter | dict | None = None,
		min_score: float = 0.0,
		include_trace: bool | None = None,
	) -> list[dict]:
		return (await self._hybrid_search_payload(
			query,
			top_k=top_k,
			candidate_k=candidate_k,
			filters=filters,
			min_score=min_score,
			include_trace=include_trace,
		)).get("results", [])

	async def _hybrid_search_payload(
		self,
		query: str,
		top_k: int = 5,
		candidate_k: int | None = None,
		filters: KnowledgeFilter | dict | None = None,
		min_score: float = 0.0,
		include_trace: bool | None = None,
	) -> dict:
		if not self._has_retrieval_source():
			raise RuntimeError("knowledge plugin has no retrieval source; configure sources or mount knowledge resources")
		if self._vector_store and not self._embedding:
			raise RuntimeError("knowledge.vector_store requires mounted knowledge.embedding")
		include_trace = self._include_trace_default if include_trace is None else include_trace
		request = KnowledgeSearchRequest(
			query=query,
			top_k=max(1, int(top_k)),
			candidate_k=max(1, int(candidate_k or self._default_candidate_k)),
			filters=_merge_filters(self._default_filter, filters),
			min_score=float(min_score or 0.0),
			include_trace=bool(include_trace),
		)
		result_sets: list[list[RetrievalResult]] = []
		trace_sources: list[dict[str, Any]] = []

		if self._mounted_index:
			response = await self._search_mounted_index(request)
			result_sets.append(response.results)
			if response.trace:
				trace_sources.append({"source": _INDEX_RESOURCE, **response.trace.to_dict()})

		if self._documents or self._mounted_documents:
			await self._ensure_local_index()
			if self._local_index:
				response = await self._local_index.search_with_trace(request)
				result_sets.append(response.results)
				if response.trace:
					trace_sources.append({"source": "local", **response.trace.to_dict()})

		if self._vector_store:
			vector_results = await self._search_vector_store(request)
			result_sets.append(vector_results)
			trace_sources.append({
				"source": _VECTOR_STORE_RESOURCE,
				"query": request.query,
				"candidate_count": len(vector_results),
				"returned_count": min(len(vector_results), request.top_k),
				"filtered": request.filters != KnowledgeFilter(),
				"reranked": False,
				"rewritten_queries": [request.query],
			})

		merged = self._merge_result_sets(result_sets, request)
		trace = None
		if request.include_trace:
			trace = {
				"query": request.query,
				"rewritten_queries": [request.query],
				"candidate_count": sum(len(items) for items in result_sets),
				"returned_count": len(merged),
				"filtered": request.filters != KnowledgeFilter(),
				"reranked": bool(self._reranker),
				"sources": trace_sources,
			}
		return {"results": [result.to_dict() for result in merged], "trace": trace}

	def _hybrid_search(self, query: str, top_k: int = 5, filters: KnowledgeFilter | dict | None = None) -> list[dict]:
		return self._format_bm25_results(self._bm25_search(query, top_k=top_k, filters=filters), query)

	def _has_retrieval_source(self) -> bool:
		return bool(self._mounted_index or self._mounted_documents or self._documents or self._vector_store)

	def _load_sources(self) -> None:
		result = self._ingestion_pipeline.ingest(self._sources)
		self._documents = list(result.documents)
		self._chunks = [
			{
				"text": doc.text,
				"source": doc.source,
				"metadata": dict(doc.metadata),
			}
			for doc in self._documents
		]
		for error in result.errors:
			logger.warning("[knowledge] %s", error)
		logger.info("[knowledge] loaded %s local document chunks", len(self._chunks))

	def _build_bm25_index(self) -> None:
		self._doc_terms = []
		self._doc_freq = Counter()
		total_dl = 0.0
		for chunk in self._chunks:
			terms = _tokenize(chunk["text"])
			counter = Counter(terms)
			self._doc_terms.append(counter)
			total_dl += len(terms)
			for term in set(terms):
				self._doc_freq[term] += 1
		self._avg_dl = total_dl / len(self._chunks) if self._chunks else 1.0
		self._retriever = HybridRetriever(self._documents)
		self._local_index_ready = False

	async def _ensure_local_index(self) -> None:
		if self._local_index_ready:
			return
		if self._local_index_lock is None:
			import asyncio
			self._local_index_lock = asyncio.Lock()
		async with self._local_index_lock:
			if self._local_index_ready:
				return
			documents = list(self._documents)
			if self._mounted_documents:
				documents.extend(await self._load_mounted_documents())
			self._local_index = InMemoryKnowledgeIndexStore(
				embedding_client=self._embedding,
				reranker=self._reranker,
				query_rewriter=self._query_rewriter,
			)
			if self._embedding:
				await self._local_index.upsert_documents(documents)
			else:
				self._local_index.set_documents(documents)
			self._local_index_ready = True

	async def _load_mounted_documents(self) -> list[KnowledgeDocument]:
		resource = self._mounted_documents
		if resource is None:
			return []
		if isinstance(resource, list | tuple):
			raw_docs = list(resource)
		else:
			list_documents = getattr(resource, "list_documents", None)
			if not callable(list_documents):
				raise RuntimeError("knowledge.documents must be a sequence or expose list_documents()")
			raw_docs = await _maybe_await(list_documents())
		return [_normalize_document(item, index) for index, item in enumerate(raw_docs or [])]

	async def _search_mounted_index(self, request: KnowledgeSearchRequest) -> KnowledgeSearchResponse:
		index = self._mounted_index
		search_with_trace = getattr(index, "search_with_trace", None)
		if callable(search_with_trace):
			raw = await _maybe_await(search_with_trace(request))
			return _normalize_response(raw, request, _INDEX_RESOURCE)
		search = getattr(index, "search", None)
		if callable(search):
			raw = await _call_search(search, request)
			return _normalize_response(raw, request, _INDEX_RESOURCE)
		raise RuntimeError("knowledge.index must expose search_with_trace(request) or search(...)")

	async def _search_vector_store(self, request: KnowledgeSearchRequest) -> list[RetrievalResult]:
		vectors = await _maybe_await(self._embedding.embed([request.query]))
		if not vectors:
			return []
		raw = await _maybe_await(self._vector_store.search(vectors[0], top_k=request.candidate_k))
		return [_normalize_result(item, f"{_VECTOR_STORE_RESOURCE}:{idx}") for idx, item in enumerate(raw or [])]

	def _merge_result_sets(self, result_sets: list[list[RetrievalResult]], request: KnowledgeSearchRequest) -> list[RetrievalResult]:
		non_empty = [[item for item in items if item.score >= request.min_score] for items in result_sets if items]
		if not non_empty:
			return []
		if len(non_empty) == 1:
			results = sorted(non_empty[0], key=lambda item: item.score, reverse=True)[:request.top_k]
		else:
			results = rrf_merge(*non_empty, top_k=max(request.candidate_k, request.top_k))[:request.top_k]
		return results

	def _build_reranker(self):
		mode = str(self._rerank_config.get("mode", "score")).lower()
		if self._mounted_reranker:
			return self._mounted_reranker
		if mode == "llm" and self._plugin_ctx.utility_model:
			return LLMReranker(self._plugin_ctx.utility_model)
		if mode in {"", "score"}:
			return ScoreReranker()
		raise RuntimeError(f"unsupported knowledge rerank mode: {mode}")

	def _build_query_rewriter(self):
		if not self._query_rewrite_config.get("enabled", False):
			return NoopQueryRewriter()
		return LLMQueryRewriter(self._plugin_ctx.utility_model)

	def _bm25_search(self, query: str, top_k: int = 30, filters: KnowledgeFilter | dict | None = None) -> list[int]:
		if self._retriever:
			results = self._retriever.bm25.search(query, top_k=top_k, filters=filters)
			indices: list[int] = []
			for result in results:
				try:
					indices.append(int(result.metadata.get("chunk_id", result.id)))
				except (TypeError, ValueError):
					continue
			return indices
		query_terms = _tokenize(query)
		knowledge_filter = _filter_from_config(filters)
		if not query_terms:
			return []
		n_docs = len(self._chunks)
		scores: list[tuple[float, int]] = []
		for i, term_counter in enumerate(self._doc_terms):
			if i < len(self._documents) and not knowledge_filter.matches(self._documents[i]):
				continue
			score = 0.0
			dl = sum(term_counter.values()) or 1
			for qt in query_terms:
				df = self._doc_freq.get(qt, 0)
				if not df:
					continue
				idf = math.log((n_docs - df + 0.5) / (df + 0.5) + 1.0)
				tf = term_counter.get(qt, 0)
				score += idf * ((tf * 2.5) / (tf + 1.5 * (1 - 0.75 + 0.75 * dl / self._avg_dl)))
			if score > 0:
				scores.append((score, i))
		scores.sort(reverse=True)
		return [idx for _, idx in scores[:top_k]]

	def _format_bm25_results(self, indices: list[int], query: str = "") -> list[dict]:
		results: list[dict] = []
		for idx in indices:
			if idx >= len(self._chunks):
				continue
			chunk = self._chunks[idx]
			results.append({
				"id": str(idx),
				"text": chunk["text"],
				"source": chunk.get("source", ""),
				"chunk_id": idx,
				"score": round(1.0 / (len(results) + 1), 4),
				"retrieval": "bm25",
				"metadata": dict(chunk.get("metadata", {})),
				"citation": _citation_for_chunk(chunk, idx),
				"highlights": _highlights(query, chunk["text"]) if query else [],
			})
		return results


async def _call_search(search: Any, request: KnowledgeSearchRequest) -> Any:
	try:
		return await _maybe_await(search(request))
	except TypeError:
		return await _maybe_await(search(request.query, top_k=request.top_k, candidate_k=request.candidate_k))


async def _maybe_await(value: Any) -> Any:
	if inspect.isawaitable(value):
		return await value
	return value


def _normalize_response(raw: Any, request: KnowledgeSearchRequest, retrieval: str) -> KnowledgeSearchResponse:
	if isinstance(raw, KnowledgeSearchResponse):
		return raw
	if isinstance(raw, dict):
		raw_results = raw.get("results", raw.get("data", []))
		trace = raw.get("trace")
		results = [_normalize_result(item, f"{retrieval}:{idx}") for idx, item in enumerate(raw_results or [])]
		return KnowledgeSearchResponse(results=results, trace=_normalize_trace(trace, request, len(results)))
	if isinstance(raw, list | tuple):
		results = [_normalize_result(item, f"{retrieval}:{idx}") for idx, item in enumerate(raw)]
		return KnowledgeSearchResponse(
			results=results,
			trace=RetrievalTrace(query=request.query, candidate_count=len(results), returned_count=min(len(results), request.top_k)) if request.include_trace else None,
		)
	if raw is None:
		return KnowledgeSearchResponse(results=[])
	raise RuntimeError(f"unsupported knowledge search response: {type(raw).__name__}")


def _normalize_result(raw: Any, fallback_id: str) -> RetrievalResult:
	if isinstance(raw, RetrievalResult):
		return raw
	if isinstance(raw, KnowledgeDocument):
		return RetrievalResult(id=raw.id, text=raw.text, score=1.0, retrieval="document", source=raw.source, metadata=raw.metadata)
	if not isinstance(raw, dict):
		return RetrievalResult(id=fallback_id, text=str(raw), score=0.0, retrieval="unknown")
	metadata = dict(raw.get("metadata") or {})
	score = float(raw.get("score", raw.get("relevance", 0.0)) or 0.0)
	text = str(raw.get("text") or raw.get("content") or metadata.get("text") or "")
	source = str(raw.get("source") or metadata.get("source") or "")
	return RetrievalResult(
		id=str(raw.get("id") or metadata.get("id") or fallback_id),
		text=text,
		score=score,
		retrieval=str(raw.get("retrieval") or raw.get("type") or "vector"),
		source=source,
		metadata=metadata,
		citation=dict(raw.get("citation") or {}),
		highlights=[str(item) for item in raw.get("highlights", [])],
	)


def _normalize_document(raw: Any, index: int) -> KnowledgeDocument:
	if isinstance(raw, KnowledgeDocument):
		return raw
	if not isinstance(raw, dict):
		return KnowledgeDocument(id=str(index), text=str(raw), metadata={"chunk_id": index})
	metadata = dict(raw.get("metadata") or {})
	metadata.setdefault("chunk_id", index)
	if raw.get("namespace") and "namespace" not in metadata:
		metadata["namespace"] = raw["namespace"]
	return KnowledgeDocument(
		id=str(raw.get("id") or index),
		text=str(raw.get("text") or raw.get("content") or ""),
		source=str(raw.get("source") or metadata.get("source") or ""),
		metadata=metadata,
	)


def _normalize_trace(raw: Any, request: KnowledgeSearchRequest, count: int) -> RetrievalTrace | None:
	if not request.include_trace:
		return None
	if isinstance(raw, RetrievalTrace):
		return raw
	if isinstance(raw, dict):
		return RetrievalTrace(
			query=str(raw.get("query") or request.query),
			rewritten_queries=[str(item) for item in raw.get("rewritten_queries", [request.query])],
			candidate_count=int(raw.get("candidate_count", count) or 0),
			returned_count=int(raw.get("returned_count", count) or 0),
			filtered=bool(raw.get("filtered", request.filters != KnowledgeFilter())),
			reranked=bool(raw.get("reranked", False)),
		)
	return RetrievalTrace(query=request.query, candidate_count=count, returned_count=min(count, request.top_k))


def _citation_for_chunk(chunk: dict, chunk_id: int) -> dict:
	metadata = dict(chunk.get("metadata", {}))
	citation = {
		"source": chunk.get("source", metadata.get("source", "")),
		"chunk_id": metadata.get("chunk_id", chunk_id),
	}
	for key in ("document_id", "title", "url", "page", "start_line", "end_line", "heading_path", "version", "updated_at"):
		if key in metadata and metadata[key] not in ("", None):
			citation[key] = metadata[key]
	return citation


def _highlights(query: str, text: str, max_items: int = 3, window: int = 80) -> list[str]:
	terms = [term for term in _tokenize(query) if len(term) > 1]
	if not terms:
		return []
	lower = text.lower()
	highlights: list[str] = []
	for term in terms:
		idx = lower.find(term.lower())
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


def _tokenize(text: str) -> list[str]:
	text = text.lower()
	words = re.findall(r"[a-z0-9_]+", text)
	chinese = re.findall(r"[\u4e00-\u9fff]", text)
	bigrams = [chinese[i] + chinese[i + 1] for i in range(len(chinese) - 1)]
	return words + chinese + bigrams


def _resource_name(value: object, default: str) -> str:
	if isinstance(value, str):
		return value
	return default


def _filter_from_config(value: object, namespace: str = "") -> KnowledgeFilter:
	if isinstance(value, KnowledgeFilter):
		if namespace and not value.namespace:
			return KnowledgeFilter(
				namespace=namespace,
				metadata=dict(value.metadata),
				source=value.source,
				source_prefix=value.source_prefix,
				allowed_acl_tags=list(value.allowed_acl_tags),
			)
		return value
	if not isinstance(value, dict):
		return KnowledgeFilter(namespace=namespace)
	metadata = dict(value.get("metadata", {})) if isinstance(value.get("metadata"), dict) else {}
	for key, item in value.items():
		if key not in {"namespace", "source", "source_prefix", "acl_tags", "allowed_acl_tags", "metadata"}:
			metadata[key] = item
	return KnowledgeFilter(
		namespace=str(value.get("namespace") or namespace or ""),
		source=str(value.get("source") or ""),
		source_prefix=str(value.get("source_prefix") or ""),
		allowed_acl_tags=[str(item) for item in value.get("allowed_acl_tags", value.get("acl_tags", []))],
		metadata=metadata,
	)


def _merge_filters(base: KnowledgeFilter, override: KnowledgeFilter | dict | None) -> KnowledgeFilter:
	extra = _filter_from_config(override)
	return KnowledgeFilter(
		namespace=extra.namespace or base.namespace,
		source=extra.source or base.source,
		source_prefix=extra.source_prefix or base.source_prefix,
		allowed_acl_tags=list(extra.allowed_acl_tags or base.allowed_acl_tags),
		metadata={**base.metadata, **extra.metadata},
	)


def _tool_filters(args: dict) -> KnowledgeFilter:
	raw = args.get("filters", {})
	filter_data = dict(raw) if isinstance(raw, dict) else {}
	if args.get("namespace"):
		filter_data["namespace"] = args["namespace"]
	if args.get("allowed_acl_tags"):
		filter_data["allowed_acl_tags"] = args["allowed_acl_tags"]
	return _filter_from_config(filter_data)
