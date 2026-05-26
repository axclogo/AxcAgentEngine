from datetime import datetime, timedelta, timezone

from axc_agent_engine.plugins.builtin.memory.support import (
	InMemoryMemoryStore,
	JsonFactExtractor,
	MemoryLayer,
	MemoryService,
	SimilarityDeduplicator,
)
from axc_agent_engine.plugins.builtin.memory.support.service import parse_facts_response


def test_memory_service_builds_layered_context():
	service = MemoryService()
	service.add("The assistant is a careful engineering agent", layer=MemoryLayer.IDENTITY, importance=1.0)
	service.add("User prefers concise explanations", layer=MemoryLayer.SEMANTIC, importance=0.9)
	service.add("Avoid repeating failed migration steps", layer=MemoryLayer.LESSON, importance=1.0)
	context = service.build_context("preferences", budget_chars=2000)
	assert "【自我认知】" in context
	assert "【经验教训】" in context
	assert "concise" in context


def test_memory_service_removes_decayed_episodic_memory():
	service = MemoryService(decay_half_life_days=1)
	item = service.add("Old transient event", layer=MemoryLayer.EPISODIC, importance=0.1)
	item.last_accessed_at = (datetime.now(timezone.utc) - timedelta(days=20)).isoformat()
	removed = service.remove_decayed(threshold=0.05)
	assert item.id in removed


def test_parse_json_facts_response():
	facts = parse_facts_response('[{"type":"preference","content":"User likes Python","importance":8}]')
	assert facts == [{"type": "preference", "content": "User likes Python", "importance": 0.8}]


def test_memory_protocol_fallback_store_and_deduplicator():
	store = InMemoryMemoryStore()
	service = MemoryService(store=store, deduplicator=SimilarityDeduplicator(threshold=0.8))
	first = service.add("User prefers compact Python examples", layer=MemoryLayer.SEMANTIC)
	second = service.add("User prefers compact Python examples.", layer=MemoryLayer.SEMANTIC)
	assert second.id == first.id
	assert store.get_item(first.id) is first
	assert JsonFactExtractor().extract('[{"content":"A","importance":1}]')[0]["content"] == "A"
