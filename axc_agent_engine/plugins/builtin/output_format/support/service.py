"""Output validation and repair service."""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ValidationResult:
	valid: bool
	errors: list[str] = field(default_factory=list)
	content: str = ""
	format_type: str = ""
	schema_id: str = ""
	schema_version: str = ""
	degraded: bool = False

	def to_dict(self) -> dict[str, Any]:
		return {
			"valid": self.valid,
			"errors": list(self.errors),
			"content_length": len(self.content),
			"format_type": self.format_type,
			"schema_id": self.schema_id,
			"schema_version": self.schema_version,
			"degraded": self.degraded,
		}


@dataclass(frozen=True)
class RepairResult:
	content: str
	validation: ValidationResult
	attempts: int = 0
	repaired: bool = False
	errors: list[str] = field(default_factory=list)
	duration_ms: int = 0

	def to_dict(self) -> dict[str, Any]:
		return {
			"valid": self.validation.valid,
			"attempts": self.attempts,
			"repaired": self.repaired,
			"errors": list(self.errors or self.validation.errors),
			"duration_ms": self.duration_ms,
			"validation": self.validation.to_dict(),
		}


class OutputValidator:
	def __init__(self, format_type: str, config: dict[str, Any], max_output_chars: int) -> None:
		self.format_type = format_type
		self.config = config
		self.max_output_chars = max_output_chars

	def validate(self, content: str) -> ValidationResult:
		if not self.format_type:
			return self._result(True, content=content)
		size_error = self._output_size_error(content)
		if size_error:
			return self._result(False, [size_error], content)
		if self.format_type == "json_schema":
			return self._validate_json_schema(content)
		if self.format_type == "markdown":
			return self._validate_markdown(content)
		if self.format_type == "text":
			return self._validate_text(content)
		return self._result(True, content=content)

	def _validate_json_schema(self, content: str) -> ValidationResult:
		json_str = extract_json(content)
		if not json_str:
			return self._result(False, ["output contains no JSON"], content)
		try:
			data = json.loads(json_str)
		except json.JSONDecodeError as e:
			return self._result(False, [f"JSON parse failed: {str(e)[:120]}"], content)
		schema = self.config.get("schema", {})
		if not schema:
			return self._result(True, content=json_str)
		try:
			import jsonschema
			validator_cls = jsonschema.validators.validator_for(schema)
			validator_cls.check_schema(schema)
			validator = validator_cls(schema)
			errors = sorted(validator.iter_errors(data), key=lambda item: list(item.path))
			if errors:
				return self._result(False, [_schema_error_message(error) for error in errors[:20]], json_str)
		except ImportError:
			errors = _fallback_json_schema_errors(data, schema)
			if errors:
				return self._result(False, errors, json_str, degraded=True)
			return self._result(True, content=json_str, degraded=True)
		except Exception as e:
			return self._result(False, [f"schema validation failed: {str(e)[:200]}"], json_str)
		return self._result(True, content=json_str)

	def _validate_markdown(self, content: str) -> ValidationResult:
		template = self.config.get("template", "")
		if not template:
			return self._result(bool(content.strip()), [] if content.strip() else ["empty output"], content)
		errors = []
		required_sections = self.config.get("required_sections") or [
			re.sub(r"\{(\w+)\}", "", heading).strip()
			for heading in re.findall(r"^#{1,6}\s+(.+)", template, re.MULTILINE)
		]
		positions = []
		for section in required_sections:
			clean = str(section).strip()
			if clean and clean not in content:
				errors.append(f"missing section: {clean}")
			elif clean:
				positions.append((clean, content.find(clean)))
		if self.config.get("section_order") and positions:
			ordered = [position for _, position in positions]
			if ordered != sorted(ordered):
				errors.append("sections are not in required order")
		for pattern in self.config.get("required_patterns", []):
			if not re.search(str(pattern), content, re.MULTILINE):
				errors.append(f"missing required pattern: {pattern}")
		for pattern in self.config.get("forbidden_patterns", []):
			if re.search(str(pattern), content, re.MULTILINE):
				errors.append(f"contains forbidden pattern: {pattern}")
		return self._result(not errors, errors, content)

	def _validate_text(self, content: str) -> ValidationResult:
		errors = []
		max_length = int(self.config.get("max_length", 0) or 0)
		if max_length and len(content) > max_length:
			errors.append(f"output too long: {len(content)}/{max_length}")
		max_lines = int(self.config.get("max_lines", 0) or 0)
		if max_lines and len(content.splitlines()) > max_lines:
			errors.append(f"too many lines: {len(content.splitlines())}/{max_lines}")
		for keyword in self.config.get("must_contain", []):
			if keyword not in content:
				errors.append(f"missing required text: {keyword}")
		for keyword in self.config.get("must_not_contain", []):
			if keyword in content:
				errors.append(f"contains forbidden text: {keyword}")
		for pattern in self.config.get("required_patterns", []):
			if not re.search(str(pattern), content, re.MULTILINE):
				errors.append(f"missing required pattern: {pattern}")
		for pattern in self.config.get("forbidden_patterns", []):
			if re.search(str(pattern), content, re.MULTILINE):
				errors.append(f"contains forbidden pattern: {pattern}")
		return self._result(not errors, errors, content)

	def _output_size_error(self, content: str) -> str:
		if self.max_output_chars and len(content) > self.max_output_chars:
			return f"output exceeds max_output_chars: {len(content)}/{self.max_output_chars}"
		return ""

	def _result(self, valid: bool, errors: list[str] | None = None, content: str = "", degraded: bool = False) -> ValidationResult:
		return ValidationResult(
			valid=valid,
			errors=errors or [],
			content=content,
			format_type=self.format_type,
			schema_id=str(self.config.get("schema_id") or self.config.get("contract_name") or ""),
			schema_version=str(self.config.get("schema_version") or ""),
			degraded=degraded,
		)


