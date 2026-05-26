"""English: This documentation describes the related engine component behavior.
中文：结构化日志配置，自动脱敏敏感数据。"""
import json
import logging
import re
from datetime import datetime, timezone

_SENSITIVE_PATTERN = re.compile(
	r'(api_key|secret|password|token|credential)\s*[=:]\s*\S+',
	re.IGNORECASE,
)


class SanitizeFilter(logging.Filter):
	"""English: This documentation describes the related engine component behavior.
中文：从所有日志消息中脱敏敏感值的过滤器。"""

	def filter(self, record: logging.LogRecord) -> bool:
		if record.args:
			record.msg = str(record.msg)
			record.args = None
		record.msg = _SENSITIVE_PATTERN.sub(
			lambda m: m.group(1) + '=***', record.msg
		)
		return True


class JsonFormatter(logging.Formatter):
	"""JSON 格式日志，用于 ELK/Loki 等结构化日志聚合。"""

	def format(self, record: logging.LogRecord) -> str:
		entry = {
			"time": datetime.now(timezone.utc).isoformat(),
			"level": record.levelname,
			"logger": record.name,
			"message": record.getMessage(),
		}
		if record.exc_info and record.exc_info[1]:
			entry["error"] = str(record.exc_info[1])
		return json.dumps(entry, ensure_ascii=False)


def setup_logging(level: str = "INFO", json_format: bool = False) -> None:
	"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
配置日志格式；json_format=True 时输出适合日志聚合的 JSON。"""
	root = logging.getLogger()
	root.setLevel(getattr(logging, level.upper(), logging.INFO))
	if root.handlers:
		root.handlers.clear()
	handler = logging.StreamHandler()
	handler.addFilter(SanitizeFilter())
	if json_format:
		handler.setFormatter(JsonFormatter())
	else:
		handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
	root.addHandler(handler)
