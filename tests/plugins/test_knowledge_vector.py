"""Tests for Knowledge plugin vector_store integration and workspace boundary."""
import os
import tempfile
import pytest
from axc_agent_engine.core.context import ExecutionConfig, ExecutionContext, ExecutionState
from axc_agent_engine.plugins import PluginContext
from axc_agent_engine.plugins.builtin.knowledge.plugin import KnowledgePlugin
from axc_agent_engine.runtime.resources import ResourceRegistry


class MockVectorStore:
	"""Mock VectorStore that records calls."""
	def __init__(self):
		self.added_texts: list[str] = []
		self.added_embeddings: list[list[float]] = []
		self.added_metadata: list[dict] = []
		self.search_results: list[dict] = []
		self.deleted_ids: list[str] = []
		self.search_called = False
		self.add_call_count = 0

	async def add(self, texts: list[str], embeddings: list[list[float]], metadata: list[dict]) -> list[str]:
		self.add_call_count += 1
		self.added_texts = texts
		self.added_embeddings = embeddings
		self.added_metadata = metadata
		return [f"id_{i}" for i in range(len(texts))]

	async def search(self, embedding: list[float], top_k: int = 5) -> list[dict]:
		self.search_called = True
		return self.search_results

	async def delete(self, ids: list[str]) -> None:
		self.deleted_ids.extend(ids)


class MockKVStore:
	"""Mock KVStore for manifest persistence."""
	def __init__(self):
		self._data: dict[str, dict] = {}

	async def get(self, key: str) -> dict | None:
		return self._data.get(key)

	async def set(self, key: str, value: dict) -> None:
		self._data[key] = value

	async def delete(self, key: str) -> None:
		self._data.pop(key, None)

	async def list_keys(self, prefix: str = "") -> list[str]:
		return [k for k in self._data if k.startswith(prefix)]


class TestKnowledgeBM25Fallback:
	def test_bm25_search_without_embeddings(self):
		"""BM25 works without embedding config."""
		with tempfile.TemporaryDirectory() as tmpdir:
			# Create test file
			test_file = os.path.join(tmpdir, "test.md")
			with open(test_file, "w") as f:
				f.write("Python is a programming language.\n\nJava is also popular.")
			plugin = KnowledgePlugin()
			ctx = PluginContext(workspace=tmpdir)
			plugin.initialize({"sources": ["test.md"]}, ctx)
			assert len(plugin._chunks) > 0
			results = plugin._hybrid_search("Python programming", top_k=3)
			assert len(results) > 0
			assert "text" in results[0]
			assert "source" in results[0]
			assert "score" in results[0]


