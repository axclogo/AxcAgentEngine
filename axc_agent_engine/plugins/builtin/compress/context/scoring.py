"""Scoring helpers for relevance recall."""
from __future__ import annotations

import re
from typing import Any


def keyword_score(query: str, text: str) -> float:
	query_terms = set(_terms(query))
	if not query_terms:
		return 0.0
	text_terms = set(_terms(text))
	return len(query_terms & text_terms) / len(query_terms)


def recency_score(index: int, total: int) -> float:
	if total <= 1:
		return 1.0
	return max(0.0, min(1.0, index / (total - 1)))


def importance_score(message: dict[str, Any]) -> float:
	if message.get("pinned"):
		return 1.0
	if message.get("role") == "tool":
		return 0.7
	content = str(message.get("content", ""))
	return 0.8 if len(content) > 400 else 0.4


def _terms(text: str) -> list[str]:
	text = text.lower()
	words = re.findall(r"[a-z0-9]+", text)
	chinese = re.findall(r"[\u4e00-\u9fff]", text)
	return words + chinese
