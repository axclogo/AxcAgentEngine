"""L4 relevance recall.
中文：此文档说明相关引擎组件的行为。"""
from __future__ import annotations

import inspect
import asyncio
import threading
from typing import Any

from axc_agent_engine.plugins.builtin.compress.context.models import RecallItem
from axc_agent_engine.plugins.builtin.compress.context.scoring import importance_score, keyword_score, recency_score


def fallback_recall(messages: list[dict[str, Any]], query: str, top_k: int, token_limit: int) -> list[RecallItem]:
	"""Keyword + recency fallback recall.
中文：此文档说明相关引擎组件的行为。"""
	items: list[RecallItem] = []
	total = len(messages)
	used_tokens = 0
	for index, message in enumerate(messages):
		if message.get("role") == "system":
			continue
		text = str(message.get("content", ""))
		score = _combined_score(query, text, message, index, total)
		if score <= 0:
			continue
		cost = int(message.get("token_estimate", max(1, len(text) // 4)))
		if used_tokens + cost > token_limit:
			continue
		used_tokens += cost
		items.append(RecallItem(text=text, score=score, message=message))
	return sorted(items, key=lambda item: item.score, reverse=True)[:top_k]


def read_recall_resource(resource: Any, query: str, top_k: int) -> list[RecallItem]:
	"""Read from sync or async recall resources.
中文：此文档说明相关引擎组件的行为。"""
	if resource is None or not hasattr(resource, "search"):
		return []
	result = resource.search(query, top_k=top_k)
	if inspect.isawaitable(result):
		result = _await_sync(result)
	return [_to_item(item) for item in result or []]


async def write_recall_resource(resource: Any, texts: list[str], metadata: list[dict[str, Any]]) -> None:
	if resource is None or not hasattr(resource, "add_texts") or not texts:
		return
	result = resource.add_texts(texts, metadata)
	if inspect.isawaitable(result):
		await result


def _combined_score(query: str, text: str, message: dict[str, Any], index: int, total: int) -> float:
	return (
		keyword_score(query, text) * 0.45
		+ recency_score(index, total) * 0.25
		+ importance_score(message) * 0.25
		+ (1.0 if message.get("pinned") else 0.0) * 0.05
	)


def _to_item(raw: Any) -> RecallItem:
	if isinstance(raw, RecallItem):
		return raw
	if isinstance(raw, dict):
		return RecallItem(
			text=str(raw.get("text") or raw.get("content") or ""),
			score=float(raw.get("score", 0.0)),
			metadata=dict(raw.get("metadata", {})),
		)
	return RecallItem(text=str(raw), score=0.0)


def _await_sync(awaitable: Any) -> Any:
	"""Run an awaitable from sync plugin hooks, including inside active event loops.
中文：此文档说明相关引擎组件的行为。"""
	try:
		asyncio.get_running_loop()
	except RuntimeError:
		return asyncio.run(awaitable)
	box: dict[str, Any] = {}

	def runner() -> None:
		try:
			box["result"] = asyncio.run(awaitable)
		except Exception as exc:
			box["error"] = exc

	thread = threading.Thread(target=runner)
	thread.start()
	thread.join()
	if "error" in box:
		raise box["error"]
	return box.get("result")