class RepairPromptBuilder:
	def __init__(self, format_type: str, config: dict[str, Any], max_repair_chars: int) -> None:
		self.format_type = format_type
		self.config = config
		self.max_repair_chars = max_repair_chars

	def build(self, content: str) -> str:
		content = content[:self.max_repair_chars]
		if self.format_type == "json_schema":
			schema_str = json.dumps(self.config.get("schema", {}), ensure_ascii=False, indent=2)
			return (
				"Convert the following content into valid JSON matching the JSON Schema. "
				"Return only JSON.\n\n"
				f"Content:\n{content}\n\nSchema:\n{schema_str}"
			)
		if self.format_type == "markdown":
			return (
				"Rewrite the following content to match the Markdown template. Preserve important information.\n\n"
				f"Content:\n{content}\n\nTemplate:\n{self.config.get('template', '')}"
			)
		if self.format_type == "text":
			requirements = []
			if self.config.get("max_length"):
				requirements.append(f"Length must be <= {self.config['max_length']} characters.")
			requirements.extend(f"Must contain: {kw}" for kw in self.config.get("must_contain", []))
			requirements.extend(f"Must not contain: {kw}" for kw in self.config.get("must_not_contain", []))
			return (
				"Rewrite the following content to satisfy the requirements. Preserve core meaning.\n\n"
				f"Content:\n{content}\n\nRequirements:\n" + "\n".join(requirements)
			)
		return ""


class OutputRepairer:
	def __init__(self, format_type: str, utility_llm: Any, prompt_builder: RepairPromptBuilder, repair_timeout: float) -> None:
		self.format_type = format_type
		self.utility_llm = utility_llm
		self.prompt_builder = prompt_builder
		self.repair_timeout = repair_timeout

	async def repair(self, content: str) -> str:
		if not self.utility_llm:
			return self.local_repair(content)
		prompt = self.prompt_builder.build(content)
		if not prompt:
			return self.local_repair(content)
		if self.repair_timeout:
			import asyncio
			result = await asyncio.wait_for(self.utility_llm.ask(prompt), timeout=self.repair_timeout)
		else:
			result = await self.utility_llm.ask(prompt)
		return strip_code_fence(result.strip()) if result else self.local_repair(content)

	def local_repair(self, content: str) -> str:
		if self.format_type == "json_schema":
			return extract_json(content) or content
		return content


class OutputFormatService:
	"""Validate and optionally repair final model output."""

	def __init__(self, format_type: str = "", config: dict[str, Any] | None = None,
				 utility_llm: Any = None, max_repair_chars: int = 3000,
				 repair_timeout: float = 30.0, max_output_chars: int = 0) -> None:
		self.format_type = format_type
		self.config = config or {}
		self.utility_llm = utility_llm
		self.max_repair_chars = max_repair_chars
		self.repair_timeout = max(0.0, float(repair_timeout))
		self.max_output_chars = max(0, int(max_output_chars or 0))
		self._validator = OutputValidator(self.format_type, self.config, self.max_output_chars)
		self._prompt_builder = RepairPromptBuilder(self.format_type, self.config, self.max_repair_chars)
		self._repairer = OutputRepairer(self.format_type, self.utility_llm, self._prompt_builder, self.repair_timeout)

	def validate(self, content: str) -> ValidationResult:
		return self._validator.validate(content)

	async def repair(self, content: str) -> str:
		"""Repair content with a utility LLM if available."""
		return await self._repairer.repair(content)

	async def validate_and_repair(self, content: str, max_attempts: int = 1) -> tuple[str, ValidationResult]:
		result = await self.repair_with_result(content, max_attempts=max_attempts)
		return result.content, result.validation

	async def repair_with_result(self, content: str, max_attempts: int = 1) -> RepairResult:
		start = time.monotonic()
		current = content
		result = self.validate(current)
		if result.valid and result.content and result.content != current:
			return RepairResult(
				content=result.content,
				validation=result,
				attempts=0,
				repaired=True,
				duration_ms=_duration_ms(start),
			)
		attempts = 0
		errors: list[str] = []
		while not result.valid and attempts < max_attempts:
			attempts += 1
			try:
				current = await self.repair(current)
			except Exception as e:
				errors.append(f"repair attempt {attempts} failed: {str(e)[:160]}")
				break
			result = self.validate(current)
			if result.valid and result.content:
				current = result.content
			elif current == content and not self.utility_llm:
				break
		return RepairResult(
			content=current,
			validation=result,
			attempts=attempts,
			repaired=current != content,
			errors=errors,
			duration_ms=_duration_ms(start),
		)


