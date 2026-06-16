"""Parameterized boundary coverage for the engine redesign."""
import pytest

from axc_agent_engine.runtime.input import PassthroughInputProvider
from axc_agent_engine.plugins.builtin.compress.context.normalizer import normalize_messages, public_message
from axc_agent_engine.plugins.builtin.compress.context.packer import pack_context
from axc_agent_engine.plugins.builtin.compress.context.recent_window import select_recent_window
from axc_agent_engine.runtime.resources import ResourceRegistry


@pytest.mark.parametrize("index", range(40))
def test_resource_registry_roundtrip_matrix(index):
	name = f"resource_{index:02d}"
	value = {"index": index}
	registry = ResourceRegistry({name: value})
	assert registry.require(name) is value
	assert registry.names() == (name,)
	copied = registry.as_dict()
	copied[name] = None
	assert registry.require(name) is value


@pytest.mark.asyncio
@pytest.mark.parametrize("index", range(30))
async def test_passthrough_input_matrix(index):
	content = f"message-{index}"
	messages = [{"role": "user", "content": content, "metadata": {"i": index}}]
	result = await PassthroughInputProvider().process(messages, {"case": index})
	assert result.messages == messages
	assert result.messages is not messages
	result.messages[0]["metadata"]["i"] = -1
	assert messages[0]["metadata"]["i"] == index


@pytest.mark.parametrize("index", range(50))
def test_normalizer_public_message_matrix(index):
	role = ["user", "assistant", "system", "invalid"][index % 4]
	message = {"role": role, "content": f"content {index}"}
	result = normalize_messages([message])
	assert len(result) == 1
	assert result[0]["role"] in {"user", "assistant", "system"}
	assert result[0]["token_estimate"] >= 1
	assert set(public_message(result[0])) == {"role", "content"}


@pytest.mark.parametrize("index", range(25))
def test_recent_window_matrix(index):
	total_rounds = 5 + index
	keep_rounds = index % 5 + 1
	messages = []
	for round_no in range(total_rounds):
		messages.append({"role": "user", "content": f"u{round_no}"})
		messages.append({"role": "assistant", "content": f"a{round_no}"})
	normalized = normalize_messages(messages)
	result = select_recent_window(normalized, keep_rounds)
	users = [m["content"] for m in result if m["role"] == "user"]
	expected = [f"u{i}" for i in range(total_rounds - keep_rounds, total_rounds)]
	assert users == expected


@pytest.mark.parametrize("index", range(25))
def test_pack_context_current_user_matrix(index):
	old = "old " * (200 + index)
	current = f"current-{index}"
	messages = normalize_messages([
		{"role": "system", "content": "sys"},
		{"role": "user", "content": old},
		{"role": "assistant", "content": old},
		{"role": "user", "content": current},
	])
	result = pack_context(messages, max_input_tokens=80, reserve_output_tokens=20)
	assert result.messages[0]["role"] == "system"
	assert result.messages[-1]["content"] == current
