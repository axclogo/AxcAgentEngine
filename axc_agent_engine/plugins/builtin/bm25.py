"""Shared BM25 statistics and ranking. 共用 BM25 词频统计和评分。"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Callable
from typing import Generic, Protocol, TypeVar


class TextDocument(Protocol):
	@property
	def text(self) -> str: ...


Doc = TypeVar("Doc", bound=TextDocument)


class BM25Corpus(Generic[Doc]):
	def __init__(self, documents: list[Doc] | None = None) -> None:
		self.documents: list[Doc] = []
		self._doc_terms: list[Counter] = []
		self._doc_freq: Counter = Counter()
		self._avg_dl = 1.0
		if documents:
			self.build(documents)

	def build(self, documents: list[Doc]) -> None:
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

	def rank(
		self, query_terms: list[str], top_k: int, matches: Callable[[Doc], bool] | None = None
	) -> list[tuple[float, Doc]]:
		if not query_terms or not self.documents:
			return []
		n_docs = len(self.documents)
		k1, b = 1.5, 0.75
		scored: list[tuple[float, int]] = []
		for idx, counter in enumerate(self._doc_terms):
			if matches is not None and not matches(self.documents[idx]):
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
		return [(score, self.documents[idx]) for score, idx in scored[:top_k]]


def tokenize(text: str) -> list[str]:
	"""Tokenize identifiers, numbers, and Chinese unigrams and bigrams. 提取标识符、数字以及中文单字和双字词。"""
	text = text.lower()
	tokens: list[str] = []
	tokens.extend(re.findall(r"[a-z_][a-z0-9_]*", text))
	tokens.extend(re.findall(r"\d+", text))
	for word in re.findall(r"[\u4e00-\u9fff]+", text):
		tokens.extend(list(word))
		tokens.extend(word[i : i + 2] for i in range(len(word) - 1))
	return [token for token in tokens if token]