def extract_json(content: str) -> str:
	match = re.search(r"```(?:json)?\s*\n?([\s\S]*?)\n?```", content)
	if match:
		return match.group(1).strip()
	stripped = content.strip()
	if (stripped.startswith("{") and stripped.endswith("}")) or (stripped.startswith("[") and stripped.endswith("]")):
		return stripped
	start = content.find("{")
	end = content.rfind("}")
	if start >= 0 and end > start:
		return content[start:end + 1]
	start = content.find("[")
	end = content.rfind("]")
	if start >= 0 and end > start:
		return content[start:end + 1]
	return ""


def strip_code_fence(content: str) -> str:
	if not content.startswith("```"):
		return content
	lines = content.split("\n")
	if lines and lines[-1].strip() == "```":
		return "\n".join(lines[1:-1]).strip()
	return "\n".join(lines[1:]).strip()


def _duration_ms(start: float) -> int:
	return int((time.monotonic() - start) * 1000)


def _schema_error_message(error: Any) -> str:
	path = ".".join(str(part) for part in error.path)
	location = path or "$"
	return f"{location}: {error.message}"


def _fallback_json_schema_errors(data: Any, schema: dict[str, Any]) -> list[str]:
	errors: list[str] = []
	_validate_basic_schema(data, schema, "$", errors)
	return errors


def _validate_basic_schema(data: Any, schema: dict[str, Any], path: str, errors: list[str]) -> None:
	expected_type = schema.get("type")
	if expected_type and not _matches_json_type(data, expected_type):
		errors.append(f"{path}: expected {expected_type}, got {_json_type(data)}")
		return
	if "enum" in schema and data not in schema["enum"]:
		errors.append(f"{path}: value is not in enum")
	if isinstance(data, dict):
		required = schema.get("required", [])
		for key in required:
			if key not in data:
				errors.append(f"{path}.{key}: missing required field")
		properties = schema.get("properties", {})
		for key, child_schema in properties.items():
			if key in data and isinstance(child_schema, dict):
				_validate_basic_schema(data[key], child_schema, f"{path}.{key}", errors)
		if schema.get("additionalProperties") is False:
			extra = sorted(set(data) - set(properties))
			for key in extra:
				errors.append(f"{path}.{key}: additional property not allowed")
	if isinstance(data, list):
		min_items = schema.get("minItems")
		max_items = schema.get("maxItems")
		if isinstance(min_items, int) and len(data) < min_items:
			errors.append(f"{path}: expected at least {min_items} items")
		if isinstance(max_items, int) and len(data) > max_items:
			errors.append(f"{path}: expected at most {max_items} items")
		item_schema = schema.get("items")
		if isinstance(item_schema, dict):
			for index, item in enumerate(data):
				_validate_basic_schema(item, item_schema, f"{path}[{index}]", errors)


def _matches_json_type(data: Any, expected_type: Any) -> bool:
	if isinstance(expected_type, list):
		return any(_matches_json_type(data, item) for item in expected_type)
	return {
		"object": isinstance(data, dict),
		"array": isinstance(data, list),
		"string": isinstance(data, str),
		"number": isinstance(data, (int, float)) and not isinstance(data, bool),
		"integer": isinstance(data, int) and not isinstance(data, bool),
		"boolean": isinstance(data, bool),
		"null": data is None,
	}.get(str(expected_type), True)


def _json_type(data: Any) -> str:
	if data is None:
		return "null"
	if isinstance(data, bool):
		return "boolean"
	if isinstance(data, dict):
		return "object"
	if isinstance(data, list):
		return "array"
	if isinstance(data, str):
		return "string"
	if isinstance(data, int):
		return "integer"
	if isinstance(data, float):
		return "number"
	return type(data).__name__
