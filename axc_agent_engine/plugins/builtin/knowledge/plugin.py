"""Knowledge 插件 — 文档切块、向量化和混合检索（BM25 索引缓存）。"""
import asyncio
import hashlib
import logging
import math
import os
import re
import time
from collections import Counter
from typing import TYPE_CHECKING

from axc_agent_engine.core.schema import ToolDefinition
from axc_agent_engine.plugins.builtin.knowledge.support import (
	CascadeReranker,
	ExternalReranker,
	HybridRetriever,
	InMemoryKnowledgeIndexStore,
	KnowledgeFilter,
	KnowledgeDocument,
	KnowledgeSearchRequest,
	LLMQueryRewriter,
	LLMReranker,
	LocalFileIngestionPipeline,
	NoopQueryRewriter,
	OpenAICompatibleEmbeddingClient,
	ScoreReranker,
	SemanticChunker,
)
from axc_agent_engine.plugins.base import BasePlugin
from axc_agent_engine.plugins.builtin.config_schemas import KNOWLEDGE_CONFIG_SCHEMA
from axc_agent_engine.utils.math_utils import cosine_similarity

if TYPE_CHECKING:
	from axc_agent_engine.core.context import ExecutionContext
	from axc_agent_engine.plugins import PluginContext

logger = logging.getLogger(__name__)


class KnowledgeResultFormatter:
	def merge_results(self, chunks: list[dict], bm25_indices: list[int], vector_items: list[dict],
					  top_k: int, query: str, min_score: float = 0.0) -> list[dict]:
		k = 60
		scores: dict[int, float] = {}
		for rank, idx in enumerate(bm25_indices):
			scores[idx] = scores.get(idx, 0) + 1.0 / (k + rank + 1)
		for rank, item in enumerate(vector_items):
			idx = item["chunk_id"]
			if isinstance(idx, int):
				scores[idx] = scores.get(idx, 0) + 1.0 / (k + rank + 1)
		sorted_items = sorted(scores.items(), key=lambda x: x[1], reverse=True)
		results: list[dict] = []
		for idx, score in sorted_items[:top_k]:
			if idx < len(chunks):
				chunk = chunks[idx]
				item = {
					"text": chunk["text"],
					"source": chunk.get("source", ""),
					"chunk_id": idx,
					"score": round(score, 4),
					"retrieval": "hybrid",
					"metadata": dict(chunk.get("metadata", {})),
					"citation": self.citation_for_chunk(chunk, idx),
					"highlights": self.highlights(query, chunk["text"]),
				}
				if item["score"] >= min_score:
					results.append(item)
		return results

	def format_bm25_results(self, chunks: list[dict], indices: list[int], query: str = "") -> list[dict]:
		results: list[dict] = []
		for idx in indices:
			if idx < len(chunks):
				chunk = chunks[idx]
				results.append({
					"text": chunk["text"],
					"source": chunk.get("source", ""),
					"chunk_id": idx,
					"score": round(1.0 / (len(results) + 1), 4),
					"retrieval": "bm25",
					"metadata": dict(chunk.get("metadata", {})),
					"citation": self.citation_for_chunk(chunk, idx),
					"highlights": self.highlights(query, chunk["text"]) if query else [],
				})
		return results

	def citation_for_chunk(self, chunk: dict, chunk_id: int) -> dict:
		metadata = dict(chunk.get("metadata", {}))
		citation = {
			"source": chunk.get("source", metadata.get("source", "")),
			"chunk_id": metadata.get("chunk_id", chunk_id),
		}
		for key in ("document_id", "title", "url", "page", "start_line", "end_line", "heading_path", "version", "updated_at"):
			if key in metadata and metadata[key] not in ("", None):
				citation[key] = metadata[key]
		return citation

	def highlights(self, query: str, text: str, max_items: int = 3, window: int = 80) -> list[str]:
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

	def trace_dict(self, request: KnowledgeSearchRequest, candidate_count: int, returned_count: int, reranked: bool) -> dict | None:
		if not request.include_trace:
			return None
		return {
			"query": request.query,
			"rewritten_queries": [request.query],
			"candidate_count": candidate_count,
			"returned_count": returned_count,
			"filtered": request.filters != KnowledgeFilter(),
			"reranked": reranked,
		}


