"""Knowledge plugin with host-injected retrieval resources.
中文：知识库插件使用宿主注入资源执行混合检索。

Official plugin boundary:
- retrieval strategy lives here;
- external clients, keys, endpoints, and stores are injected by the host;
- resource failures are surfaced as tool errors.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from typing import TYPE_CHECKING, Any

from axc_agent_engine.core.schema import ToolDefinition
from axc_agent_engine.plugins.base import BasePlugin
from axc_agent_engine.plugins.builtin.common import string_array
from axc_agent_engine.plugins.builtin.config_schemas import KNOWLEDGE_CONFIG_SCHEMA
from axc_agent_engine.plugins.builtin.knowledge.support import (
	BM25Index,
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
from axc_agent_engine.plugins.builtin.knowledge.support.retrieval import normalize_filter

if TYPE_CHECKING:
	from axc_agent_engine.core.context import ExecutionContext
	from axc_agent_engine.plugins.context import PluginContext

logger = logging.getLogger(__name__)

_INDEX_RESOURCE = "knowledge.index"
_DOCUMENTS_RESOURCE = "knowledge.documents"
_EMBEDDING_RESOURCE = "knowledge.embedding"
_VECTOR_STORE_RESOURCE = "knowledge.vector_store"
_RERANKER_RESOURCE = "knowledge.reranker"


class KnowledgeResourceError(RuntimeError):
	"""Invalid mounted knowledge resource. 挂载的知识资源无效。"""


class KnowledgePlugin(BasePlugin):
	name = "knowledge"
	display_name = "知识库"
	priority = 20
	version = "2.0.0"
	config_schema = KNOWLEDGE_CONFIG_SCHEMA

	def initialize(self, config: dict, plugin_ctx: PluginContext) -> None:
		super().initialize(config, plugin_ctx)
		self._sources = config.get("sources", [])
		self._chunk_size = config.get("chunk_size", 512)
		self._chunk_overlap = config.get("chunk_overlap", 50)
		self._namespace = config.get("namespace", "")
		self._default_filter = normalize_filter(config.get("filters", {}), namespace=self._namespace)
		self._default_candidate_k = config.get("candidate_k", 30)
		self._include_trace_default = config.get("include_trace", False)
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
		self._local_index: InMemoryKnowledgeIndexStore | None = None
		self._local_index_ready = False
		self._local_index_lock: asyncio.Lock | None = None
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
		self._bm25 = BM25Index(self._documents)
		if not self._has_retrieval_source():
			raise RuntimeError("knowledge plugin requires sources or mounted knowledge resources")

	def inject_context(self, exec_ctx: ExecutionContext, topic: str = "") -> str:
		if not topic:
			return ""
		results = self._bm25.search(topic, top_k=5, filters=self._default_filter)
		if not results:
			return ""
		lines = ["[相关知识]"]
		for item in results:
			lines.append(f"---\n{item.text}\n(source: {item.source})")
		return "\n".join(lines)

	async def on_execution_start(self, exec_ctx: ExecutionContext) -> None:
		if self._documents or self._mounted_documents:
			await self._ensure_local_index()

	def get_tools(self) -> list[ToolDefinition]:
		return [
			ToolDefinition(
				name="knowledge_search",
				description="搜索知识库中的相关内容",
				parameters={
					"type": "object",
					"properties": {
						"query": {"type": "string", "description": "搜索关键词或问题"},
						"top_k": {"type": "integer", "description": "返回结果数量", "default": 5},
						"candidate_k": {
							"type": "integer",
							"description": "候选召回数量",
							"default": self._default_candidate_k,
						},
						"namespace": {"type": "string", "description": "知识库命名空间"},
						"filters": {"type": "object", "description": '元数据过滤条件，如 {"document_id": "..."}'},
						"allowed_acl_tags": {
							"type": "array",
							"items": {"type": "string"},
							"description": "调用方已授权的 ACL tag",
						},
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
		payload = await self._hybrid_search_payload(
			query,
			top_k=int(args.get("top_k", 5)),
			candidate_k=int(args.get("candidate_k", self._default_candidate_k)),
			filters=_tool_filters(args),
			min_score=float(args.get("min_score", 0.0) or 0.0),
			include_trace=bool(args.get("include_trace", self._include_trace_default)),
		)
		return ToolOutput(
			content=payload,
			content_type="json",
			summary=f"knowledge_search：为 '{query}' 找到 {len(payload.get('results', []))} 条结果",
			llm_view=_knowledge_search_llm_view(query, payload.get("results", [])),
		)

	async def _hybrid_search_payload(
		self,
		query: str,
		top_k: int = 5,
		candidate_k: int | None = None,
		filters: KnowledgeFilter | dict | None = None,
		min_score: float = 0.0,
		include_trace: bool | None = None,
	) -> dict:
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
			trace_sources.append(
				{
					"source": _VECTOR_STORE_RESOURCE,
					"query": request.query,
					"candidate_count": len(vector_results),
					"returned_count": min(len(vector_results), request.top_k),
					"filtered": request.filters != KnowledgeFilter(),
					"reranked": False,
					"rewritten_queries": [request.query],
				}
			)

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

	def _has_retrieval_source(self) -> bool:
		return bool(self._mounted_index or self._mounted_documents or self._documents or self._vector_store)

	def _load_sources(self) -> None:
		result = self._ingestion_pipeline.ingest(self._sources)
		self._documents = list(result.documents)
		for error in result.errors:
			raise RuntimeError(f"knowledge source load failed: {error}")
		logger.info("[knowledge] loaded %s local document chunks", len(self._documents))

	async def _ensure_local_index(self) -> None:
		if self._local_index_ready:
			return
		if self._local_index_lock is None:
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
		if isinstance(resource, list | tuple):
			raw_docs = list(resource)
		else:
			list_documents = getattr(resource, "list_documents", None)
			if not callable(list_documents):
				raise KnowledgeResourceError("knowledge.documents must be a sequence or expose list_documents()")
			raw_docs = await _maybe_await(list_documents())
		return [_normalize_document(item, index) for index, item in enumerate(raw_docs)]

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
			raise RuntimeError("knowledge.embedding returned no vectors")
		raw = await _maybe_await(self._vector_store.search(vectors[0], top_k=request.candidate_k))
		if raw is None:
			raise RuntimeError("knowledge.vector_store.search returned None")
		return [_normalize_result(item, f"{_VECTOR_STORE_RESOURCE}:{idx}") for idx, item in enumerate(raw)]

	def _merge_result_sets(
		self, result_sets: list[list[RetrievalResult]], request: KnowledgeSearchRequest
	) -> list[RetrievalResult]:
		non_empty = [[item for item in items if item.score >= request.min_score] for items in result_sets if items]
		if not non_empty:
			return []
		if len(non_empty) == 1:
			results = sorted(non_empty[0], key=lambda item: item.score, reverse=True)[: request.top_k]
		else:
			results = rrf_merge(*non_empty, top_k=max(request.candidate_k, request.top_k))[: request.top_k]
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
		if not self._plugin_ctx.utility_model:
			raise RuntimeError("knowledge.query_rewrite requires mounted utility model")
		return LLMQueryRewriter(self._plugin_ctx.utility_model)


async def _call_search(search: Any, request: KnowledgeSearchRequest) -> Any:
	return await _maybe_await(search(request))


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
			trace=RetrievalTrace(
				query=request.query, candidate_count=len(results), returned_count=min(len(results), request.top_k)
			)
			if request.include_trace
			else None,
		)
	if raw is None:
		raise RuntimeError(f"{retrieval} returned None")
	raise RuntimeError(f"unsupported knowledge search response: {type(raw).__name__}")


def _normalize_result(raw: Any, fallback_id: str) -> RetrievalResult:
	if isinstance(raw, RetrievalResult):
		return raw
	if isinstance(raw, KnowledgeDocument):
		return RetrievalResult(
			id=raw.id, text=raw.text, score=1.0, retrieval="document", source=raw.source, metadata=raw.metadata
		)
	if not isinstance(raw, dict):
		raise KnowledgeResourceError(f"unsupported knowledge search result: {type(raw).__name__}")
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
		highlights=string_array(raw.get("highlights", []), "highlights"),
	)


def _normalize_document(raw: Any, index: int) -> KnowledgeDocument:
	if isinstance(raw, KnowledgeDocument):
		return raw
	if not isinstance(raw, dict):
		raise KnowledgeResourceError(f"unsupported knowledge document: {type(raw).__name__}")
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
			rewritten_queries=string_array(raw.get("rewritten_queries", [request.query]), "rewritten_queries"),
			candidate_count=int(raw.get("candidate_count", count) or 0),
			returned_count=int(raw.get("returned_count", count) or 0),
			filtered=bool(raw.get("filtered", request.filters != KnowledgeFilter())),
			reranked=bool(raw.get("reranked", False)),
		)
	return RetrievalTrace(query=request.query, candidate_count=count, returned_count=min(count, request.top_k))


def _merge_filters(base: KnowledgeFilter, override: KnowledgeFilter | dict | None) -> KnowledgeFilter:
	extra = normalize_filter(override)
	return KnowledgeFilter(
		namespace=extra.namespace or base.namespace,
		source=extra.source or base.source,
		source_prefix=extra.source_prefix or base.source_prefix,
		allowed_acl_tags=list(extra.allowed_acl_tags or base.allowed_acl_tags),
		metadata={**base.metadata, **extra.metadata},
	)


def _tool_filters(args: dict) -> KnowledgeFilter:
	raw = args.get("filters", {})
	if not isinstance(raw, dict):
		raise TypeError("knowledge filters must be an object")
	filter_data = raw.copy()
	if args.get("namespace"):
		filter_data["namespace"] = args["namespace"]
	if "allowed_acl_tags" in args:
		filter_data["allowed_acl_tags"] = args["allowed_acl_tags"]
	return normalize_filter(filter_data)


def _knowledge_search_llm_view(query: str, results: list[dict]) -> str:
	if not results:
		return f"Knowledge search\nQuery: {query}\nResults: none"
	lines = [f"Knowledge search\nQuery: {query}", f"Results: {len(results)}"]
	for index, item in enumerate(results, start=1):
		score = item.get("score", "")
		source = item.get("source", "") or item.get("document_id", "")
		text = str(item.get("text", item.get("content", ""))).strip()
		title = item.get("title", "") or item.get("chunk_id", "") or item.get("id", "")
		header = f"{index}. {title}".rstrip()
		if score != "":
			header += f" | score={score}"
		if source:
			header += f" | source={source}"
		lines.append(header)
		if text:
			lines.append(f"   {text}")
	return "\n".join(lines)
