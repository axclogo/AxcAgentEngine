"""OutputFormat 插件 — 输出格式约束与校验"""
import json
import logging
from typing import Any, TYPE_CHECKING

from axc_agent_engine.core.errors import ErrorCategory, ErrorEnvelope, PluginError
from axc_agent_engine.core.schema import ToolDefinition
from axc_agent_engine.plugins.base import BasePlugin
from axc_agent_engine.plugins.builtin.config_schemas import OUTPUT_FORMAT_CONFIG_SCHEMA
from axc_agent_engine.plugins.builtin.output_format.support import OutputFormatService

if TYPE_CHECKING:
	from axc_agent_engine.core.context import ExecutionContext
	from axc_agent_engine.plugins import PluginContext

logger = logging.getLogger(__name__)

FORMAT_PROMPTS = {
	"json_schema": "你必须严格按照以下 JSON Schema 格式输出：\n```json\n{schema}\n```\n不要输出任何 JSON 之外的内容。",
	"markdown": "请按照以下 Markdown 模板格式输出：\n{template}",
	"text": "输出要求：{constraints}",
}


class OutputFormatPlugin(BasePlugin):
	name = "output_format"
	display_name = "输出格式"
	priority = 95
	version = "1.0.0"
	config_schema = OUTPUT_FORMAT_CONFIG_SCHEMA

	def initialize(self, config: dict, plugin_ctx: "PluginContext") -> None:
		self._type = config.get("type", "")
		self._schema = config.get("schema", {})
		self._template = config.get("template", "")
		self._constraints = config.get("constraints", "")
		self._strict = config.get("strict", False)
		self._auto_repair = config.get("auto_repair", config.get("repair", True))
		self._repair_attempts = int(config.get("repair_attempts", 1))
		self._repair_timeout = float(config.get("repair_timeout", 30))
		self._max_repair_chars = int(config.get("max_repair_chars", 3000))
		self._max_output_chars = int(config.get("max_output_chars", 0) or 0)
		self._schema_id = str(config.get("schema_id") or config.get("contract_name") or "")
		self._schema_version = str(config.get("schema_version") or "")
		self._plugin_ctx = plugin_ctx

	def inject_context(self, exec_ctx: "ExecutionContext", topic: str = "") -> str:
		"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
注入格式要求到 system_prompt"""
		if not self._type:
			return ""
		if self._type == "json_schema" and self._schema:
			schema_str = json.dumps(self._schema, ensure_ascii=False, indent=2)
			return FORMAT_PROMPTS["json_schema"].format(schema=schema_str)
		elif self._type == "markdown" and self._template:
			return FORMAT_PROMPTS["markdown"].format(template=self._template)
		elif self._type == "text" and self._constraints:
			return FORMAT_PROMPTS["text"].format(constraints=self._constraints)
		return ""

	async def on_execution_complete(self, exec_ctx: "ExecutionContext", result: str, trace: dict) -> str:
		"""English: This documentation describes the related engine component behavior.
