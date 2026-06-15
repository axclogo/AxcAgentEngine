import pytest

from axc_agent_engine.core.input_messages import (
	extract_last_user_message,
	normalize_multimodal_messages,
)


def test_extract_last_user_message_reads_last_user_text_parts():
	messages = [
		{"role": "user", "content": "first"},
		{"role": "assistant", "content": "ignored"},
		{
			"role": "user",
			"content": [
				{"type": "text", "text": "line 1"},
				{"type": "image_url", "image_url": {"url": "https://example.test/a.png"}},
				{"type": "text", "text": "line 2"},
			],
		},
	]

	assert extract_last_user_message(messages) == "line 1\nline 2"


def test_extract_last_user_message_handles_empty_and_scalar_content():
	assert extract_last_user_message([]) == ""
	assert extract_last_user_message([{"role": "assistant", "content": "no user"}]) == ""
	assert extract_last_user_message([{"role": "user", "content": None}]) == ""
	assert extract_last_user_message([{"role": "user", "content": 123}]) == "123"


def test_normalize_multimodal_messages_converts_supported_parts_without_mutating_source():
	content = [
		{"type": "text", "text": "goal"},
		{"type": "image_url", "image_url": {"url": "https://example.test/a.png", "detail": "low"}},
		{"type": "image_base64", "data": "abc", "media_type": "image/jpeg"},
		{"type": "file_ref", "ref": "file-1", "metadata": {"name": "report.pdf"}},
	]
	messages = [{"role": "user", "content": content}]

	normalized = normalize_multimodal_messages(messages)

	assert normalized == [
		{
			"role": "user",
			"content": [
				{"type": "text", "text": "goal"},
				{"type": "image_url", "image_url": {"url": "https://example.test/a.png", "detail": "low"}},
				{"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,abc"}},
				{"type": "file_ref", "file_ref": {"ref": "file-1", "name": "report.pdf"}},
			],
		}
	]
	assert messages[0]["content"] is content
	assert normalized[0] is not messages[0]


@pytest.mark.parametrize(
	"content,error",
	[
		([[object()]], TypeError),
		([{"type": "image_url", "image_url": {}}], ValueError),
		([{"type": "image_base64"}], ValueError),
		([{"type": "file_ref"}], ValueError),
		([{"type": "file_ref", "ref": "file-1", "metadata": [("name", "report.pdf")]}], TypeError),
		([{"type": "audio"}], ValueError),
	],
)
def test_normalize_multimodal_messages_rejects_invalid_parts(content, error):
	with pytest.raises(error):
		normalize_multimodal_messages([{"role": "user", "content": content}])
