"""Tests for planning module — planner, scheduler, observer, replanner, por_runner."""
import pytest
import json
from unittest.mock import AsyncMock

from axc_agent_engine.planning.planner import Plan, PlanStep, create_plan, validate_plan
from axc_agent_engine.planning.planning_service import PlanningService
from axc_agent_engine.core.errors import SchemaError
from axc_agent_engine.planning.scheduler import get_next_steps, get_remaining_count, mark_step_done, mark_step_failed
from axc_agent_engine.planning.observer import observe_step
from axc_agent_engine.planning.replanner import replan, should_replan
from axc_agent_engine.planning.step_executor import build_step_prompt
from axc_agent_engine.planning.por_runner import PORRunner
from axc_agent_engine.planning.runtime import PlanRuntime
from axc_agent_engine.core.schema import StepStatus
from axc_agent_engine.core.schema import LLMMessage, LLMResponse, LLMUsage, ToolDefinition
from axc_agent_engine.core.events import EventType
from axc_agent_engine.core.context import ExecutionContext, ExecutionConfig
from axc_agent_engine.core.message_store import MessageStore
from axc_agent_engine.core.plugin_manager import PluginManager
from axc_agent_engine.core.llm_caller import LLMCaller
from axc_agent_engine.tools.registry import ToolRegistry
from axc_agent_engine.tools.tool_output import ToolOutput


class TestCreatePlan:
	def test_basic(self):
		plan = create_plan("goal", [
			{"step_id": 1, "description": "step 1"},
			{"step_id": 2, "description": "step 2", "depends_on": [1]},
		])
		assert plan.goal == "goal"
		assert len(plan.steps) == 2
		assert plan.steps[1].depends_on == [1]

	def test_empty_steps(self):
		plan = create_plan("goal", [])
		assert plan.steps == []

	def test_missing_fields(self):
		plan = create_plan("g", [{"description": "x"}])
		assert plan.steps[0].step_id == 0
		assert plan.steps[0].depends_on == []

	def test_validate_plan_rejects_duplicate_steps(self):
		plan = create_plan("goal", [
			{"step_id": 1, "description": "a"},
			{"step_id": 1, "description": "b"},
		])
		with pytest.raises(SchemaError, match="Duplicate"):
			validate_plan(plan)

	def test_validate_plan_rejects_cycles(self):
		plan = create_plan("goal", [
			{"step_id": 1, "description": "a", "depends_on": [2]},
			{"step_id": 2, "description": "b", "depends_on": [1]},
		])
		with pytest.raises(SchemaError, match="cycle"):
			validate_plan(plan)

	def test_detect_plan_ignores_invalid_plan(self):
		message = {"content": '{"goal":"g","steps":[{"step_id":1,"description":"a","depends_on":[2]}]}'}
		assert PlanningService.detect_plan(message) is None


class TestScheduler:
	def test_get_next_steps_no_deps(self):
		plan = Plan(goal="g", steps=[
			PlanStep(step_id=1, description="a"),
			PlanStep(step_id=2, description="b"),
		])
		ready = get_next_steps(plan)
		assert len(ready) == 2

	def test_get_next_steps_with_deps(self):
		plan = Plan(goal="g", steps=[
			PlanStep(step_id=1, description="a"),
			PlanStep(step_id=2, description="b", depends_on=[1]),
		])
		ready = get_next_steps(plan)
		assert len(ready) == 1
		assert ready[0].step_id == 1

	def test_get_next_steps_dep_done(self):
		plan = Plan(goal="g", steps=[
			PlanStep(step_id=1, description="a", status=StepStatus.DONE),
			PlanStep(step_id=2, description="b", depends_on=[1]),
		])
		ready = get_next_steps(plan)
		assert len(ready) == 1
		assert ready[0].step_id == 2

	def test_get_remaining_count(self):
		plan = Plan(goal="g", steps=[
			PlanStep(step_id=1, description="a", status=StepStatus.DONE),
			PlanStep(step_id=2, description="b"),
			PlanStep(step_id=3, description="c"),
		])
		assert get_remaining_count(plan) == 2

	def test_mark_step_done(self):
		plan = Plan(goal="g", steps=[PlanStep(step_id=1, description="a")])
		mark_step_done(plan, 1, "completed")
		assert plan.steps[0].status == StepStatus.DONE
		assert plan.steps[0].result == "completed"

	def test_mark_step_failed(self):
		plan = Plan(goal="g", steps=[PlanStep(step_id=1, description="a")])
		mark_step_failed(plan, 1, "error msg")
		assert plan.steps[0].status == StepStatus.FAILED
		assert plan.steps[0].error == "error msg"


