"""Retrieval helpers private to the memory plugin."""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class MemoryDocument:
	"""A searchable memory document."""
	id: str
	text: str
	metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RetrievalResult:
	"""One memory retrieval hit."""
	id: str
	text: str
	score: float
	retrieval: str
	source: str = ""
	metadata: dict[str, Any] = field(default_factory=dict)


class BM25Index:
	"""Small dependency-free BM25 index for memory content."""

	def __init__(self, documents: list[MemoryDocument] | None = None) -> None:
		self.documents: list[MemoryDocument] = []
		self._doc_terms: list[Counter] = []
		self._doc_freq: Counter = Counter()
		self._avg_dl = 1.0
		if documents:
			self.build(documents)

	def build(self, documents: list[MemoryDocument]) -> None:
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

	def search(self, query: str, top_k: int = 30) -> list[RetrievalResult]:
		query_terms = tokenize(query)
		if not query_terms or not self.documents:
			return []
		n_docs = len(self.documents)
		k1, b = 1.5, 0.75
		scored: list[tuple[float, int]] = []
		for idx, counter in enumerate(self._doc_terms):
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
		return [
			RetrievalResult(
				id=self.documents[idx].id,
				text=self.documents[idx].text,
				score=score,
				retrieval="bm25",
				source=str(self.documents[idx].metadata.get("source", "")),
				metadata=self.documents[idx].metadata,
			)
			for score, idx in scored[:top_k]
		]


def rrf_merge(*ranked_lists: list[RetrievalResult], top_k: int = 10, k: int = 60) -> list[RetrievalResult]:
	"""Reciprocal rank fusion."""
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
			score=score,
			retrieval=retrieval,
			source=item.source,
			metadata=item.metadata,
		))
	return results


def tokenize(text: str) -> list[str]:
	"""Tokenize Chinese, English, numbers, and identifiers."""
	text = text.lower()
	tokens: list[str] = []
	tokens.extend(re.findall(r"[a-z_][a-z0-9_]*", text))
	tokens.extend(re.findall(r"\d+", text))
	for word in re.findall(r"[\u4e00-\u9fff]+", text):
		tokens.extend(list(word))
		tokens.extend(word[i:i + 2] for i in range(len(word) - 1))
	return [token for token in tokens if token]
