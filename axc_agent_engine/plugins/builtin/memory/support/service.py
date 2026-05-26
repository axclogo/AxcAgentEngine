"""Layered memory service.
中文：此文档说明相关引擎组件的行为。"""
from __future__ import annotations

import hashlib
import json
import math
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from axc_agent_engine.plugins.builtin.memory.support.graph import GraphMemory
from axc_agent_engine.plugins.builtin.memory.support.retrieval import BM25Index, MemoryDocument, RetrievalResult, rrf_merge


class MemoryLayer(StrEnum):
	IDENTITY = "identity"
	SEMANTIC = "semantic"
	EPISODIC = "episodic"
	LESSON = "lesson"


@dataclass
class MemoryItem:
	id: str
	layer: str
	content: str
	fact_type: str = "fact"
	importance: float = 0.5
	confidence: float = 0.8
	source: str = ""
	created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
	last_accessed_at: str = ""
	access_count: int = 0
	decay_score: float = 1.0
	content_hash: str = ""
	metadata: dict[str, Any] = field(default_factory=dict)

	def __post_init__(self) -> None:
		if not self.content_hash:
			self.content_hash = hashlib.sha256(self.content.encode("utf-8")).hexdigest()

	def to_dict(self) -> dict[str, Any]:
		return {
			"id": self.id,
			"layer": self.layer,
			"content": self.content,
			"fact_type": self.fact_type,
			"importance": self.importance,
			"confidence": self.confidence,
			"source": self.source,
			"created_at": self.created_at,
			"last_accessed_at": self.last_accessed_at,
			"access_count": self.access_count,
			"decay_score": self.decay_score,
			"content_hash": self.content_hash,
			"metadata": dict(self.metadata),
		}

	@classmethod
	def from_dict(cls, data: dict[str, Any]) -> "MemoryItem":
		return cls(
			id=str(data.get("id") or uuid.uuid4()),
			layer=str(data.get("layer") or MemoryLayer.SEMANTIC),
			content=str(data.get("content") or ""),
			fact_type=str(data.get("fact_type") or "fact"),
			importance=float(data.get("importance", 0.5)),
			confidence=float(data.get("confidence", 0.8)),
			source=str(data.get("source") or ""),
			created_at=str(data.get("created_at") or datetime.now(timezone.utc).isoformat()),
			last_accessed_at=str(data.get("last_accessed_at") or ""),
			access_count=int(data.get("access_count", 0)),
			decay_score=float(data.get("decay_score", 1.0)),
			content_hash=str(data.get("content_hash") or ""),
			metadata=dict(data.get("metadata") or {}),
		)


@runtime_checkable
class MemoryStore(Protocol):
	"""Storage-neutral four-layer memory persistence.
中文：此文档说明相关引擎组件的行为。"""
	def add_item(self, item: MemoryItem) -> MemoryItem: ...
	def get_item(self, item_id: str) -> MemoryItem | None: ...
	def list_items(self, layer: str | None = None) -> list[MemoryItem]: ...
	def update_item(self, item: MemoryItem) -> MemoryItem: ...
	def delete_item(self, item_id: str) -> bool: ...


@runtime_checkable
class MemoryRetriever(Protocol):
	"""Retrieves relevant memories from a store.
中文：此文档说明相关引擎组件的行为。"""
	def retrieve(self, topic: str = "", layer: str | None = None, top_k: int = 5) -> list[MemoryItem]: ...


@runtime_checkable
class FactExtractor(Protocol):
	"""Extracts structured facts from model output or conversation text.
中文：此文档说明相关引擎组件的行为。"""
	def extract(self, text: str) -> list[dict[str, Any]]: ...


@runtime_checkable
class Deduplicator(Protocol):
	"""Finds duplicate memory records.
中文：此文档说明相关引擎组件的行为。"""
	def find_duplicate(self, content: str, candidates: list[MemoryItem], layer: str | None = None) -> MemoryItem | None: ...


@runtime_checkable
class GraphMemoryStore(Protocol):
	"""Graph memory operations used by layered memory.
中文：此文档说明相关引擎组件的行为。"""
	def search(self, query: str, limit: int = 10) -> list[dict[str, Any]]: ...


class InMemoryMemoryStore:
	"""No-database MemoryStore fallback.
中文：此文档说明相关引擎组件的行为。"""

	def __init__(self, items: list[MemoryItem] | None = None) -> None:
		self._items: dict[str, MemoryItem] = {item.id: item for item in items or []}

	def add_item(self, item: MemoryItem) -> MemoryItem:
		self._items[item.id] = item
		return item

	def get_item(self, item_id: str) -> MemoryItem | None:
		return self._items.get(item_id)

	def list_items(self, layer: str | None = None) -> list[MemoryItem]:
		return [item for item in self._items.values() if not layer or item.layer == str(layer)]

	def update_item(self, item: MemoryItem) -> MemoryItem:
		if item.id not in self._items:
			raise KeyError(item.id)
		self._items[item.id] = item
		return item

	def delete_item(self, item_id: str) -> bool:
		return self._items.pop(item_id, None) is not None

	def replace_all(self, items: list[MemoryItem]) -> None:
		self._items = {item.id: item for item in items}


