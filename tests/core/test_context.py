"""Tests for ExecutionContext — config, state, convenience methods."""
import pytest
from axc_agent_engine.core.context import ExecutionConfig, ExecutionState, ExecutionContext


class TestExecutionConfig:
	def test_defaults(self):
		cfg = ExecutionConfig()
		assert cfg.max_rounds == 50
		assert cfg.stream is False
		assert cfg.thinking == "auto"
		assert cfg.parallel_tool_calls is True
		assert cfg.step_timeout == 300
		assert cfg.total_timeout == 600

	def test_custom_values(self):
		cfg = ExecutionConfig(max_rounds=100, stream=True, thinking="always")
		assert cfg.max_rounds == 100
		assert cfg.stream is True
		assert cfg.thinking == "always"

	def test_frozen(self):
		cfg = ExecutionConfig()
		with pytest.raises(Exception):
			cfg.max_rounds = 999


class TestExecutionState:
	def test_defaults(self):
		state = ExecutionState()
		assert state.current_round == 0
		assert state.total_input_tokens == 0
		assert state.cancelled is False
		assert state.error == ""
		assert state.metadata == {}

	def test_mutable(self):
		state = ExecutionState()
		state.current_round = 5
		state.cancelled = True
		assert state.current_round == 5
		assert state.cancelled is True


class TestExecutionContext:
	def test_config_access(self):
		ctx = ExecutionContext(config=ExecutionConfig(max_rounds=20, stream=True))
		assert ctx.config.max_rounds == 20
		assert ctx.config.stream is True
		assert ctx.config.system_prompt == ""

	def test_state_access(self):
		ctx = ExecutionContext()
		assert ctx.state.current_round == 0
		assert ctx.state.total_input_tokens == 0
		assert ctx.state.cancelled is False

	def test_state_mutation(self):
		ctx = ExecutionContext()
		ctx.state.current_round = 3
		assert ctx.state.current_round == 3

	def test_cancel(self):
		ctx = ExecutionContext()
		assert ctx.state.cancelled is False
		ctx.cancel()
		assert ctx.state.cancelled is True

	def test_add_usage(self):
		ctx = ExecutionContext()
		ctx.add_usage(100, 50)
		assert ctx.state.total_input_tokens == 100
		assert ctx.state.total_output_tokens == 50
		ctx.add_usage(200, 100)
		assert ctx.state.total_input_tokens == 300
		assert ctx.state.total_output_tokens == 150

	def test_estimate_image_tokens_low(self):
		ctx = ExecutionContext()
		msgs = [{"role": "user", "content": [{"type": "image_url", "image_url": {"detail": "low"}}]}]
		assert ctx.estimate_image_tokens(msgs) == 85

	def test_estimate_image_tokens_high(self):
		ctx = ExecutionContext()
		msgs = [{"role": "user", "content": [{"type": "image_url", "image_url": {"detail": "high"}}]}]
		assert ctx.estimate_image_tokens(msgs) == 85 + 170 * 4

	def test_estimate_image_tokens_no_images(self):
		ctx = ExecutionContext()
		msgs = [{"role": "user", "content": "hello"}]
		assert ctx.estimate_image_tokens(msgs) == 0

	def test_add_image_tokens(self):
		ctx = ExecutionContext()
		msgs = [{"role": "user", "content": [{"type": "image_url", "image_url": {"detail": "low"}}]}]
		ctx.add_image_tokens(msgs)
		assert ctx.state.total_input_tokens == 85

	def test_check_cancelled(self):
		from axc_agent_engine.core.errors import CancelledError
		ctx = ExecutionContext()
		ctx.check_cancelled()  # should not raise
		ctx.cancel()
		with pytest.raises(CancelledError):
			ctx.check_cancelled()

	def test_metadata_access(self):
		ctx = ExecutionContext()
		ctx.state.metadata["key"] = "value"
		assert ctx.state.metadata["key"] == "value"

	def test_error_setter(self):
		ctx = ExecutionContext()
		ctx.state.error = "something went wrong"
		assert ctx.state.error == "something went wrong"
