"""Tests for error hierarchy."""
import pytest
from axc_agent_engine.core.errors import (
	AxcError, ConfigError, SchemaError, PluginError, PluginLoadError,
	PluginInitError, LLMError, ProviderError, LLMTimeoutError,
	ExecutionError, MaxRoundsError, CancelledError,
)


class TestErrorHierarchy:
	def test_base_error(self):
		e = AxcError("base")
		assert isinstance(e, Exception)
		assert str(e) == "base"

	def test_config_errors(self):
		assert issubclass(ConfigError, AxcError)
		assert issubclass(SchemaError, ConfigError)

	def test_plugin_errors(self):
		assert issubclass(PluginError, AxcError)
		assert issubclass(PluginLoadError, PluginError)
		assert issubclass(PluginInitError, PluginError)

	def test_llm_errors(self):
		assert issubclass(LLMError, AxcError)
		assert issubclass(ProviderError, LLMError)
		assert issubclass(LLMTimeoutError, LLMError)

	def test_execution_errors(self):
		assert issubclass(ExecutionError, AxcError)
		assert issubclass(MaxRoundsError, ExecutionError)
		assert issubclass(CancelledError, ExecutionError)

	def test_catch_by_base(self):
		with pytest.raises(AxcError):
			raise ProviderError("test")

	def test_catch_specific(self):
		with pytest.raises(ProviderError):
			raise ProviderError("test")
		# Should not catch unrelated
		with pytest.raises(ProviderError):
			try:
				raise ProviderError("x")
			except ConfigError:
				pass
			except ProviderError:
				raise
