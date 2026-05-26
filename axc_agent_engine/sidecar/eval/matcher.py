"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
宿主调用的标注匹配器，用于评测或参考答案短路。
Host-invoked annotation matcher for eval/reference-answer short-circuiting."""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import TYPE_CHECKING, Any

from axc_agent_engine.sidecar.eval.store import AnnotationReply

if TYPE_CHECKING:
	from axc_agent_engine.llm.provider import EmbeddingProvider
	from axc_agent_engine.sidecar.eval.store import AnnotationStore


@dataclass
class AnnotationMatch:
	"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
一条匹配到的标注回复。
	One matched annotation reply.
	"""

	reply: AnnotationReply
	score: float
	method: str
	matched_text: str = ""


class AnnotationMatcher:
	"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
在不触碰 Agent runtime 的前提下，把用户输入匹配到 AnnotationStore。
	Matches user input against AnnotationStore without touching Agent runtime.
	"""

	def __init__(
		self,
		store: "AnnotationStore",
		embedding_provider: "EmbeddingProvider | None" = None,
		threshold: float = 0.86,
	) -> None:
		self._store = store
		self._embedding_provider = embedding_provider
		self._threshold = threshold

	async def match(self, query: str, threshold: float | None = None) -> AnnotationMatch | None:
		"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
返回超过阈值的最佳标注匹配。
		Return the best matching annotation above threshold.
		"""
		matches = await self.match_all(query, top_k=1, threshold=threshold)
		return matches[0] if matches else None

	async def match_all(self, query: str, top_k: int = 5, threshold: float | None = None) -> list[AnnotationMatch]:
		"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
使用精确、向量、词法降级评分返回排序后的匹配。
		Return ranked matches using exact, vector, then lexical fallback scoring.
		"""
		if not query.strip():
			return []
		replies = await self._store.list_replies()
		candidates = [(reply, _reply_match_text(reply)) for reply in replies]
		candidates = [(reply, text) for reply, text in candidates if text]
		if not candidates:
			return []

		query_norm = _normalize(query)
		exact = [
			AnnotationMatch(reply=reply, score=1.0, method="exact", matched_text=text)
			for reply, text in candidates
			if _normalize(text) == query_norm or _normalize(reply.case_id) == query_norm
		]
		if exact:
			return exact[:max(1, top_k)]

		floor = self._threshold if threshold is None else threshold
		vector_matches = await self._vector_matches(query, candidates)
		if vector_matches:
			return [match for match in vector_matches if match.score >= floor][:max(1, top_k)]

		lexical = [
			AnnotationMatch(reply=reply, score=_lexical_score(query, text), method="lexical", matched_text=text)
			for reply, text in candidates
		]
		lexical.sort(key=lambda item: item.score, reverse=True)
		return [match for match in lexical if match.score >= floor][:max(1, top_k)]

	async def _vector_matches(
		self,
		query: str,
		candidates: list[tuple[AnnotationReply, str]],
	) -> list[AnnotationMatch]:
		if not self._embedding_provider:
			return []
		try:
			vectors = await self._embedding_provider.embed([query, *[text for _, text in candidates]])
		except Exception:
			return []
		if len(vectors) != len(candidates) + 1:
			return []
		query_vec = vectors[0]
		matches = [
			AnnotationMatch(reply=reply, score=_cosine(query_vec, vector), method="vector", matched_text=text)
			for (reply, text), vector in zip(candidates, vectors[1:])
		]
		matches.sort(key=lambda item: item.score, reverse=True)
		return matches


def _reply_match_text(reply: AnnotationReply) -> str:
	metadata: dict[str, Any] = reply.metadata or {}
	for key in ("input", "query", "question", "prompt"):
		value = metadata.get(key)
		if value:
			return str(value)
	if getattr(reply, "input", ""):
		return str(reply.input)
	return reply.case_id


def _normalize(text: str) -> str:
	return re.sub(r"\s+", " ", text.strip().lower())


def _lexical_score(query: str, candidate: str) -> float:
	query_norm = _normalize(query)
	candidate_norm = _normalize(candidate)
	if not query_norm or not candidate_norm:
		return 0.0
	ratio = SequenceMatcher(None, query_norm, candidate_norm).ratio()
	query_tokens = set(query_norm.split())
	candidate_tokens = set(candidate_norm.split())
	if not query_tokens or not candidate_tokens:
		return ratio
	jaccard = len(query_tokens & candidate_tokens) / len(query_tokens | candidate_tokens)
	return max(ratio, jaccard)


def _cosine(left: list[float], right: list[float]) -> float:
	if not left or not right or len(left) != len(right):
		return 0.0
	dot = sum(a * b for a, b in zip(left, right))
	left_norm = math.sqrt(sum(a * a for a in left))
	right_norm = math.sqrt(sum(b * b for b in right))
	if left_norm == 0 or right_norm == 0:
		return 0.0
	return dot / (left_norm * right_norm)
