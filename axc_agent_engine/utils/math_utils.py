"""数学工具函数。"""
import math


def cosine_similarity(vec_a: list[float], vec_b: list[float]) -> float:
	"""计算两个向量的余弦相似度。"""
	if not vec_a or not vec_b or len(vec_a) != len(vec_b):
		return 0.0
	dot = sum(a * b for a, b in zip(vec_a, vec_b))
	norm_a = math.sqrt(sum(a * a for a in vec_a))
	norm_b = math.sqrt(sum(b * b for b in vec_b))
	if norm_a == 0 or norm_b == 0:
		return 0.0
	return dot / (norm_a * norm_b)