class JsonFactExtractor:
	"""FactExtractor backed by the engine's JSON fact response parser.
中文：此文档说明相关引擎组件的行为。"""

	def extract(self, text: str) -> list[dict[str, Any]]:
		return parse_facts_response(text)


class SimilarityDeduplicator:
	"""Character-shingle deduplicator used by the fallback memory service.
中文：此文档说明相关引擎组件的行为。"""

	def __init__(self, threshold: float = 0.85) -> None:
		self.threshold = threshold

	def find_duplicate(self, content: str, candidates: list[MemoryItem], layer: str | None = None) -> MemoryItem | None:
		for item in candidates:
			if layer and item.layer != str(layer):
				continue
			if char_similarity(content, item.content) >= self.threshold:
				return item
		return None


class MemoryService:
	"""Four-layer memory with deduplication, decay, BM25 retrieval, and graph hooks.
中文：此文档说明相关引擎组件的行为。"""

	def __init__(self, dedup_threshold: float = 0.85, decay_half_life_days: int = 7,
				 graph: GraphMemory | None = None,
				 store: MemoryStore | None = None,
				 deduplicator: Deduplicator | None = None,
				 fact_extractor: FactExtractor | None = None) -> None:
		self.dedup_threshold = dedup_threshold
		self.decay_half_life_days = max(1, decay_half_life_days)
		self.graph = graph or GraphMemory()
		self.store = store or InMemoryMemoryStore()
		self.deduplicator = deduplicator or SimilarityDeduplicator(dedup_threshold)
		self.fact_extractor = fact_extractor or JsonFactExtractor()
		self._bm25: dict[str, BM25Index] = {}
		self._dirty_layers: set[str] = set()

	@property
	def items(self) -> list[MemoryItem]:
		return self.store.list_items()

	@items.setter
	def items(self, value: list[MemoryItem]) -> None:
		if isinstance(self.store, InMemoryMemoryStore):
			self.store.replace_all(value)
			return
		current = self.store.list_items()
		for item in current:
			self.store.delete_item(item.id)
		for item in value:
			self.store.add_item(item)

	def add(self, content: str, layer: str = MemoryLayer.SEMANTIC,
			fact_type: str = "fact", importance: float = 0.5,
			confidence: float = 0.8, source: str = "",
			metadata: dict[str, Any] | None = None) -> MemoryItem:
		content = content.strip()
		if not content:
			raise ValueError("memory content cannot be empty")
		duplicate = self.find_duplicate(content, layer)
		if duplicate:
			self.merge(duplicate, content, importance)
			return duplicate
		item = MemoryItem(
			id=str(uuid.uuid4()),
			layer=str(layer),
			content=content,
			fact_type=fact_type,
			importance=_normalize_importance(importance),
			confidence=max(0.0, min(float(confidence), 1.0)),
			source=source,
			metadata=metadata or {},
		)
		self.store.add_item(item)
		self._dirty_layers.add(item.layer)
		return item

	def merge(self, existing: MemoryItem, new_content: str, new_importance: float) -> None:
		if len(new_content) > len(existing.content):
			existing.content = new_content
			existing.content_hash = hashlib.sha256(new_content.encode("utf-8")).hexdigest()
		existing.importance = max(existing.importance, _normalize_importance(new_importance))
		existing.confidence = min(existing.confidence + 0.05, 1.0)
		existing.access_count += 1
		existing.last_accessed_at = datetime.now(timezone.utc).isoformat()
		self._dirty_layers.add(existing.layer)

	def find_duplicate(self, content: str, layer: str | None = None) -> MemoryItem | None:
		return self.deduplicator.find_duplicate(content, self.items, layer)

	def retrieve(self, topic: str = "", layer: str | None = None, top_k: int = 5) -> list[MemoryItem]:
		candidates = [item for item in self.items if not layer or item.layer == str(layer)]
		if not candidates:
			return []
		self.decay()
		if topic:
			results = self.hybrid_search(topic, layer=layer, top_k=top_k)
			if results:
				ids = [result.id for result in results]
				by_id = {item.id: item for item in candidates}
				return [self._touch(by_id[item_id]) for item_id in ids if item_id in by_id]
		candidates.sort(key=lambda item: (item.importance, item.decay_score, item.access_count), reverse=True)
		return [self._touch(item) for item in candidates[:top_k]]

	def hybrid_search(self, query: str, layer: str | None = None, top_k: int = 10) -> list[RetrievalResult]:
		layers = [str(layer)] if layer else sorted({item.layer for item in self.items})
		merged_inputs: list[list[RetrievalResult]] = []
		for current_layer in layers:
			index = self._index_for(current_layer)
			results = index.search(query, top_k=top_k * 3)
			weighted = []
			by_id = {item.id: item for item in self.items if item.layer == current_layer}
			for result in results:
				item = by_id.get(result.id)
				if not item:
					continue
				score = result.score + 0.3 * item.importance + 0.2 * item.decay_score
				weighted.append(RetrievalResult(
					id=result.id,
					text=result.text,
					score=score,
					retrieval=result.retrieval,
					source=result.source,
					metadata=result.metadata,
				))
			merged_inputs.append(weighted)
		graph_ids = [row.get("source_memory_id") for row in self.graph.search(query, limit=top_k * 2)]
		graph_results = [
			RetrievalResult(id=str(memory_id), text="", score=1.0, retrieval="graph")
			for memory_id in graph_ids if memory_id
		]
		if graph_results:
			merged_inputs.append(graph_results)
		return rrf_merge(*merged_inputs, top_k=top_k) if merged_inputs else []

	def decay(self) -> None:
		now = datetime.now(timezone.utc)
		for item in self.items:
			if item.layer in (MemoryLayer.IDENTITY, MemoryLayer.LESSON):
				item.decay_score = 1.0
				continue
			try:
				base = datetime.fromisoformat(item.last_accessed_at or item.created_at)
			except ValueError:
				base = now
			days = max(0.0, (now - base).total_seconds() / 86400)
			half_life = 30 if item.layer == MemoryLayer.SEMANTIC else self.decay_half_life_days
			item.decay_score = math.pow(0.5, days / max(1, half_life))

	def remove_decayed(self, threshold: float = 0.05) -> list[str]:
		self.decay()
		removed = [item.id for item in self.items if item.layer == MemoryLayer.EPISODIC and item.decay_score < threshold]
		if removed:
			self.items = [item for item in self.items if item.id not in set(removed)]
			self._bm25.clear()
			self._dirty_layers.clear()
		return removed

	def build_context(self, topic: str = "", budget_chars: int = 4000) -> str:
		parts: list[str] = []
		remaining = budget_chars
		sections = [
			("【自我认知】", MemoryLayer.IDENTITY, 3),
			("【经验教训】", MemoryLayer.LESSON, 5),
			("【相关知识】", MemoryLayer.SEMANTIC, 5),
			("【近期相关事件】", MemoryLayer.EPISODIC, 3),
		]
		for title, layer, top_k in sections:
			items = self.retrieve(topic, layer=layer, top_k=top_k)
			if not items:
				continue
			text = title + "\n" + "\n".join(f"- {item.content}" for item in items)
			if len(text) > remaining and parts:
				continue
			parts.append(text[:remaining])
			remaining -= len(text)
			if remaining <= 0:
				break
		if topic and remaining > 200:
			graph_lines = [
				f"- {row['source']} --[{row['relation_type']}]--> {row['target']}: {row['description']}"
				for row in self.graph.search(topic, limit=5)
			]
			if graph_lines:
				parts.append("【实体关系】\n" + "\n".join(graph_lines))
		return "\n\n".join(parts)

	def dump(self) -> list[dict[str, Any]]:
		return [item.to_dict() for item in self.items]

	def load(self, records: list[dict[str, Any]]) -> None:
		self.items = [MemoryItem.from_dict(record) for record in records if record.get("content")]
		self._bm25.clear()
		self._dirty_layers = {item.layer for item in self.items}

	def _index_for(self, layer: str) -> BM25Index:
		if layer not in self._bm25 or layer in self._dirty_layers:
			docs = [
				MemoryDocument(id=item.id, text=item.content, metadata={"layer": item.layer, "fact_type": item.fact_type})
				for item in self.items if item.layer == layer
			]
			self._bm25[layer] = BM25Index(docs)
			self._dirty_layers.discard(layer)
		return self._bm25[layer]

	def _touch(self, item: MemoryItem) -> MemoryItem:
		item.access_count += 1
		item.last_accessed_at = datetime.now(timezone.utc).isoformat()
		return item