class KnowledgeSearchService:
	def __init__(self, plugin: "KnowledgePlugin", formatter: KnowledgeResultFormatter) -> None:
		self.plugin = plugin
		self.formatter = formatter

	def hybrid_search(self, query: str, top_k: int = 5, filters: KnowledgeFilter | dict | None = None) -> list[dict]:
		plugin = self.plugin
		if not plugin._chunks:
			return []
		return self.formatter.format_bm25_results(plugin._chunks, plugin._bm25_search(query, top_k=top_k, filters=filters), query)

	async def hybrid_search_async(
		self,
		query: str,
		top_k: int = 5,
		candidate_k: int | None = None,
		filters: KnowledgeFilter | dict | None = None,
		min_score: float = 0.0,
		include_trace: bool | None = None,
	) -> list[dict]:
		return (await self.hybrid_search_payload(
			query,
			top_k=top_k,
			candidate_k=candidate_k,
			filters=filters,
			min_score=min_score,
			include_trace=include_trace,
		)).get("results", [])

	async def hybrid_search_payload(
		self,
		query: str,
		top_k: int = 5,
		candidate_k: int | None = None,
		filters: KnowledgeFilter | dict | None = None,
		min_score: float = 0.0,
		include_trace: bool | None = None,
	) -> dict:
		plugin = self.plugin
		if not plugin._chunks:
			return {"results": [], "trace": None}
		include_trace = plugin._include_trace_default if include_trace is None else include_trace
		request = KnowledgeSearchRequest(
			query=query,
			top_k=top_k,
			candidate_k=candidate_k or plugin._default_candidate_k,
			filters=_merge_filters(plugin._default_filter, filters),
			min_score=min_score,
			include_trace=bool(include_trace),
		)
		if plugin._index_store and not plugin._vector_store:
			response = await plugin._index_store.search_with_trace(request)
			if response.results:
				return response.to_dict()
		bm25_results = plugin._bm25_search(query, top_k=request.candidate_k, filters=request.filters)
		vector_items: list[dict] = []
		if plugin._vector_store and plugin._embedding_client:
			try:
				query_embeddings = await plugin._embed_texts([query])
				if query_embeddings:
					raw_results = await plugin._vector_store.search(query_embeddings[0], top_k=request.candidate_k)
					vector_items = self.normalize_vector_results(raw_results)
			except Exception as e:
				logger.warning(f"[knowledge] vector_store.search failed, falling back: {e}")
			if not vector_items and plugin._embeddings:
				vector_items = plugin._local_vector_items(query, top_k=request.candidate_k)
		if vector_items:
			results = self.formatter.merge_results(plugin._chunks, bm25_results, vector_items, request.top_k, query, request.min_score)
			return {"results": results, "trace": self.formatter.trace_dict(request, len(bm25_results) + len(vector_items), len(results), reranked=False)}
		results = self.formatter.format_bm25_results(plugin._chunks, bm25_results, query)[:request.top_k]
		if request.min_score:
			results = [item for item in results if item.get("score", 0.0) >= request.min_score]
		return {"results": results, "trace": self.formatter.trace_dict(request, len(bm25_results), len(results), reranked=False)}

	def normalize_vector_results(self, raw_results: list[dict]) -> list[dict]:
		items: list[dict] = []
		for r in raw_results:
			chunk_id = r.get("metadata", {}).get("chunk_id", r.get("id", 0))
			items.append({"chunk_id": chunk_id, "score": r.get("score", 0.0)})
		return items


