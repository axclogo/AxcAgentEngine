"""Tests for #32 dependency range tightening."""


class TestDependencyRanges:
	def test_httpx_range_tightened(self):
		"""Verify httpx dependency is <0.29 not <1.0."""
		import tomllib
		with open("pyproject.toml", "rb") as f:
			data = tomllib.load(f)
		deps = data["project"]["dependencies"]
		httpx_dep = next(d for d in deps if d.startswith("httpx"))
		assert "<0.29" in httpx_dep
		assert "<1.0" not in httpx_dep

	def test_pydantic_range(self):
		import tomllib
		with open("pyproject.toml", "rb") as f:
			data = tomllib.load(f)
		deps = data["project"]["dependencies"]
		pydantic_dep = next(d for d in deps if d.startswith("pydantic"))
		assert ">=2.0" in pydantic_dep
		assert "<3.0" in pydantic_dep

	def test_pyyaml_range(self):
		import tomllib
		with open("pyproject.toml", "rb") as f:
			data = tomllib.load(f)
		deps = data["project"]["dependencies"]
		yaml_dep = next(d for d in deps if d.startswith("pyyaml"))
		assert ">=6.0" in yaml_dep