def parse_facts_response(text: str) -> list[dict[str, Any]]:
	"""Parse JSON fact extraction responses.
中文：此文档说明相关引擎组件的行为。"""
	if not text:
		return []
	text = text.strip()
	if text.startswith("```"):
		lines = text.split("\n")
		text = "\n".join(lines[1:-1] if lines and lines[-1].strip() == "```" else lines[1:])
	start = text.find("[")
	end = text.rfind("]")
	if start < 0 or end <= start:
		return []
	try:
		raw = json.loads(text[start:end + 1])
	except (json.JSONDecodeError, TypeError):
		return []
	if not isinstance(raw, list):
		return []
	facts = []
	for item in raw:
		if not isinstance(item, dict) or not item.get("content"):
			continue
		facts.append({
			"type": item.get("type", "fact"),
			"content": str(item["content"]).strip(),
			"importance": _normalize_importance(item.get("importance", 0.5)),
		})
	return facts


def char_similarity(a: str, b: str) -> float:
	if not a or not b:
		return 0.0
	if len(a) < 2 or len(b) < 2:
		set_a, set_b = set(a), set(b)
	else:
		set_a = {a[i:i + 2] for i in range(len(a) - 1)}
		set_b = {b[i:i + 2] for i in range(len(b) - 1)}
	union = len(set_a | set_b)
	return len(set_a & set_b) / union if union else 0.0


def _normalize_importance(value: Any) -> float:
	try:
		number = float(value)
	except (TypeError, ValueError):
		return 0.5
	if number > 1:
		number = number / 10.0
	return max(0.0, min(number, 1.0))
