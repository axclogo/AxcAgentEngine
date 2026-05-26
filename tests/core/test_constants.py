"""Tests for constants module."""
from axc_agent_engine.core.constants import (
	PLUGIN_CONTEXT_TAG,
	COMPRESS_MARKER_SNIP, COMPRESS_MARKER_MICRO,
	STREAM_MAX_CHUNKS,
)


class TestConstants:
	def test_plugin_context_tag(self):
		assert PLUGIN_CONTEXT_TAG == "[plugin_context]"

	def test_compress_markers(self):
		assert "COMPRESSED" in COMPRESS_MARKER_SNIP
		assert "COMPRESSED" in COMPRESS_MARKER_MICRO

	def test_stream_max_chunks(self):
		assert STREAM_MAX_CHUNKS == 20_000
