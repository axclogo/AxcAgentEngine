"""Tests for engine boundary primitives."""
import pytest

from axc_agent_engine import (
	DuplicateResourceError,
	InputProviderResult,
	PassthroughInputProvider,
	ResourceNotFoundError,
	ResourceRegistry,
	ResourceTypeError,
)
from axc_agent_engine.agent import Agent
from axc_agent_engine.core.schema import RuntimeConfig


class TestResourceRegistry:
	def test_names_are_sorted(self):
		registry = ResourceRegistry({"b": object(), "a": object()})
		assert registry.names() == ("a", "b")

	def test_get_missing_returns_none(self):
		assert ResourceRegistry().get("missing") is None

	def test_require_missing_raises(self):
		with pytest.raises(ResourceNotFoundError):
			ResourceRegistry().require("missing")

	def test_duplicate_rejected_by_default(self):
		registry = ResourceRegistry({"x": object()})
		with pytest.raises(DuplicateResourceError):
			registry.register("x", object())

	def test_replace_is_explicit(self):
		registry = ResourceRegistry({"x": "old"})
		registry.register("x", "new", replace=True)
		assert registry.require("x") == "new"

	def test_expected_type_checked(self):
		registry = ResourceRegistry({"x": "value"})
		with pytest.raises(ResourceTypeError):
			registry.get("x", int)

	def test_as_dict_is_shallow_copy(self):
		registry = ResourceRegistry({"x": "value"})
		copied = registry.as_dict()
		copied["x"] = "changed"
		assert registry.require("x") == "value"


class TestInputProvider:
	@pytest.mark.asyncio
	async def test_passthrough_deep_copies_messages(self):
		messages = [{"role": "user", "content": "hi"}]
		result = await PassthroughInputProvider().process(messages, {"session_id": "s"})
		assert isinstance(result, InputProviderResult)
		assert result.messages == messages
		result.messages[0]["content"] = "changed"
		assert messages[0]["content"] == "hi"

	@pytest.mark.asyncio
	async def test_agent_chat_uses_input_provider(self, mock_llm):
		class Provider:
			async def process(self, messages, context):
				return InputProviderResult(messages=[{"role": "user", "content": "processed"}])

		agent = Agent("a", "", "", RuntimeConfig(), [], mock_llm, None, input_provider=Provider())
		await agent.chat("raw")
		assert mock_llm.chat.call_args.args[0][-1]["content"] == "processed"

	@pytest.mark.asyncio
	async def test_agent_messages_uses_input_provider(self, mock_llm):
		class Provider:
			async def process(self, messages, context):
				return InputProviderResult(messages=[*messages, {"role": "user", "content": context["agent_name"]}])

		agent = Agent("agent-name", "", "", RuntimeConfig(), [], mock_llm, None, input_provider=Provider())
		await agent.chat_with_messages([{"role": "user", "content": "raw"}])
		assert mock_llm.chat.call_args.args[0][-1]["content"] == "agent-name"
