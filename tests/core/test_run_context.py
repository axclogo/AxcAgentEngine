import pytest

from axc_agent_engine.core.run_context import (
	context_run_id,
	dict_or_empty,
	normalize_run_context,
	sync_run_id,
)
from axc_agent_engine.core.run_request import RunRequest


def test_dict_or_empty_rejects_non_dict_values():
	assert dict_or_empty(None, "metadata") == {}
	assert dict_or_empty({"run_id": 0}, "metadata") == {"run_id": 0}
	with pytest.raises(TypeError, match="metadata must be a dict"):
		dict_or_empty([("run_id", "x")], "metadata")


def test_context_run_id_preserves_zero_and_ignores_empty_values():
	assert context_run_id({}) == ""
	assert context_run_id({"run_id": None}) == ""
	assert context_run_id({"run_id": ""}) == ""
	assert context_run_id({"run_id": 0}) == "0"


def test_sync_run_id_rejects_conflicts_and_copies_option_run_id_to_metadata():
	metadata = {"trace_id": "t"}
	sync_run_id({"run_id": 0}, metadata)
	assert metadata["run_id"] == "0"

	with pytest.raises(ValueError, match="run_options.run_id conflicts with metadata.run_id"):
		sync_run_id({"run_id": "a"}, {"run_id": "b"})


def test_normalize_run_context_generates_and_syncs_run_id():
	options, metadata = normalize_run_context(
		run_options={"run_id": 0, "stream": False},
		metadata={"tenant": "t"},
	)

	assert options == {"run_id": 0, "stream": False}
	assert metadata == {"tenant": "t", "run_id": "0"}


def test_normalize_run_context_uses_supplied_default_run_id():
	options, metadata = normalize_run_context(
		run_options={"stream": False},
		metadata={},
		default_run_id="batch:case",
	)

	assert options == {"stream": False}
	assert metadata["run_id"] == "batch:case"


def test_run_request_create_copies_mutable_context():
	messages = [{"role": "user", "content": [{"type": "text", "text": "original"}]}]
	llm_options = {"extra": {"value": "original"}}
	run_options = {"control": {"value": "original"}}
	metadata = {"trace": {"id": "original"}}

	request = RunRequest.create(
		user_message="hi",
		messages=messages,
		llm_options=llm_options,
		run_options=run_options,
		metadata=metadata,
	)
	messages[0]["content"][0]["text"] = "mutated"
	llm_options["extra"]["value"] = "mutated"
	run_options["control"]["value"] = "mutated"
	metadata["trace"]["id"] = "mutated"

	assert request.messages == [{"role": "user", "content": [{"type": "text", "text": "original"}]}]
	assert request.llm_options == {"extra": {"value": "original"}}
	assert request.metadata["trace"] == {"id": "original"}