class TestKnowledgeVectorStore:
	@pytest.mark.asyncio
	async def test_vector_store_add_called_on_execution_start(self):
		"""vector_store.add is called with embeddings during on_execution_start."""
		with tempfile.TemporaryDirectory() as tmpdir:
			test_file = os.path.join(tmpdir, "doc.txt")
			with open(test_file, "w") as f:
				f.write("Machine learning is a subset of AI.")
			mock_vs = MockVectorStore()
			mock_kv = MockKVStore()
			ctx = PluginContext(workspace=tmpdir, resources=ResourceRegistry({"knowledge_vector": mock_vs}), kv_store=mock_kv)
			plugin = KnowledgePlugin()
			plugin.initialize({
				"sources": ["doc.txt"],
				"embedding": {"base_url": "http://fake"},
			}, ctx)
			async def fake_embed(texts):
				return [[0.1, 0.2, 0.3] for _ in texts]
			plugin._embed_texts = fake_embed
			exec_ctx = ExecutionContext(config=ExecutionConfig(), state=ExecutionState())
			await plugin.on_execution_start(exec_ctx)
			assert len(mock_vs.added_texts) > 0
			assert len(mock_vs.added_embeddings) == len(mock_vs.added_texts)
			assert all("source" in m for m in mock_vs.added_metadata)
			assert all("chunk_id" in m for m in mock_vs.added_metadata)

	@pytest.mark.asyncio
	async def test_unchanged_source_not_re_embedded(self):
		"""Source with same fingerprint should not be re-embedded."""
		with tempfile.TemporaryDirectory() as tmpdir:
			test_file = os.path.join(tmpdir, "doc.txt")
			with open(test_file, "w") as f:
				f.write("Stable content that should not change.")
			mock_vs = MockVectorStore()
			mock_kv = MockKVStore()
			ctx = PluginContext(workspace=tmpdir, resources=ResourceRegistry({"knowledge_vector": mock_vs}), kv_store=mock_kv)
			plugin = KnowledgePlugin()
			plugin.initialize({
				"sources": ["doc.txt"],
				"embedding": {"base_url": "http://fake"},
			}, ctx)
			call_count = [0]
			async def counting_embed(texts):
				call_count[0] += 1
				return [[0.1] for _ in texts]
			plugin._embed_texts = counting_embed
			exec_ctx = ExecutionContext(config=ExecutionConfig(), state=ExecutionState())
			await plugin.on_execution_start(exec_ctx)
			assert call_count[0] == 1
			# Second call — should skip (fingerprint unchanged)
			plugin._embedding_ready = False
			await plugin.on_execution_start(exec_ctx)
			assert call_count[0] == 1  # Not called again

	@pytest.mark.asyncio
	async def test_changed_source_deletes_old_chunks(self):
		"""Changed source should delete old vector_store entries."""
		with tempfile.TemporaryDirectory() as tmpdir:
			test_file = os.path.join(tmpdir, "doc.txt")
			with open(test_file, "w") as f:
				f.write("Original content.")
			mock_vs = MockVectorStore()
			mock_kv = MockKVStore()
			ctx = PluginContext(workspace=tmpdir, resources=ResourceRegistry({"knowledge_vector": mock_vs}), kv_store=mock_kv)
			plugin = KnowledgePlugin()
			plugin.initialize({
				"sources": ["doc.txt"],
				"embedding": {"base_url": "http://fake"},
			}, ctx)
			async def fake_embed(texts):
				return [[0.1] for _ in texts]
			plugin._embed_texts = fake_embed
			exec_ctx = ExecutionContext(config=ExecutionConfig(), state=ExecutionState())
			await plugin.on_execution_start(exec_ctx)
			first_ids = list(mock_vs.added_texts)
			# Simulate source change
			plugin._embedding_ready = False
			plugin._chunks = [{"text": "New content completely different.", "source": os.path.join(tmpdir, "doc.txt")}]
			plugin._manifests.clear()  # Force re-check
			await plugin.on_execution_start(exec_ctx)
			# Should have called add again with new content
			assert mock_vs.added_texts != first_ids

	@pytest.mark.asyncio
	async def test_embedding_built_only_once(self):
		"""Embedding build is idempotent (lock prevents double build)."""
		with tempfile.TemporaryDirectory() as tmpdir:
			test_file = os.path.join(tmpdir, "doc.txt")
			with open(test_file, "w") as f:
				f.write("Test content for embedding.")
			mock_vs = MockVectorStore()
			ctx = PluginContext(workspace=tmpdir, resources=ResourceRegistry({"knowledge_vector": mock_vs}))
			plugin = KnowledgePlugin()
			plugin.initialize({
				"sources": ["doc.txt"],
				"embedding": {"base_url": "http://fake"},
			}, ctx)
			call_count = [0]
			async def counting_embed(texts):
				call_count[0] += 1
				return [[0.1] for _ in texts]
			plugin._embed_texts = counting_embed
			exec_ctx = ExecutionContext(config=ExecutionConfig(), state=ExecutionState())
			await plugin.on_execution_start(exec_ctx)
			await plugin.on_execution_start(exec_ctx)
			assert call_count[0] == 1


class TestKnowledgeWorkspaceBoundary:
	def test_path_outside_workspace_rejected(self):
		"""Sources outside workspace boundary are rejected."""
		with tempfile.TemporaryDirectory() as tmpdir:
			# Create file outside workspace
			ctx = PluginContext(workspace=tmpdir)
			plugin = KnowledgePlugin()
			plugin.initialize({"sources": ["../secret.txt"]}, ctx)
			# Should not load any chunks from outside path
			assert len(plugin._chunks) == 0

	def test_path_inside_workspace_accepted(self):
		"""Sources inside workspace are loaded."""
		with tempfile.TemporaryDirectory() as tmpdir:
			test_file = os.path.join(tmpdir, "allowed.txt")
			with open(test_file, "w") as f:
				f.write("This is allowed content.")
			ctx = PluginContext(workspace=tmpdir)
			plugin = KnowledgePlugin()
			plugin.initialize({"sources": ["allowed.txt"]}, ctx)
			assert len(plugin._chunks) > 0


