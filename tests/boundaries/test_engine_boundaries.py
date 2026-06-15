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
from axc_agent_engine.core.input_messages import extract_last_user_message
from axc_agent_engine.core.schema import LLMMessage, LLMResponse, RuntimeConfig
from axc_agent_engine.plugins.base import BasePlugin


class CaptureLLM:
	model = "capture"
	tool_name_mapping = None

	def __init__(self):
		self.messages = []

	async def chat(self, messages, tools=None, **kwargs):
		self.messages.append(messages)
		return LLMResponse(message=LLMMessage(content="ok"))

	async def stream(self, messages, tools=None, **kwargs):
		raise AssertionError("chat path expected")

	async def ask(self, prompt, **kwargs):
		return ""

	async def close(self):
		pass


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

	def test_input_provider_result_copies_mutable_fields(self):
		messages = [{"role": "user", "content": [{"type": "text", "text": "hi"}]}]
		artifact = object()
		artifacts = [artifact]
		metadata = {"trace": {"id": "t1"}}
		result = InputProviderResult(messages=messages, artifacts=artifacts, metadata=metadata)

		messages[0]["content"][0]["text"] = "mutated"
		artifacts.append(object())
		metadata["trace"]["id"] = "mutated"

		assert result.messages == [{"role": "user", "content": [{"type": "text", "text": "hi"}]}]
		assert result.artifacts == [artifact]
		assert result.metadata == {"trace": {"id": "t1"}}

	def test_input_provider_result_preserves_artifact_identity(self):
		class RuntimeArtifact:
			def __deepcopy__(self, memo):
				raise RuntimeError("artifact is a runtime object")

		artifact = RuntimeArtifact()
		result = InputProviderResult(messages=[{"role": "user", "content": "hi"}], artifacts=[artifact])

		assert result.artifacts[0] is artifact

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

	@pytest.mark.asyncio
	async def test_agent_preserves_multimodal_message_content(self):
		content = [
			{"type": "text", "text": "describe this"},
			{"type": "image_url", "image_url": {"url": "https://example.test/image.png"}},
			{"type": "image_base64", "media_type": "image/jpeg", "data": "abc"},
			{"type": "file_ref", "ref": "artifact-1", "metadata": {"kind": "pdf"}},
		]
		llm = CaptureLLM()
		agent = Agent("agent-name", "", "", RuntimeConfig(), [], llm, None)
		await agent.chat_with_messages([{"role": "user", "content": content}])
		normalized = llm.messages[0][-1]["content"]
		assert normalized[0] == {"type": "text", "text": "describe this"}
		assert normalized[1] == {"type": "image_url", "image_url": {"url": "https://example.test/image.png"}}
		assert normalized[2] == {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,abc"}}
		assert normalized[3] == {"type": "file_ref", "file_ref": {"ref": "artifact-1", "kind": "pdf"}}

	@pytest.mark.asyncio
	async def test_agent_rejects_invalid_multimodal_part(self):
		agent = Agent("agent-name", "", "", RuntimeConfig(), [], CaptureLLM(), None)
		with pytest.raises(ValueError, match="unsupported message content part type"):
			await agent.chat_with_messages([{"role": "user", "content": [{"type": "audio_url", "url": "x"}]}])

	def test_agent_extracts_text_goal_from_multimodal_message(self):
		content = [
			{"type": "text", "text": "text goal"},
			{"type": "image_url", "image_url": {"url": "https://example.test/image.png"}},
		]
		assert extract_last_user_message([{"role": "user", "content": content}]) == "text goal"

	@pytest.mark.asyncio
	async def test_agent_maps_runtime_queues_and_stream_idle_timeout_from_run_options(self):
		import asyncio

		captured_ctx = []

		class CapturePlugin(BasePlugin):
			name = "capture"
			async def on_execution_start(self, exec_ctx):
				captured_ctx.append(exec_ctx)

		req_q = asyncio.Queue()
		resp_q = asyncio.Queue()
		agent = Agent("agent-name", "", "", RuntimeConfig(), [CapturePlugin()], CaptureLLM(), None)
		await agent.chat("raw", run_options={
			"stream_idle_timeout": 7,
			"approval_queue": req_q,
			"response_queue": resp_q,
		})
		assert captured_ctx[0].config.stream_idle_timeout == 7
		assert captured_ctx[0].runtime.approval_queue is req_q
		assert captured_ctx[0].runtime.response_queue is resp_q
