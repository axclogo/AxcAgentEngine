"""Retrieval helpers private to the memory plugin. 记忆插件内部的检索函数。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from axc_agent_engine.plugins.builtin.bm25 import BM25Corpus, tokenize


@dataclass(frozen=True)
class MemoryDocument:
	"""A searchable memory document. 可供检索的记忆文档。"""

	id: str
	text: str
	metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RetrievalResult:
	"""One memory retrieval hit. 一条记忆检索命中记录。"""

	id: str
	text: str
	score: float
	retrieval: str
	source: str = ""
	metadata: dict[str, Any] = field(default_factory=dict)


class BM25Index(BM25Corpus[MemoryDocument]):
	"""Small dependency-free BM25 index for memory content. 为记忆文本建立无外部依赖的 BM25 索引。"""

	def search(self, query: str, top_k: int = 30) -> list[RetrievalResult]:
		return [
			RetrievalResult(
				id=doc.id,
				text=doc.text,
				score=score,
				retrieval="bm25",
				source=str(doc.metadata.get("source", "")),
				metadata=doc.metadata,
			)
			for score, doc in self.rank(tokenize(query), top_k)
		]


def rrf_merge(*ranked_lists: list[RetrievalResult], top_k: int = 10, k: int = 60) -> list[RetrievalResult]:
	"""Reciprocal rank fusion. 倒数排名融合。"""
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
		results.append(
			RetrievalResult(
				id=item.id,
				text=item.text,
				score=score,
				retrieval=retrieval,
				source=item.source,
				metadata=item.metadata,
			)
		)
	return results