class TestObserver:
	@pytest.mark.asyncio
	async def test_observe_step_success(self):
		obs = await observe_step(1, StepStatus.DONE, "result", "desc", "goal", 2)
		assert obs.step_ok is True
		assert obs.action == "continue"

	@pytest.mark.asyncio
	async def test_observe_step_failure_with_remaining(self):
		obs = await observe_step(1, StepStatus.FAILED, "error", "desc", "goal", 2)
		assert obs.step_ok is False
		assert obs.action == "replan"

	@pytest.mark.asyncio
	async def test_observe_step_last_step_success(self):
		obs = await observe_step(1, StepStatus.DONE, "result", "desc", "goal", 0)
		assert obs.goal_achieved is True
		assert obs.action == "done"

	@pytest.mark.asyncio
	async def test_observe_step_last_step_failure(self):
		obs = await observe_step(1, StepStatus.FAILED, "error", "desc", "goal", 0)
		assert obs.goal_achieved is False
		assert obs.action == "done"

	@pytest.mark.asyncio
	async def test_observe_with_llm(self):
		llm = AsyncMock()
		llm.ask = AsyncMock(return_value='{"step_ok": true, "goal_achieved": false, "action": "continue", "reason": "ok"}')
		obs = await observe_step(1, StepStatus.DONE, "result", "desc", "goal", 2, llm)
		assert obs.action == "continue"

	@pytest.mark.asyncio
	async def test_observe_with_llm_fallback(self):
		llm = AsyncMock()
		llm.ask = AsyncMock(side_effect=RuntimeError("fail"))
		obs = await observe_step(1, StepStatus.DONE, "result", "desc", "goal", 2, llm)
		assert obs.action == "continue"  # Falls back to heuristic


class TestReplanner:
	def test_should_replan(self):
		plan = Plan(goal="g", steps=[], replan_count=0)
		assert should_replan(plan) is True
		plan.replan_count = 3
		assert should_replan(plan) is False

	@pytest.mark.asyncio
	async def test_replan_without_new_steps(self):
		plan = Plan(goal="g", steps=[
			PlanStep(step_id=1, description="a", status=StepStatus.FAILED),
			PlanStep(step_id=2, description="b", depends_on=[1]),
		])
		result = await replan(plan, 1)
		assert result.replan_count == 1
		assert result.steps[1].status == StepStatus.SKIPPED

	@pytest.mark.asyncio
	async def test_replan_with_llm(self):
		llm = AsyncMock()
		llm.ask = AsyncMock(return_value='[{"step_id": 5, "description": "retry"}]')
		plan = Plan(goal="g", steps=[
			PlanStep(step_id=1, description="a", status=StepStatus.FAILED, error="oops"),
		])
		result = await replan(plan, 1, llm)
		assert any(s.step_id == 5 for s in result.steps)


class TestStepExecutor:
	def test_build_step_prompt(self):
		plan = Plan(goal="Build app", steps=[
			PlanStep(step_id=1, description="Setup", status=StepStatus.DONE, result="done"),
			PlanStep(step_id=2, description="Code", tools_needed=["file_write"]),
		])
		prompt = build_step_prompt(plan, plan.steps[1])
		assert "Build app" in prompt
		assert "Code" in prompt
		assert "file_write" in prompt
		# Step result should be referenced
		assert "done" in prompt