class KnowledgeEmbeddingIndexer:
	def __init__(self, plugin: "KnowledgePlugin") -> None:
		self.plugin = plugin

	async def build_incremental(self) -> None:
		plugin = self.plugin
		if not plugin._chunks:
			return
		source_chunks: dict[str, list[tuple[int, dict]]] = {}
		for i, chunk in enumerate(plugin._chunks):
			src = chunk.get("source", "unknown")
			source_chunks.setdefault(src, []).append((i, chunk))
		if plugin._kv_store:
			keys = await plugin._kv_store.list_keys("knowledge:index:")
			for key in keys:
				manifest = await plugin._kv_store.get(key)
				if manifest:
					plugin._manifests[manifest.get("fingerprint", "")] = manifest
		if plugin._embeddings is None:
			plugin._embeddings = [[] for _ in plugin._chunks]
		for src, indexed_chunks in source_chunks.items():
			content_hash = hashlib.sha256("".join(c["text"] for _, c in indexed_chunks).encode()).hexdigest()[:16]
			fingerprint = f"{src}:{content_hash}:{plugin._index_version}"
			if fingerprint in plugin._manifests:
				continue
			src_real = os.path.realpath(src)
			old_manifest = next((m for m in plugin._manifests.values() if os.path.realpath(m.get("source", "")) == src_real), None)
			if old_manifest and plugin._vector_store:
				old_ids = old_manifest.get("chunk_ids", [])
				if old_ids:
					await plugin._vector_store.delete(old_ids)
				old_fp = old_manifest.get("fingerprint", "")
				plugin._manifests.pop(old_fp, None)
				if plugin._kv_store:
					await plugin._kv_store.delete(f"knowledge:index:{old_fp}")
			texts = [c["text"] for _, c in indexed_chunks]
			indices = [i for i, _ in indexed_chunks]
			metadata = [dict(c.get("metadata", {"source": src, "chunk_id": i})) for i, c in indexed_chunks]
			embeddings = await plugin._embed_texts(texts)
			if not embeddings:
				continue
			for idx, emb in zip(indices, embeddings):
				if idx < len(plugin._embeddings):
					plugin._embeddings[idx] = emb
					plugin._index_store.set_document_embedding(str(idx), emb)
			chunk_ids: list[str] = []
			if plugin._vector_store:
				chunk_ids = await plugin._vector_store.add(texts, embeddings, metadata)
			manifest = {
				"source": src,
				"fingerprint": fingerprint,
				"chunk_ids": chunk_ids,
				"index_version": plugin._index_version,
				"updated_at": time.time(),
			}
			plugin._manifests[fingerprint] = manifest
			if plugin._kv_store:
				await plugin._kv_store.set(f"knowledge:index:{fingerprint}", manifest)
		logger.info(f"[knowledge] Embedding build complete, {len(plugin._chunks)} chunks indexed")


class KnowledgePlugin(BasePlugin):
	name = "knowledge"
	display_name = "知识库"
	priority = 20
	version = "1.0.0"
	config_schema = KNOWLEDGE_CONFIG_SCHEMA

	def initialize(self, config: dict, plugin_ctx: "PluginContext") -> None:
		super().initialize(config, plugin_ctx)
		self._sources = config.get("sources", [])
		self._chunk_size = config.get("chunk_size", 512)
		self._chunk_overlap = config.get("chunk_overlap", 50)
		self._embedding_config = config.get("embedding", {})
		self._embedding_batch_size = max(1, int(self._embedding_config.get("batch_size", 64)))
		self._embedding_retries = max(0, int(self._embedding_config.get("retries", 2)))
		self._embedding_retry_delay = max(0.0, float(self._embedding_config.get("retry_delay", 0.5)))
		self._rerank_config = config.get("rerank", {})
		self._query_rewrite_config = config.get("query_rewrite", {})
		self._namespace = str(config.get("namespace", ""))
		self._default_filter = _filter_from_config(config.get("filters", {}), namespace=self._namespace)
		self._default_candidate_k = int(config.get("candidate_k", 30))
		self._include_trace_default = bool(config.get("include_trace", False))
		self._vector_resource = _resource_name(config.get("vector_store"), "knowledge_vector")
		self._documents: list[KnowledgeDocument] = []
		self._chunks: list[dict] = []
		self._embeddings: list[list[float]] | None = None
		self._embedding_client: dict | None = None
		self._vector_store = plugin_ctx.resources.get(self._vector_resource) if self._vector_resource else None
		self._kv_store = plugin_ctx.kv_store
		self._workspace = plugin_ctx.workspace or ""
		self._embedding_ready = False
		self._embedding_lock: asyncio.Lock | None = None
		self._index_version = f"{self._chunk_size}:{self._chunk_overlap}"
		self._manifests: dict[str, dict] = {}  # English: source_hash maps to manifest. 中文：source_hash 映射到 manifest。
		# English: BM25 cache. 中文：BM25 缓存。
		self._doc_terms: list[Counter] = []
		self._doc_freq: Counter = Counter()
		self._avg_dl: float = 0.0
		self._vocab: dict[str, int] = {}
		self._vocab_sorted: list[str] = []
		self._chunker = SemanticChunker(max_chunk_size=self._chunk_size, chunk_overlap=self._chunk_overlap)
		self._formatter = KnowledgeResultFormatter()
		self._search_service = KnowledgeSearchService(self, self._formatter)
		self._embedding_indexer = KnowledgeEmbeddingIndexer(self)
		self._ingestion_pipeline = LocalFileIngestionPipeline(
			chunker=self._chunker,
			workspace=self._workspace,
			namespace=self._namespace,
			default_metadata=config.get("metadata", {}),
		)
		self._retriever: HybridRetriever | None = None
		self._reranker = self._build_reranker()
		self._query_rewriter = self._build_query_rewriter()
		self._index_store = InMemoryKnowledgeIndexStore(reranker=self._reranker, query_rewriter=self._query_rewriter)
		if self._embedding_config.get("base_url"):
			self._init_embedding_client()
		self._load_sources()
		self._build_bm25_index()
		self._build_retriever()

	def inject_context(self, exec_ctx: "ExecutionContext", topic: str = "") -> str:
		if not self._chunks or not topic:
			return ""
		results = self._hybrid_search(topic, top_k=5, filters=self._default_filter)
		if not results:
			return ""
		lines = ["[相关知识]"]
		for item in results:
			lines.append(f"---\n{item['text']}\n(source: {item.get('source', 'unknown')})")
		return "\n".join(lines)

	async def on_execution_start(self, exec_ctx: "ExecutionContext") -> None:
		"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
