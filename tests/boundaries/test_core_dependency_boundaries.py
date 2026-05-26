"""Core dependency boundary tests for execution-kernel refactors."""
from __future__ import annotations

from pathlib import Path
import tomllib


ROOT = next(path for path in Path(__file__).resolve().parents if (path / "pyproject.toml").exists())


def _pyproject() -> dict:
	with (ROOT / "pyproject.toml").open("rb") as f:
		return tomllib.load(f)


def _dependencies() -> list[str]:
	data = _pyproject()["project"]
	deps = list(data.get("dependencies", []))
	for values in data.get("optional-dependencies", {}).values():
		deps.extend(values)
	return [dep.lower() for dep in deps]


def _source_contains(paths: list[Path], needles: tuple[str, ...]) -> list[str]:
	offenders: list[str] = []
	for root in paths:
		for path in root.rglob("*.py"):
			text = path.read_text(encoding="utf-8").lower()
			if any(needle in text for needle in needles):
				offenders.append(str(path.relative_to(ROOT)))
	return offenders


def test_large_agent_frameworks_are_not_dependencies():
	blocked = ("llama-index", "llamaindex", "langgraph", "crewai")
	deps = _dependencies()
	assert not any(any(name in dep for name in blocked) for dep in deps)


def test_pydantic_graph_is_only_planning_runtime_dependency():
	offenders = _source_contains(
		[ROOT / "axc_agent_engine/core", ROOT / "axc_agent_engine/tools", ROOT / "axc_agent_engine/llm"],
		("pydantic_graph", "pydantic-graph"),
	)
	assert offenders == []


def test_burr_is_only_workflow_adapter_dependency():
	offenders = _source_contains(
		[ROOT / "axc_agent_engine/core", ROOT / "axc_agent_engine/tools", ROOT / "axc_agent_engine/planning"],
		("burr",),
	)
	assert offenders == []


def test_react_kernel_does_not_depend_on_planning_or_workflow():
	path = ROOT / "axc_agent_engine/core/react_kernel.py"
	if not path.exists():
		return
	text = path.read_text(encoding="utf-8")
	assert "axc_agent_engine.planning" not in text
	assert "axc_agent_engine.workflow" not in text
