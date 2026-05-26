"""Safety 插件 — 输入注入检测 + 输出脱敏 + 输入清洗"""
import re
import logging
from typing import Any

from axc_agent_engine.plugins.base import BasePlugin

logger = logging.getLogger(__name__)

INJECTION_PATTERNS = [
	r"ignore\s+(all\s+)?previous\s+instructions",
	r"忽略(之前|上面|以上)(的|所有)?(指令|提示|规则)",
	r"你(现在)?是一个",
	r"system\s*prompt",
	r"<\/?system>",
	r"IMPORTANT:\s*NEW\s*INSTRUCTIONS",
	r"```\s*system",
	r"<\|im_start\|>",
	r"disregard\s+(all\s+)?prior",
	r"override\s+(your\s+)?instructions",
	r"jailbreak",
	r"DAN\s+mode",
]

PII_PATTERNS = {
	"phone": (r"(?<!\d)1[3-9]\d{9}(?!\d)", lambda m: m[:3] + "****" + m[-4:]),
	"id_card": (r"(?<!\d)\d{17}[\dXx](?!\d)", lambda m: m[:6] + "********" + m[-4:]),
	"email": (r"[\w.-]+@[\w.-]+\.\w{2,}", lambda m: m[0] + "***@" + m.split("@")[1] if "@" in m else "***"),
	"bank_card": (r"(?<!\d)\d{16,19}(?!\d)", lambda m: m[:4] + " **** **** " + m[-4:]),
}

INPUT_MAX_LENGTH = 30000


class SafetyPlugin(BasePlugin):
	"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
安全防护 — 输入清洗(transform_messages) + 注入检测(pre_llm_call) + 输出脱敏(post_tool_call)"""
	name = "safety"
	display_name = "安全防护"
	priority = 10
	version = "1.0.0"
	fail_closed = True

	def initialize(self, config: dict, plugin_ctx: Any = None) -> None:
		self._prompt_injection = config.get("prompt_injection", True)
		self._pii_masking = config.get("pii_masking", False)
		self._input_sanitize = config.get("input_sanitize", True)

	def transform_messages(self, messages: list, exec_ctx: Any = None, current_message: str = "") -> list:
		"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
只做输入清洗，不做注入检测（注入检测在 pre_llm_call 中）"""
		if not messages or not self._input_sanitize:
			return messages
		return self._sanitize_latest_user(messages)

	def pre_llm_call(self, exec_ctx: Any = None, messages: list[dict] = None,
					 tools: list[dict] | None = None) -> tuple[list[dict], list[dict] | None]:
		"""LLM 调用前做注入检测"""
		if not self._prompt_injection or not messages:
			return messages, tools
		for i in range(len(messages) - 1, -1, -1):
			msg = messages[i]
			if msg.get("role") == "user":
				content = msg.get("content", "")
				if isinstance(content, str) and _detect_injection(content):
					logger.warning("[safety] Potential prompt injection detected")
					messages = list(messages)
					messages[i] = {
						"role": "user",
						"content": "[安全系统] 检测到潜在的注入攻击，原始消息已被过滤。请告知用户其请求无法处理。",
					}
				break
		return messages, tools

	async def post_tool_call(self, exec_ctx: Any = None, tool_name: str = "", arguments: dict = None,
					   result: Any = None, duration_ms: int = 0) -> Any:
		if not self._pii_masking:
			return result
		#English: PII masking on ToolOutput content 中文：源码说明。
		from axc_agent_engine.tools.tool_output import ToolOutput
		if isinstance(result, ToolOutput):
			if isinstance(result.content, str):
				result.content = _mask_pii(result.content)
			if result.summary:
				result.summary = _mask_pii(result.summary)
		return result

	def _sanitize_latest_user(self, messages: list) -> list:
		for i in range(len(messages) - 1, -1, -1):
			if messages[i].get("role") == "user":
				content = messages[i].get("content", "")
				if isinstance(content, str):
					cleaned = sanitize_input(content)
					if cleaned != content:
						messages = list(messages)
						messages[i] = {**messages[i], "content": cleaned}
				break
		return messages


def sanitize_input(text: str) -> str:
	"""English: This documentation describes the related engine component behavior.
中文：清洗用户输入"""
	if not text:
		return text
	text = re.sub(r'<at\s+user_id="[^"]*">([^<]*)</at>', r'@\1', text)
	text = re.sub(r'<@\w+>', '', text)
	text = re.sub(r'<br\s*/?>', '\n', text, flags=re.IGNORECASE)
	text = re.sub(r'<[^>]+>', '', text)
	text = re.sub(r':\w+:', '', text)
	text = re.sub(r'\n{3,}', '\n\n', text)
	if len(text) > INPUT_MAX_LENGTH:
		text = text[:INPUT_MAX_LENGTH] + "\n...[输入已截断]"
	return text


def _detect_injection(content: str) -> bool:
	if len(content.strip()) < 10:
		return False
	matches = sum(1 for p in INJECTION_PATTERNS if re.search(p, content, re.IGNORECASE))
	return matches >= 2 or (matches == 1 and len(content) > 20)


def _mask_pii(text: str) -> str:
	for pii_type, (pattern, masker) in PII_PATTERNS.items():
		text = re.sub(pattern, lambda m: masker(m.group()), text)
	return text