首次执行时异步构建 embeddings，带锁并支持增量更新。"""
		if self._embedding_ready or not self._embedding_client:
			return
		if self._embedding_lock is None:
			self._embedding_lock = asyncio.Lock()
		async with self._embedding_lock:
			if self._embedding_ready:
				return
			await self._build_embeddings_incremental()
			self._embedding_ready = True

	async def _build_embeddings_incremental(self) -> None:
		"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
基于 fingerprint 增量构建 embeddings，每个 source 独立 manifest。"""
		await self._embedding_indexer.build_incremental()

	def get_tools(self) -> list[ToolDefinition]:
		"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
提供 knowledge_search 工具"""
		if not self._chunks:
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
				execute=self._tool_knowledge_search,
			)
		]

	def _build_bm25_index(self) -> None:
		"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
构建 BM25 索引缓存（_load_sources 后调用一次）"""
		self._doc_terms = []
		self._doc_freq = Counter()
		self._vocab = {}
		total_dl = 0.0
		for chunk in self._chunks:
			terms = _tokenize(chunk["text"])
			term_counter = Counter(terms)
			self._doc_terms.append(term_counter)
			total_dl += len(terms)
			for t in set(terms):
				self._doc_freq[t] += 1
				if t not in self._vocab:
					self._vocab[t] = 0
				self._vocab[t] += 1
		n_docs = len(self._chunks)
		self._avg_dl = total_dl / n_docs if n_docs > 0 else 1.0
		self._vocab_sorted = sorted(self._vocab.keys())
		self._build_retriever()

	def _build_retriever(self) -> None:
		documents = list(self._documents)
		self._retriever = HybridRetriever(documents)
		self._index_store = InMemoryKnowledgeIndexStore(
			embedding_client=self._index_store.embedding_client,
			reranker=self._reranker,
			query_rewriter=self._query_rewriter,
		)
		self._index_store.set_documents(documents)

	def _build_reranker(self):
		mode = str(self._rerank_config.get("mode", "score")).lower()
		rerankers = []
		endpoint = self._rerank_config.get("endpoint", "")
		if endpoint and mode in {"model", "external", "cascade"}:
			rerankers.append(ExternalReranker(
				endpoint=endpoint,
				api_key=self._rerank_config.get("api_key", ""),
				timeout=float(self._rerank_config.get("timeout", 30)),
			))
		if mode in {"llm", "cascade"} and self._plugin_ctx.utility_model:
			rerankers.append(LLMReranker(self._plugin_ctx.utility_model))
		rerankers.append(ScoreReranker())
		if len(rerankers) == 1:
			return rerankers[0]
		return CascadeReranker(rerankers)

	def _build_query_rewriter(self):
		if not self._query_rewrite_config.get("enabled", False):
			return NoopQueryRewriter()
		return LLMQueryRewriter(self._plugin_ctx.utility_model)

	def _hybrid_search(self, query: str, top_k: int = 5, filters: KnowledgeFilter | dict | None = None) -> list[dict]:
		"""English: This documentation describes the related engine component behavior.
中文：同步快速检索路径，仅用于上下文注入。"""
		return self._search_service.hybrid_search(query, top_k=top_k, filters=filters)

	def _bm25_search(self, query: str, top_k: int = 30, filters: KnowledgeFilter | dict | None = None) -> list[int]:
		"""English: This documentation describes the related engine component behavior.
中文：BM25 检索（使用缓存索引）"""
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
		k1, b = 1.5, 0.75
		scores: list[tuple[float, int]] = []
		for i, term_counter in enumerate(self._doc_terms):
			if i < len(self._documents) and not knowledge_filter.matches(self._documents[i]):
				continue
			score = 0.0
			dl = sum(term_counter.values())
			for qt in query_terms:
				if qt not in self._doc_freq:
					continue
				df = self._doc_freq[qt]
				idf = math.log((n_docs - df + 0.5) / (df + 0.5) + 1.0)
				tf = term_counter.get(qt, 0)
				tf_norm = (tf * (k1 + 1)) / (tf + k1 * (1 - b + b * dl / self._avg_dl))
				score += idf * tf_norm
			if score > 0:
				scores.append((score, i))
		scores.sort(reverse=True)
		return [idx for _, idx in scores[:top_k]]

	def _local_vector_items(self, query: str, top_k: int = 30) -> list[dict]:
		"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
