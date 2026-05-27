"""Source text policy tests for comments and model-facing prompts."""
from __future__ import annotations

import ast
import io
import re
import tokenize
from pathlib import Path


ROOT = next(path for path in Path(__file__).resolve().parents if (path / "pyproject.toml").exists())
SOURCE_ROOT = ROOT / "axc_agent_engine"
CHINESE_RE = re.compile(r"[\u4e00-\u9fff]")
ENGLISH_RE = re.compile(r"[A-Za-z]{3,}")
COMMENT_SKIP_PREFIXES = (
	"type:",
	"type ignore",
	"noqa",
	"pragma:",
	"fmt:",
	"mypy:",
	"pyright:",
	"pylint:",
	"ruff:",
	"coding",
	"!",
)
ENGLISH_PROMPT_PHRASES = (
	"You are acting",
	"Return exactly",
	"Generate a structured",
	"Summarize the tool",
	"Return only JSON",
	"Rewrite the following",
	"Rewrite the retrieval",
	"Score each document",
	"Tool activity:",
	"Content:",
	"Schema:",
	"Template:",
	"Requirements:",
	"[plugin_context]",
	"[COMPRESSED:",
	"[recalled context]",
	"[context truncated]",
	"[restored file cache]",
	"[compressed recall]",
	"[tool result compacted]",
	"[Error]",
	"[artifacts:",
	"...[omitted",
)
MODEL_TEXT_KEYS = {"description", "content"}
MODEL_TEXT_NAMES = {"prompt", "system_prompt"}


def _has_bilingual_text(text: str) -> bool:
	english = ENGLISH_RE.search(text)
	chinese = CHINESE_RE.search(text)
	return bool(english and chinese and english.start() < chinese.start())


def _python_files() -> list[Path]:
	return sorted(SOURCE_ROOT.rglob("*.py"))


def test_comments_and_docstrings_are_english_first_bilingual():
	offenders: list[str] = []
	for path in _python_files():
		text = path.read_text(encoding="utf-8")
		tree = ast.parse(text)
		for node in ast.walk(tree):
			if not isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
				continue
			doc = ast.get_docstring(node, clean=False)
			if doc and not _has_bilingual_text(doc):
				offenders.append(f"{path.relative_to(ROOT)}:{getattr(node, 'lineno', 1)} docstring")
		tokens = tokenize.generate_tokens(io.StringIO(text).readline)
		for token in tokens:
			if token.type != tokenize.COMMENT:
				continue
			comment = token.string[1:].strip()
			if not comment or comment.lower().startswith(COMMENT_SKIP_PREFIXES):
				continue
			if not _has_bilingual_text(comment):
				offenders.append(f"{path.relative_to(ROOT)}:{token.start[0]} comment")
	assert offenders == []


def test_hardcoded_model_prompts_are_chinese():
	offenders: list[str] = []
	for path in _python_files():
		text = "\n".join(_all_string_literals(ast.parse(path.read_text(encoding="utf-8"))))
		for phrase in ENGLISH_PROMPT_PHRASES:
			if phrase in text:
				offenders.append(f"{path.relative_to(ROOT)} contains {phrase!r}")
	assert offenders == []


def test_model_visible_literals_are_not_english_only():
	offenders: list[str] = []
	for path in _python_files():
		tree = ast.parse(path.read_text(encoding="utf-8"))
		for node in ast.walk(tree):
			for literal in _model_visible_literals(node):
				if _english_only(literal.value):
					offenders.append(f"{path.relative_to(ROOT)}:{literal.lineno} {literal.value[:80]!r}")
	assert offenders == []


class _Literal:
	def __init__(self, value: str, lineno: int) -> None:
		self.value = value
		self.lineno = lineno


def _model_visible_literals(node: ast.AST) -> list[_Literal]:
	if isinstance(node, ast.Dict):
		return _dict_model_literals(node)
	if isinstance(node, ast.keyword) and node.arg in MODEL_TEXT_KEYS:
		return _string_literals(node.value)
	if isinstance(node, ast.Assign):
		target_names = {target.id for target in node.targets if isinstance(target, ast.Name)}
		if target_names & MODEL_TEXT_NAMES:
			return _string_literals(node.value)
	return []


def _dict_model_literals(node: ast.Dict) -> list[_Literal]:
	results: list[_Literal] = []
	for key, value in zip(node.keys, node.values):
		if not isinstance(key, ast.Constant) or key.value not in MODEL_TEXT_KEYS:
			continue
		results.extend(_string_literals(value))
	return results


def _string_literals(node: ast.AST) -> list[_Literal]:
	if isinstance(node, ast.Constant) and isinstance(node.value, str):
		return [_Literal(node.value, node.lineno)]
	if isinstance(node, ast.JoinedStr):
		values = []
		for part in node.values:
			if isinstance(part, ast.Constant) and isinstance(part.value, str):
				values.append(part.value)
		text = "".join(values)
		return [_Literal(text, node.lineno)] if text else []
	return []


def _all_string_literals(tree: ast.AST) -> list[str]:
	values: list[str] = []
	for node in ast.walk(tree):
		if isinstance(node, ast.Constant) and isinstance(node.value, str):
			values.append(node.value)
		elif isinstance(node, ast.JoinedStr):
			parts = [
				part.value
				for part in node.values
				if isinstance(part, ast.Constant) and isinstance(part.value, str)
			]
			if parts:
				values.append("".join(parts))
	return values


def _english_only(text: str) -> bool:
	if not text or CHINESE_RE.search(text):
		return False
	return bool(ENGLISH_RE.search(text))
