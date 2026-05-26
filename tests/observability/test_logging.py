import json
import logging
import sys

from axc_agent_engine.observability.logging import JsonFormatter, SanitizeFilter, setup_logging


def test_sanitize_filter_masks_sensitive_values():
	record = logging.LogRecord("x", logging.INFO, __file__, 1, "api_key=secret token=abc", (), None)
	assert SanitizeFilter().filter(record) is True
	assert "api_key=***" in record.msg
	assert "token=***" in record.msg


def test_json_formatter_includes_error():
	try:
		raise RuntimeError("boom")
	except RuntimeError:
		record = logging.getLogger("x").makeRecord("x", logging.ERROR, __file__, 1, "failed", (), exc_info=sys.exc_info())
	body = json.loads(JsonFormatter().format(record))
	assert body["level"] == "ERROR"
	assert body["message"] == "failed"
	assert body["error"] == "boom"


def test_setup_logging_replaces_handlers():
	setup_logging("DEBUG", json_format=True)
	root = logging.getLogger()
	assert root.level == logging.DEBUG
	assert len(root.handlers) == 1
	assert isinstance(root.handlers[0].formatter, JsonFormatter)