本地向量 fallback；只在 query 和 chunk 向量维度一致时启用。"""
		if not self._embeddings:
			return []
		query_terms = _tokenize(query)
		query_vec = self._text_to_sparse_vec(query_terms)
		if not query_vec:
			return []
		scores: list[tuple[float, int]] = []
		for i, emb in enumerate(self._embeddings):
			if len(query_vec) != len(emb):
				continue
			sim = cosine_similarity(query_vec, emb)
			if sim > 0:
				scores.append((sim, i))
		scores.sort(reverse=True)
		return [{"chunk_id": idx, "score": score} for score, idx in scores[:top_k]]

	def _text_to_sparse_vec(self, terms: list[str]) -> list[float]:
		"""TF-IDF 稀疏向量（使用缓存词汇表）"""
		if not self._chunks or not self._vocab_sorted:
			return []
		n_docs = len(self._chunks)
		term_counts = Counter(terms)
		vec = []
		for t in self._vocab_sorted:
			tf = term_counts.get(t, 0)
			df = self._vocab.get(t, 0)
			idf = math.log((n_docs + 1) / (df + 1)) + 1
			vec.append(tf * idf)
		return vec

	def _init_embedding_client(self) -> None:
		"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
初始化 embedding API 客户端"""
		try:
			self._embedding_client = {
				"base_url": self._embedding_config["base_url"].rstrip("/"),
				"api_key": self._embedding_config.get("api_key", ""),
			}
			self._index_store.embedding_client = OpenAICompatibleEmbeddingClient(
				self._embedding_client["base_url"],
				self._embedding_client["api_key"],
			)
			logger.info("[knowledge] Embedding API configured")
		except Exception as e:
			logger.warning(f"[knowledge] Embedding client init failed: {e}")

	async def _embed_texts(self, texts: list[str]) -> list[list[float]]:
		"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