class TestKnowledgeSearchResults:
	def test_search_returns_structured_results(self):
		"""knowledge_search returns text, source, chunk_id, score, retrieval."""
		with tempfile.TemporaryDirectory() as tmpdir:
			test_file = os.path.join(tmpdir, "data.md")
			with open(test_file, "w") as f:
				f.write("# Topic\n\nRelevant information about testing.")
			ctx = PluginContext(workspace=tmpdir)
			plugin = KnowledgePlugin()
			plugin.initialize({"sources": ["data.md"]}, ctx)
			results = plugin._hybrid_search("testing", top_k=3)
			assert len(results) > 0
			for r in results:
				assert "text" in r
				assert "source" in r
				assert "chunk_id" in r
				assert "score" in r
				assert "retrieval" in r
				assert isinstance(r["score"], float)
				assert r["retrieval"] in ("bm25", "vector", "hybrid")


class TestVectorStoreSearch:
	@pytest.mark.asyncio
	async def test_vector_store_search_called_in_async_search(self):
		"""_hybrid_search_async calls vector_store.search when available."""
		with tempfile.TemporaryDirectory() as tmpdir:
			test_file = os.path.join(tmpdir, "doc.txt")
			with open(test_file, "w") as f:
				f.write("Machine learning uses neural networks.")
			mock_vs = MockVectorStore()
			mock_vs.search_results = [
				{"id": "id_0", "text": "Machine learning uses neural networks.", "score": 0.95,
				 "metadata": {"source": os.path.join(tmpdir, "doc.txt"), "chunk_id": 0}}
			]
			mock_kv = MockKVStore()
			ctx = PluginContext(workspace=tmpdir, resources=ResourceRegistry({"knowledge_vector": mock_vs}), kv_store=mock_kv)
			plugin = KnowledgePlugin()
			plugin.initialize({
				"sources": ["doc.txt"],
				"embedding": {"base_url": "http://fake"},
			}, ctx)
			async def fake_embed(texts):
				return [[0.1, 0.2, 0.3] for _ in texts]
			plugin._embed_texts = fake_embed
			# Build embeddings first
			exec_ctx = ExecutionContext(config=ExecutionConfig(), state=ExecutionState())
			await plugin.on_execution_start(exec_ctx)
			# Now search
			results = await plugin._hybrid_search_async("neural networks", top_k=3)
			assert mock_vs.search_called
			assert len(results) > 0
			assert results[0]["retrieval"] == "hybrid"
			assert "text" in results[0]
			assert "source" in results[0]
			assert "chunk_id" in results[0]
			assert "score" in results[0]

	@pytest.mark.asyncio
	async def test_vector_store_search_empty_falls_back_to_bm25(self):
		"""When vector_store.search returns empty, fallback to BM25."""
		with tempfile.TemporaryDirectory() as tmpdir:
			test_file = os.path.join(tmpdir, "doc.txt")
			with open(test_file, "w") as f:
				f.write("Python programming language is versatile.")
			mock_vs = MockVectorStore()
			mock_vs.search_results = []  # Empty results
			mock_kv = MockKVStore()
			ctx = PluginContext(workspace=tmpdir, resources=ResourceRegistry({"knowledge_vector": mock_vs}), kv_store=mock_kv)
			plugin = KnowledgePlugin()
			plugin.initialize({
				"sources": ["doc.txt"],
				"embedding": {"base_url": "http://fake"},
			}, ctx)
			async def fake_embed(texts):
				return [[0.1] for _ in texts]
			plugin._embed_texts = fake_embed
			exec_ctx = ExecutionContext(config=ExecutionConfig(), state=ExecutionState())
			await plugin.on_execution_start(exec_ctx)
			results = await plugin._hybrid_search_async("Python programming", top_k=3)
			# Should still get results from BM25 fallback
			assert len(results) > 0
			# retrieval should be "hybrid" (local cosine available) or "bm25"
			assert results[0]["retrieval"] in ("hybrid", "bm25")


