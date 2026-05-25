"""Tests for #25 Event type categories and #7 Event factory methods."""
from axc_agent_engine.core.events import (
	Event, EventType,
	STREAM_EVENTS, THINKING_EVENTS, TOOL_EVENTS,
	PLAN_EVENTS, SYSTEM_EVENTS, TERMINAL_EVENTS,
)


class TestEventCategories:
	def test_stream_events_contains_correct_types(self):
		assert EventType.STREAM_START in STREAM_EVENTS
		assert EventType.STREAM_DELTA in STREAM_EVENTS
		assert EventType.STREAM_END in STREAM_EVENTS
		assert len(STREAM_EVENTS) == 3

	def test_thinking_events_contains_correct_types(self):
		assert EventType.THINKING_START in THINKING_EVENTS
		assert EventType.THINKING_DELTA in THINKING_EVENTS
		assert EventType.THINKING_END in THINKING_EVENTS
		assert len(THINKING_EVENTS) == 3

	def test_tool_events_contains_correct_types(self):
		assert EventType.TOOL_CALL in TOOL_EVENTS
		assert EventType.TOOL_RESULT in TOOL_EVENTS
		assert EventType.TOOL_ARGS_PREVIEW in TOOL_EVENTS
		assert len(TOOL_EVENTS) == 3

	def test_plan_events_contains_correct_types(self):
		assert EventType.PLAN_CREATED in PLAN_EVENTS
		assert EventType.STEP_START in PLAN_EVENTS
		assert EventType.STEP_COMPLETED in PLAN_EVENTS
		assert len(PLAN_EVENTS) == 3

	def test_system_events_contains_correct_types(self):
		assert EventType.CACHE_HIT in SYSTEM_EVENTS
		assert EventType.COST_UPDATE in SYSTEM_EVENTS
		assert EventType.STATE_CHANGE in SYSTEM_EVENTS
		assert len(SYSTEM_EVENTS) == 3

	def test_terminal_events_contains_correct_types(self):
		assert EventType.DONE in TERMINAL_EVENTS
		assert EventType.ERROR in TERMINAL_EVENTS
		assert len(TERMINAL_EVENTS) == 2

	def test_all_event_types_categorized(self):
		all_categorized = STREAM_EVENTS | THINKING_EVENTS | TOOL_EVENTS | PLAN_EVENTS | SYSTEM_EVENTS | TERMINAL_EVENTS
		all_types = set(EventType)
		assert all_types == all_categorized

	def test_categories_are_frozenset(self):
		assert isinstance(STREAM_EVENTS, frozenset)
		assert isinstance(THINKING_EVENTS, frozenset)
		assert isinstance(TOOL_EVENTS, frozenset)
		assert isinstance(PLAN_EVENTS, frozenset)
		assert isinstance(SYSTEM_EVENTS, frozenset)
		assert isinstance(TERMINAL_EVENTS, frozenset)

	def test_categories_no_overlap(self):
		categories = [STREAM_EVENTS, THINKING_EVENTS, TOOL_EVENTS, PLAN_EVENTS, SYSTEM_EVENTS, TERMINAL_EVENTS]
		for i, cat_a in enumerate(categories):
			for cat_b in categories[i+1:]:
				assert cat_a & cat_b == frozenset()


class TestEventFactoryMethods:
	def test_tool_call_factory(self):
		e = Event.tool_call("file_read", "tc-1", {"path": "a.txt"})
		assert e.type == EventType.TOOL_CALL
		assert e.tool_name == "file_read"
		assert e.tool_call_id == "tc-1"
		assert e.arguments == {"path": "a.txt"}

	def test_tool_result_factory(self):
		e = Event.tool_result("file_read", "tc-1", "content here")
		assert e.type == EventType.TOOL_RESULT
		assert e.tool_name == "file_read"
		assert e.content == "content here"

	def test_error_factory(self):
		e = Event.error("something broke")
		assert e.type == EventType.ERROR
		assert e.content == "something broke"

	def test_done_factory(self):
		e = Event.done("final answer")
		assert e.type == EventType.DONE
		assert e.content == "final answer"

	def test_step_start_factory(self):
		e = Event.step_start(1, "Do something")
		assert e.type == EventType.STEP_START
		assert e.step_id == 1
		assert e.content == "Do something"

	def test_step_completed_factory(self):
		e = Event.step_completed(2, "Done")
		assert e.type == EventType.STEP_COMPLETED
		assert e.step_id == 2
		assert e.content == "Done"

	def test_delta_factory(self):
		e = Event.delta("chunk")
		assert e.type == EventType.STREAM_DELTA
		assert e.content == "chunk"

	def test_plan_created_factory(self):
		steps = [{"step_id": 1, "description": "step 1"}]
		e = Event.plan_created("goal", steps)
		assert e.type == EventType.PLAN_CREATED
		assert e.content == "goal"
		assert e.steps == steps

	def test_state_change_factory(self):
		e = Event.state_change("switched", {"reason": "fallback"})
		assert e.type == EventType.STATE_CHANGE
		assert e.content == "switched"
		assert e.metadata == {"reason": "fallback"}

	def test_state_change_factory_no_metadata(self):
		e = Event.state_change("info")
		assert e.metadata == {}

	def test_cost_update_factory(self):
		e = Event.cost_update(100, 50)
		assert e.type == EventType.COST_UPDATE
		assert e.metadata["input_tokens"] == 100
		assert e.metadata["output_tokens"] == 50
