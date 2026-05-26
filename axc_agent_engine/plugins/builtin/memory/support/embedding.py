"""Embedding helpers private to the memory plugin.
中文：此文档说明相关引擎组件的行为。"""
from __future__ import annotations

import math


class HashEmbeddingClient:
	"""Dependency-free deterministic embedding fallback for local memory vectors.
中文：此文档说明相关引擎组件的行为。"""

	def __init__(self, dimensions: int = 256) -> None:
		self.dimensions = max(8, dimensions)

	async def embed(self, texts: list[str]) -> list[list[float]]:
		return [_hash_embedding(text, self.dimensions) for text in texts]


class OpenAICompatibleEmbeddingClient:
	"""Small OpenAI-compatible /embeddings client used when configured by memory.
中文：此文档说明相关引擎组件的行为。"""

	def __init__(self, base_url: str, model: str, api_key: str = "", timeout: int = 30) -> None:
		if not base_url:
			raise ValueError("base_url is required")
		if not model:
			raise ValueError("model is required")
		self.base_url = base_url.rstrip("/")
		self.model = model
		self.api_key = api_key
		self.timeout = timeout

	async def embed(self, texts: list[str]) -> list[list[float]]:
		if not texts:
			return []
		import httpx
		headers = {"Content-Type": "application/json"}
		if self.api_key:
			headers["Authorization"] = f"Bearer {self.api_key}"
		async with httpx.AsyncClient(timeout=self.timeout) as client:
			response = await client.post(
				f"{self.base_url}/embeddings",
				headers=headers,
				json={"model": self.model, "input": texts},
			)
			response.raise_for_status()
			data = response.json()
			return [item["embedding"] for item in data.get("data", [])]


def _hash_embedding(text: str, dimensions: int) -> list[float]:
	vector = [0.0] * dimensions
	for token in text.lower().split():
		index = hash(token) % dimensions
		vector[index] += 1.0
	norm = math.sqrt(sum(value * value for value in vector))
	return [value / norm for value in vector] if norm else vector