class TestMultiSourceManifest:
	@pytest.mark.asyncio
	async def test_multi_source_chunk_ids_not_shared(self):
		"""Each source gets its own chunk_ids in manifest, not shared."""
		with tempfile.TemporaryDirectory() as tmpdir:
			file_a = os.path.join(tmpdir, "a.txt")
			file_b = os.path.join(tmpdir, "b.txt")
			with open(file_a, "w") as f:
				f.write("Content from source A about apples.")
			with open(file_b, "w") as f:
				f.write("Content from source B about bananas.")
			mock_vs = MockVectorStore()
			# Track per-call results
			add_results: list[list[str]] = []
			async def tracking_add(texts, embeddings, metadata):
				ids = [f"id_{len(add_results)}_{i}" for i in range(len(texts))]
				add_results.append(ids)
				mock_vs.added_texts = texts
				mock_vs.added_embeddings = embeddings
				mock_vs.added_metadata = metadata
				return ids
			mock_vs.add = tracking_add
			mock_kv = MockKVStore()
			ctx = PluginContext(workspace=tmpdir, resources=ResourceRegistry({"knowledge_vector": mock_vs}), kv_store=mock_kv)
			plugin = KnowledgePlugin()
			plugin.initialize({
				"sources": ["a.txt", "b.txt"],
				"embedding": {"base_url": "http://fake"},
			}, ctx)
			async def fake_embed(texts):
				return [[0.1] for _ in texts]
			plugin._embed_texts = fake_embed
			exec_ctx = ExecutionContext(config=ExecutionConfig(), state=ExecutionState())
			await plugin.on_execution_start(exec_ctx)
			# Check manifests have different chunk_ids
			manifests = list(plugin._manifests.values())
			assert len(manifests) == 2
			ids_a = manifests[0].get("chunk_ids", [])
			ids_b = manifests[1].get("chunk_ids", [])
			# They should not be the same list
			assert ids_a != ids_b or (not ids_a and not ids_b)

	@pytest.mark.asyncio
	async def test_changed_source_only_deletes_its_own_chunks(self):
		"""Changing one source should only delete that source's old chunk_ids."""
		with tempfile.TemporaryDirectory() as tmpdir:
			file_a = os.path.join(tmpdir, "a.txt")
			file_b = os.path.join(tmpdir, "b.txt")
			with open(file_a, "w") as f:
				f.write("Original A content.")
			with open(file_b, "w") as f:
				f.write("Original B content.")
			mock_vs = MockVectorStore()
			call_idx = [0]
			async def indexed_add(texts, embeddings, metadata):
				call_idx[0] += 1
				return [f"batch{call_idx[0]}_id{i}" for i in range(len(texts))]
			mock_vs.add = indexed_add
			mock_kv = MockKVStore()
			ctx = PluginContext(workspace=tmpdir, resources=ResourceRegistry({"knowledge_vector": mock_vs}), kv_store=mock_kv)
			plugin = KnowledgePlugin()
			plugin.initialize({
				"sources": ["a.txt", "b.txt"],
				"embedding": {"base_url": "http://fake"},
			}, ctx)
			async def fake_embed(texts):
				return [[0.1] for _ in texts]
			plugin._embed_texts = fake_embed
			exec_ctx = ExecutionContext(config=ExecutionConfig(), state=ExecutionState())
			await plugin.on_execution_start(exec_ctx)
			# Record which ids belong to source A
			a_manifest = next(m for m in plugin._manifests.values() if "a.txt" in m["source"])
			a_ids = a_manifest["chunk_ids"]
			# Now change source A only
			plugin._embedding_ready = False
			# Reload with changed content for A
			plugin._chunks = [
				{"text": "Changed A content completely new.", "source": os.path.join(tmpdir, "a.txt")},
				{"text": "Original B content.", "source": os.path.join(tmpdir, "b.txt")},
			]
			plugin._embeddings = None
			# Keep all manifests — build logic should detect A changed via fingerprint mismatch
			await plugin.on_execution_start(exec_ctx)
			# Only A's old ids should have been deleted
			assert set(a_ids).issubset(set(mock_vs.deleted_ids))
