"""Tests for events module — Event, EventType, factory methods."""
from axc_agent_engine.core.events import Event, EventType
from axc_agent_engine.core.errors import ErrorCategory, ErrorEnvelope


class TestEventType:
	def test_values(self):
		assert EventType.STREAM_START == "stream_start"
		assert EventType.DONE == "done"
		assert EventType.ERROR == "error"
		assert EventType.TOOL_CALL == "tool_call"

	def test_is_string(self):
		assert isinstance(EventType.DONE, str)


class TestEvent:
	def test_basic_creation(self):
		e = Event(type=EventType.DONE, content="finished")
		assert e.type == EventType.DONE
		assert e.content == "finished"
		assert e.tool_name == ""
		assert e.metadata == {}

	def test_factory_tool_call(self):
		e = Event.tool_call("echo", "tc1", {"text": "hi"})
		assert e.type == EventType.TOOL_CALL
		assert e.tool_name == "echo"
		assert e.tool_call_id == "tc1"
		assert e.arguments == {"text": "hi"}

	def test_factory_tool_result(self):
		e = Event.tool_result("echo", "tc1", "result text")
		assert e.type == EventType.TOOL_RESULT
		assert e.tool_name == "echo"
		assert e.content == "result text"

	def test_factory_error(self):
		e = Event.error("something broke")
		assert e.type == EventType.ERROR
		assert e.content == "something broke"

	def test_factory_error_envelope(self):
		envelope = ErrorEnvelope(
			code="provider.timeout",
			message="timed out",
			category=ErrorCategory.TIMEOUT,
			retryable=True,
		)
		e = Event.error(envelope)
		assert e.type == EventType.ERROR
		assert e.content == "timed out"
		assert e.metadata["error"]["code"] == "provider.timeout"
		assert e.metadata["error"]["retryable"] is True

	def test_factory_done(self):
		e = Event.done("all good")
		assert e.type == EventType.DONE
		assert e.content == "all good"

	def test_factory_cancelled(self):
		e = Event.cancelled("stopped", {"run_id": "r1"})
		assert e.type == EventType.CANCELLED
		assert e.content == "stopped"
		assert e.metadata["run_id"] == "r1"

	def test_factory_step_start(self):
		e = Event.step_start(1, "Do something")
		assert e.type == EventType.STEP_START
		assert e.step_id == 1
		assert e.content == "Do something"

	def test_factory_step_completed(self):
		e = Event.step_completed(2, "Done")
		assert e.type == EventType.STEP_COMPLETED
		assert e.step_id == 2

	def test_factory_delta(self):
		e = Event.delta("chunk")
		assert e.type == EventType.STREAM_DELTA
		assert e.content == "chunk"

	def test_default_fields(self):
		e = Event(type=EventType.DONE)
		assert e.content == ""
		assert e.tool_name == ""
		assert e.tool_call_id == ""
		assert e.arguments == {}
		assert e.step_id == 0
		assert e.steps == []
		assert e.metadata == {}