调用 embedding API 获取向量"""
		if not self._embedding_client:
			return []
		output: list[list[float]] = []
		for start in range(0, len(texts), self._embedding_batch_size):
			batch = texts[start:start + self._embedding_batch_size]
			vectors = await self._embed_batch(batch)
			if len(vectors) != len(batch):
				logger.warning("[knowledge] Embedding batch returned %s vectors for %s texts", len(vectors), len(batch))
				return output
			output.extend(vectors)
		return output

	async def _embed_batch(self, texts: list[str]) -> list[list[float]]:
		if not texts:
			return []
		import httpx
		last_error: Exception | None = None
		for attempt in range(self._embedding_retries + 1):
			try:
				async with httpx.AsyncClient(timeout=float(self._embedding_config.get("timeout", 30))) as client:
					resp = await client.post(
						f"{self._embedding_client['base_url']}/embeddings",
						headers={"Authorization": f"Bearer {self._embedding_client['api_key']}",
								 "Content-Type": "application/json"},
						json={"input": texts})
					resp.raise_for_status()
					data = resp.json()
					return [item["embedding"] for item in data.get("data", [])]
			except Exception as e:
				last_error = e
				if attempt < self._embedding_retries and self._embedding_retry_delay:
					await asyncio.sleep(self._embedding_retry_delay * (attempt + 1))
		logger.warning(f"[knowledge] Embedding API call failed: {last_error}")
		return []

	def _load_sources(self) -> None:
		"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
加载知识源文件，并按 workspace 解析相对路径。"""
		result = self._ingestion_pipeline.ingest(self._sources)
		self._documents = list(result.documents)
		self._chunks = [
			{
				"text": doc.text,
				"source": doc.source,
				"metadata": dict(doc.metadata),
				"embedding": None,
			}
			for doc in self._documents
		]
		for error in result.errors:
			logger.warning("[knowledge] %s", error)
		logger.info(f"[knowledge] Loaded {len(self._chunks)} document chunks")

	async def _hybrid_search_async(
		self,
		query: str,
		top_k: int = 5,
		candidate_k: int | None = None,
		filters: KnowledgeFilter | dict | None = None,
		min_score: float = 0.0,
		include_trace: bool | None = None,
	) -> list[dict]:
		return await self._search_service.hybrid_search_async(
			query,
			top_k=top_k,
			candidate_k=candidate_k,
			filters=filters,
			min_score=min_score,
			include_trace=include_trace,
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
		"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
异步混合检索：vector_store.search → 本地 cosine → BM25 fallback。"""
		return await self._search_service.hybrid_search_payload(
			query,
			top_k=top_k,
			candidate_k=candidate_k,
			filters=filters,
			min_score=min_score,
			include_trace=include_trace,
		)

	def _normalize_vector_results(self, raw_results: list[dict]) -> list[dict]:
		"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
把 vector_store.search 结果标准化为内部格式。"""
		return self._search_service.normalize_vector_results(raw_results)

	def _merge_results(self, bm25_indices: list[int], vector_items: list[dict], top_k: int, query: str, min_score: float = 0.0) -> list[dict]:
		"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
用 RRF 融合 BM25 索引和向量结果。"""
		return self._formatter.merge_results(self._chunks, bm25_indices, vector_items, top_k, query, min_score)

	def _format_bm25_results(self, indices: list[int], query: str = "") -> list[dict]:
		"""English: This documentation describes the related engine component behavior.
中文：格式化纯 BM25 结果。"""
		return self._formatter.format_bm25_results(self._chunks, indices, query)

	async def _tool_knowledge_search(self, args: dict, context: dict):
		"""knowledge_search 工具，支持 vector_store 的异步混合检索。"""
		from axc_agent_engine.tools.tool_output import ToolOutput
		query = args.get("query", "")
		top_k = int(args.get("top_k", 5))
		if not query:
			return ToolOutput.error("query 不能为空")
		payload = await self._hybrid_search_payload(
			query,
			top_k=top_k,
			candidate_k=int(args.get("candidate_k", self._default_candidate_k)),
			filters=_tool_filters(args),
			min_score=float(args.get("min_score", 0.0) or 0.0),
			include_trace=bool(args.get("include_trace", self._include_trace_default)),
		)
		return ToolOutput.json_output(
			payload,
			summary=f"knowledge_search：为 '{query[:50]}' 找到 {len(payload.get('results', []))} 条结果"
		)


def _tokenize(text: str) -> list[str]:
	"""English: This documentation describes the related engine component behavior.
中文：简单分词，支持中英文混合文本。"""
	text = text.lower()
	words = re.findall(r'[a-z0-9]+', text)
	chinese = re.findall(r'[\u4e00-\u9fff]', text)
	bigrams = [chinese[i] + chinese[i + 1] for i in range(len(chinese) - 1)]
	return words + chinese + bigrams


def _resource_name(value: object, default: str) -> str:
	if isinstance(value, str):
		return value
	if isinstance(value, dict):
		return str(value.get("resource", default))
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
