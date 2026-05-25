"""Tests for #13 shared cosine_similarity in utils/math_utils.py."""
from axc_agent_engine.utils.math_utils import cosine_similarity


class TestCosineSimilarity:
	def test_identical_vectors(self):
		v = [1.0, 2.0, 3.0]
		assert abs(cosine_similarity(v, v) - 1.0) < 1e-9

	def test_orthogonal_vectors(self):
		a = [1.0, 0.0]
		b = [0.0, 1.0]
		assert abs(cosine_similarity(a, b)) < 1e-9

	def test_opposite_vectors(self):
		a = [1.0, 0.0]
		b = [-1.0, 0.0]
		assert abs(cosine_similarity(a, b) - (-1.0)) < 1e-9

	def test_empty_vectors(self):
		assert cosine_similarity([], []) == 0.0

	def test_different_lengths(self):
		assert cosine_similarity([1.0, 2.0], [1.0]) == 0.0

	def test_zero_vector_a(self):
		assert cosine_similarity([0.0, 0.0], [1.0, 2.0]) == 0.0

	def test_zero_vector_b(self):
		assert cosine_similarity([1.0, 2.0], [0.0, 0.0]) == 0.0

	def test_positive_similarity(self):
		a = [1.0, 1.0]
		b = [1.0, 0.5]
		sim = cosine_similarity(a, b)
		assert 0.0 < sim < 1.0

	def test_single_element(self):
		assert abs(cosine_similarity([3.0], [5.0]) - 1.0) < 1e-9

	def test_negative_similarity(self):
		a = [1.0, 0.0]
		b = [-0.5, 0.0]
		assert cosine_similarity(a, b) < 0

	def test_none_like_empty(self):
		assert cosine_similarity([], [1.0]) == 0.0