class TestPORRunner:
	@pytest.mark.asyncio
	async def test_empty_steps_error(self):
		llm = AsyncMock()
		pm = PluginManager([])
		caller = LLMCaller(primary=llm, fallback=None, plugin_manager=pm)
		ctx = ExecutionContext(config=ExecutionConfig(stream=True, max_rounds=10))
		ms = MessageStore()
		reg = ToolRegistry()
		runtime = PlanRuntime(llm_caller=caller, message_store=ms, registry=reg, plugin_manager=pm, ctx=ctx)
		runner = PORRunner(runtime=runtime)
		plan = create_plan("test", [])
		events = []
		async for event in runner.run(plan, "test"):
			events.append(event)
		assert any(e.type == EventType.ERROR for e in events)

	@pytest.mark.asyncio
	async def test_parallel_steps_are_reduced_into_parent_messages(self):
		class StepProvider:
			@property
			def model(self):
				return "step-provider"

			async def chat(self, messages, tools=None, **kwargs):
				last = messages[-1]["content"]
				if "当前步骤 1" in last:
					return _response("step-one-result")
				if "当前步骤 2" in last:
					return _response("step-two-result")
				if "当前步骤 3" in last:
					joined = "\n".join(str(m.get("content", "")) for m in messages)
					assert "step-one-result" in joined
					assert "step-two-result" in joined
					return _response("step-three-result")
				return _response("final-result")

			async def stream(self, messages, tools=None, **kwargs):
				raise AssertionError("non-stream POR path expected")

			async def ask(self, prompt, **kwargs):
				return ""

			async def close(self):
				pass

		pm = PluginManager([])
		caller = LLMCaller(primary=StepProvider(), fallback=None, plugin_manager=pm)
		ctx = ExecutionContext(config=ExecutionConfig(stream=False, max_rounds=20))
		ms = MessageStore()
		reg = ToolRegistry()
		runtime = PlanRuntime(llm_caller=caller, message_store=ms, registry=reg, plugin_manager=pm, ctx=ctx)
		runner = PORRunner(runtime=runtime)
		plan = Plan(goal="parallel plan", steps=[
			PlanStep(step_id=1, description="First independent step"),
			PlanStep(step_id=2, description="Second independent step"),
			PlanStep(step_id=3, description="Dependent step", depends_on=[1, 2]),
		])
		events = []
		async for event in runner.run(plan, "parallel plan"):
			events.append(event)
		contents = "\n".join(str(m.get("content", "")) for m in ms.get_all())
		assert "[POR 步骤 1 结果]" in contents
		assert "[POR 步骤 2 结果]" in contents
		assert "step-one-result" in contents
		assert "step-two-result" in contents
		assert plan.steps[2].result == "step-three-result"
		assert events[-1].type == EventType.DONE

	@pytest.mark.asyncio
	async def test_step_sub_loop_exhaustion_marks_step_failed(self):
		class ToolLoopProvider:
			@property
			def model(self):
				return "tool-loop-provider"

			async def chat(self, messages, tools=None, **kwargs):
				return LLMResponse(
					message=LLMMessage(
						content="",
						tool_calls=[{
							"id": "tc-1",
							"function": {"name": "noop", "arguments": json.dumps({})},
						}],
					),
					usage=LLMUsage(input_tokens=1, output_tokens=1),
				)

			async def stream(self, messages, tools=None, **kwargs):
				raise AssertionError("non-stream POR path expected")

			async def ask(self, prompt, **kwargs):
				return ""

			async def close(self):
				pass

		async def noop(args, ctx):
			return ToolOutput.text("ok")

		pm = PluginManager([])
		caller = LLMCaller(primary=ToolLoopProvider(), fallback=None, plugin_manager=pm)
		ctx = ExecutionContext(config=ExecutionConfig(stream=False, max_rounds=5))
		ms = MessageStore()
		reg = ToolRegistry()
		reg.register(ToolDefinition(name="noop", execute=noop, parameters={"type": "object", "properties": {}}))
		runtime = PlanRuntime(llm_caller=caller, message_store=ms, registry=reg, plugin_manager=pm, ctx=ctx)
		runner = PORRunner(runtime=runtime)
		plan = Plan(goal="looping plan", steps=[PlanStep(step_id=1, description="Looping step")])

		events = []
		async for event in runner.run(plan, "looping plan"):
			events.append(event)

		assert plan.steps[0].status == StepStatus.FAILED
		assert plan.steps[0].error == "步骤超过子循环轮次限制"
		assert any(event.type == EventType.STEP_COMPLETED and "步骤超过" in event.content for event in events)

	@pytest.mark.asyncio
	async def test_step_tool_call_can_continue_to_final_answer(self):
		class ToolThenFinalProvider:
			def __init__(self):
				self.calls = 0

			@property
			def model(self):
				return "tool-then-final-provider"

			async def chat(self, messages, tools=None, **kwargs):
				self.calls += 1
				if self.calls == 1:
					return LLMResponse(
						message=LLMMessage(
							content="",
							tool_calls=[{
								"id": "tc-1",
								"function": {"name": "noop", "arguments": json.dumps({})},
							}],
						),
						usage=LLMUsage(input_tokens=1, output_tokens=1),
					)
				return _response("step-final")

			async def stream(self, messages, tools=None, **kwargs):
				raise AssertionError("non-stream POR path expected")

			async def ask(self, prompt, **kwargs):
				return ""

			async def close(self):
				pass

		async def noop(args, ctx):
			return ToolOutput.text("tool-ok")

		provider = ToolThenFinalProvider()
		pm = PluginManager([])
		caller = LLMCaller(primary=provider, fallback=None, plugin_manager=pm)
		ctx = ExecutionContext(config=ExecutionConfig(stream=False, max_rounds=10))
		ms = MessageStore()
		reg = ToolRegistry()
		reg.register(ToolDefinition(name="noop", execute=noop, parameters={"type": "object", "properties": {}}))
		runtime = PlanRuntime(llm_caller=caller, message_store=ms, registry=reg, plugin_manager=pm, ctx=ctx)
		runner = PORRunner(runtime=runtime)
		plan = Plan(goal="tool plan", steps=[PlanStep(step_id=1, description="Use a tool")])

		events = []
		async for event in runner.run(plan, "tool plan"):
			events.append(event)

		assert provider.calls >= 2
		assert plan.steps[0].status == StepStatus.DONE
		assert plan.steps[0].result == "step-final"
		assert events[-1].type == EventType.DONE


def _response(content: str):
	from axc_agent_engine.core.schema import LLMMessage, LLMResponse, LLMUsage
	return LLMResponse(message=LLMMessage(content=content), usage=LLMUsage(input_tokens=1, output_tokens=1))
