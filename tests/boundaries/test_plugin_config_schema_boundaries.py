"""Plugin configuration schema boundary tests.
中文：插件配置 Schema 边界测试。"""
from __future__ import annotations

import ast
from pathlib import Path

from axc_agent_engine.plugins.builtin import AVAILABLE_BUILTIN_PLUGINS
from axc_agent_engine.plugins.registry import PluginRegistry


ROOT = next(path for path in Path(__file__).resolve().parents if (path / "pyproject.toml").exists())
PLUGIN_ROOT = ROOT / "axc_agent_engine" / "plugins"
MODEL_CONFIG_KEYS = {"model", "model_name", "llm", "llm_model", "provider", "provider_name"}


def test_plugin_config_schemas_do_not_expose_model_selection():
	"""Plugin YAML must not choose agent model/provider.
中文：插件 YAML 不能选择 Agent 模型或 provider。"""
	registry = PluginRegistry()
	registry.register_many(AVAILABLE_BUILTIN_PLUGINS.values())
	offenders: list[str] = []
	for plugin_name, schema in registry.list_plugin_config_schemas().items():
		for path in _field_paths(schema.fields):
			parts = [part for part in path.replace("[]", "").split(".") if part]
			if parts and parts[-1] in MODEL_CONFIG_KEYS:
				offenders.append(f"{plugin_name}.{path}")
	assert offenders == []


def test_plugins_do_not_read_model_selection_from_config():
	"""Plugin code must not read model/provider selection from YAML config.
中文：插件代码不能从 YAML config 读取模型或 provider 选择。"""
	offenders: list[str] = []
	for path in sorted(PLUGIN_ROOT.rglob("*.py")):
		tree = ast.parse(path.read_text(encoding="utf-8"))
		for node in ast.walk(tree):
			if _reads_forbidden_config_key(node):
				offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}")
	assert offenders == []


def _field_paths(fields, prefix: str = "") -> list[str]:
	paths: list[str] = []
	for field in fields:
		path = f"{prefix}.{field.key}" if prefix and field.key else field.key
		if path:
			paths.append(path)
		if field.children:
			paths.extend(_field_paths(field.children, path))
		if field.item_schema:
			item_prefix = f"{path}[]" if path else "[]"
			if field.item_schema.children:
				paths.extend(_field_paths(field.item_schema.children, item_prefix))
	return paths


def _reads_forbidden_config_key(node: ast.AST) -> bool:
	if not isinstance(node, ast.Call):
		return False
	func = node.func
	if not isinstance(func, ast.Attribute) or func.attr != "get":
		return False
	if not node.args:
		return False
	target = func.value
	if not isinstance(target, ast.Name | ast.Attribute):
		return False
	target_name = target.id if isinstance(target, ast.Name) else target.attr
	if "config" not in target_name:
		return False
	key = node.args[0]
	return isinstance(key, ast.Constant) and key.value in MODEL_CONFIG_KEYS
