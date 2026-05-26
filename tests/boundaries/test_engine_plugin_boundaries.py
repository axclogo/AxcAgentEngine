"""Boundary tests for keeping optional capabilities out of the core package."""
from __future__ import annotations

import sys
import subprocess
from pathlib import Path


PLUGIN_ONLY_TOP_LEVELS = {
	"eval",
	"graph",
	"knowledge",
	"memory",
	"multi_agent",
	"output_format",
	"security",
	"simulation",
}

ROOT = next(path for path in Path(__file__).resolve().parents if (path / "pyproject.toml").exists())


def test_plugin_only_capabilities_are_not_top_level_packages():
	root = ROOT / "axc_agent_engine"
	existing = {path.name for path in root.iterdir() if path.is_dir()}

	assert PLUGIN_ONLY_TOP_LEVELS.isdisjoint(existing)


def test_importing_engine_does_not_import_builtin_plugin_packages():
	code = """
import sys
import axc_agent_engine.engine  # noqa: F401
loaded = [
    name for name in sys.modules
    if name.startswith('axc_agent_engine.plugins.builtin.')
    or name.startswith('axc_agent_engine.sidecar')
]
if loaded:
    raise SystemExit('\\n'.join(sorted(loaded)))
"""
	result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, text=True, capture_output=True)

	assert result.returncode == 0, result.stderr or result.stdout


def test_sidecar_contains_host_driven_capabilities():
	root = ROOT / "axc_agent_engine" / "sidecar"
	existing = {path.name for path in root.iterdir() if path.is_dir()}

	assert {"multi_agent", "simulation", "eval"}.issubset(existing)