中文：校验并可选修复最终输出。"""
		if not result or not self._type:
			return result
		service = self._service()
		repair_result = None
		if self._auto_repair:
			repair_result = await service.repair_with_result(result, max_attempts=self._repair_attempts)
			fixed = repair_result.content
			validation = repair_result.validation
			if not validation.valid:
				logger.warning("[output_format] Final output format validation failed: %s", validation.errors)
				await self._record_result(exec_ctx, validation, repair_result, strict_failed=self._strict)
				if self._strict:
					raise OutputContractError(validation.errors, validation.to_dict())
				return fixed
			await self._record_result(exec_ctx, validation, repair_result)
			return fixed
		validation = service.validate(result)
		if not validation.valid:
			logger.warning("[output_format] Final output format validation failed: %s", validation.errors)
			await self._record_result(exec_ctx, validation, strict_failed=self._strict)
			if self._strict:
				raise OutputContractError(validation.errors, validation.to_dict())
		else:
			await self._record_result(exec_ctx, validation)
		return result

	def get_tools(self) -> list[ToolDefinition]:
		return [ToolDefinition(
			name="output_format_validate",
			description="根据已配置的输出格式契约校验草稿内容。",
			parameters={
				"type": "object",
				"properties": {
					"content": {"type": "string", "description": "需要校验的草稿内容"},
					"repair": {"type": "boolean", "description": "是否尝试修复", "default": False},
				},
				"required": ["content"],
			},
			is_read_only=True,
			capability="output_validation",
			risk_level="safe",
			execute=self._tool_validate,
		)]

	def _validate_output(self, output: str) -> bool:
		return self._service().validate(output).valid

	def _validate_json(self, output: str) -> bool:
		return self._service().validate(output).valid

	def _validate_schema(self, data: Any) -> bool:
		if not isinstance(self._schema, dict):
			return True
		required = self._schema.get("required", [])
		if required and isinstance(data, dict):
			return all(k in data for k in required)
		return True

	def _service(self) -> OutputFormatService:
		config: dict[str, Any] = {
			"schema": self._schema,
			"template": self._template,
			"schema_id": self._schema_id,
			"schema_version": self._schema_version,
		}
		if isinstance(self._constraints, dict):
			config.update(self._constraints)
		elif self._constraints:
			config["must_contain"] = [self._constraints]
		return OutputFormatService(
			self._type,
			config,
			utility_model=getattr(self._plugin_ctx, "utility_model", None),
			max_repair_chars=self._max_repair_chars,
			repair_timeout=self._repair_timeout,
			max_output_chars=self._max_output_chars,
		)

	async def _tool_validate(self, args: dict, context: dict):
		from axc_agent_engine.tools.tool_output import ToolOutput
		content = str(args.get("content", ""))
		if not content:
			return ToolOutput.error("content 不能为空")
		repair = bool(args.get("repair", False))
		service = self._service()
		if repair:
			result = await service.repair_with_result(content, max_attempts=self._repair_attempts)
			return ToolOutput.json_output(result.to_dict(), summary="输出格式校验通过" if result.validation.valid else "输出格式校验失败")
		validation = service.validate(content)
		return ToolOutput.json_output(validation.to_dict(), summary="输出格式校验通过" if validation.valid else "输出格式校验失败")

	async def _record_result(self, exec_ctx: "ExecutionContext", validation, repair_result=None, strict_failed: bool = False) -> None:
		payload = {
			"valid": validation.valid,
			"format_type": self._type,
			"schema_id": validation.schema_id,
			"schema_version": validation.schema_version,
			"errors": list(validation.errors),
			"strict": self._strict,
			"strict_failed": strict_failed,
			"auto_repair": self._auto_repair,
			"repair_attempts": repair_result.attempts if repair_result else 0,
			"repaired": repair_result.repaired if repair_result else False,
			"degraded": validation.degraded,
		}
		exec_ctx.state.metadata["output_format"] = payload
		await self._audit(exec_ctx, payload)

	async def _audit(self, exec_ctx: "ExecutionContext", payload: dict[str, Any]) -> None:
		if not exec_ctx.services.audit_sink:
			return
		from axc_agent_engine.observability.audit import AuditEvent
		metadata = exec_ctx.state.metadata
		error = {}
		if not payload["valid"]:
			error = ErrorEnvelope(
				code="output.contract_violation",
				message="Final output failed output format contract validation",
				category=ErrorCategory.CONTRACT,
				retryable=bool(self._auto_repair and payload["repair_attempts"] < self._repair_attempts),
				details={"errors": payload["errors"]},
			).to_dict()
		await exec_ctx.services.audit_sink.record(AuditEvent(
			type="output_format_validated" if payload["valid"] else "output_format_failed",
			actor=str(metadata.get("agent_name") or metadata.get("user_id") or ""),
			session_id=str(metadata.get("session_id") or ""),
			tool_name="output_format",
			capability="output_validation",
			risk_level="safe",
			allowed=payload["valid"],
			error=error,
			metadata=payload,
		))


class OutputContractError(PluginError):
	"""Raised when strict output contract validation fails.
中文：此文档说明相关引擎组件的行为。"""

	def __init__(self, errors: list[str], details: dict[str, Any] | None = None) -> None:
		self.errors = list(errors)
		self.envelope = ErrorEnvelope(
			code="output.contract_violation",
			message="Final output failed output format contract validation",
			category=ErrorCategory.CONTRACT,
			retryable=False,
			details=details or {"errors": self.errors},
		)
		super().__init__(self.envelope.message + ": " + "; ".join(self.errors))
