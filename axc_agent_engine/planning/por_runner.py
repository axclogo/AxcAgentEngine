"""POR runner — Plan-Observe-Replan 执行子流程。

执行 LLM 创建的多步骤计划。每个步骤运行一个子 ReAct 循环。
并行步骤使用隔离的子 MessageStore/ExecutionContext，避免竞态。
串行步骤共享主 MessageStore，保证上下文连续。
"""
import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import AsyncIterator

from axc_agent_engine.core.events import Event, EventType
from axc_agent_engine.core.react_loop import ReActTurnResult, ReActTurnRunner, por_visible_event
from axc_agent_engine.runtime.checkpoint import CheckpointStatus
from axc_agent_engine.planning.checkpointing import load_plan_checkpoint, save_plan_checkpoint
from axc_agent_engine.planning.planner import Plan, PlanStep
from axc_agent_engine.planning.observer import observe_step
from axc_agent_engine.planning.replanner import replan, should_replan
from axc_agent_engine.planning.runtime import PlanRuntime
from axc_agent_engine.planning.scheduler import get_next_steps, get_remaining_count, mark_step_done, mark_step_failed
from axc_agent_engine.planning.step_executor import build_step_prompt
from axc_agent_engine.core.schema import StepStatus

logger = logging.getLogger(__name__)

_MAX_STEP_ROUNDS_RATIO = 4
_MIN_STEP_ROUNDS = 5


@dataclass
class StepExecutionResult:
	"""POR step execution output before it is reduced into the parent context."""
	step_id: int
	result: str
	events: list[Event] = field(default_factory=list)
	input_tokens: int = 0
	output_tokens: int = 0
	isolated: bool = False


class PlanCheckpointRecorder:
	def __init__(self, runtime: PlanRuntime) -> None:
		self._ctx = runtime.ctx

	async def save(
		self,
		plan: Plan | None,
		status: CheckpointStatus,
		current_step_id: int | None = None,
		phase: str = "por",
	) -> None:
		if not plan:
			return
		run_id = self._ctx.state.metadata.get("run_id", "")
		sequence = self._ctx.state.current_round * 1000 + (current_step_id or 0)
		try:
			await save_plan_checkpoint(
				self._ctx.services.checkpoint_store,
				run_id,
				sequence,
				plan,
				status=status,
				current_step_id=current_step_id,
				metadata={"phase": phase},
			)
		except Exception as e:
			logger.warning(f"POR checkpoint save error: {e}")


class StepContextFactory:
	def __init__(self, runtime: PlanRuntime) -> None:
		self._ctx = runtime.ctx

	def isolated(self, plan: Plan, step: PlanStep):
		from axc_agent_engine.core.message_store import MessageStore as ChildMessageStore
		child_messages = ChildMessageStore()
		child_messages.init_system_prompt(self._ctx.config.system_prompt)
		completed = [s for s in plan.steps if s.status == StepStatus.DONE]
		if completed:
			summary = "\n".join(f"步骤 {s.step_id}：{s.result}" for s in completed[-3:])
			child_messages.append({"role": "system", "content": f"[已完成步骤]\n{summary}"})
		child_messages.append({"role": "user", "content": build_step_prompt(plan, step)})
		child_ctx = self._ctx.fork_for_child({"por_step_id": step.step_id})
		return child_messages, child_ctx


class StepResultReducer:
	def __init__(self, runtime: PlanRuntime) -> None:
		self._ctx = runtime.ctx
		self._messages = runtime.message_store

	def merge(self, step_result: StepExecutionResult) -> None:
		if step_result.input_tokens or step_result.output_tokens:
			self._ctx.add_usage(step_result.input_tokens, step_result.output_tokens)
		if step_result.isolated:
			self._messages.append({
				"role": "assistant",
				"content": f"[POR step {step_result.step_id} result]\n{step_result.result}",
			})


class StepRunner:
	def __init__(self, runtime: PlanRuntime, context_factory: StepContextFactory) -> None:
		self._llm = runtime.llm_caller
		self._messages = runtime.message_store
		self._registry = runtime.registry
		self._pm = runtime.plugin_manager
		self._ctx = runtime.ctx
		self._context_factory = context_factory
		self._shared_turn_runner = ReActTurnRunner(self._llm, self._registry, self._pm, self._ctx, self._messages)

	async def run_shared(self, plan: Plan, step: PlanStep) -> tuple[str, list[Event]]:
		step.status = StepStatus.RUNNING
		self._messages.append({"role": "user", "content": build_step_prompt(plan, step)})
		configured_max = max(_MIN_STEP_ROUNDS, self._ctx.config.max_rounds // _MAX_STEP_ROUNDS_RATIO)
		remaining_total = self._ctx.config.max_rounds - self._ctx.state.current_round
		max_step_rounds = min(configured_max, max(_MIN_STEP_ROUNDS, remaining_total))
		step_start_time = time.time()
		step_timeout = self._ctx.config.step_timeout
		collected_events: list[Event] = []
		for _ in range(max_step_rounds):
			self._ctx.check_cancelled()
			if step_timeout > 0 and time.time() - step_start_time > step_timeout:
				step.status = StepStatus.FAILED
				return f"Step execution timeout ({step_timeout}s)", collected_events
			self._ctx.state.current_round += 1
			if self._ctx.state.current_round >= self._ctx.config.max_rounds:
				step.status = StepStatus.FAILED
				return "Step exceeded total round limit", collected_events
			try:
				turn_result = None
				async for item in self._shared_turn_runner.run(
					emit_tool_events=False,
					event_filter=por_visible_event,
				):
					if isinstance(item, ReActTurnResult):
						turn_result = item
					else:
						collected_events.append(item)
			except Exception as e:
				step.status = StepStatus.FAILED
				return f"LLM call failed: {e}", collected_events
			if turn_result is None:
				step.status = StepStatus.FAILED
				return "LLM call failed: no result", collected_events
			content = turn_result.content
			if not turn_result.has_tool_calls:
				step.status = StepStatus.DONE
				return content, collected_events
		step.status = StepStatus.FAILED
		return "Step exceeded sub-loop round limit", collected_events

	async def run_isolated(self, plan: Plan, step: PlanStep) -> StepExecutionResult:
		child_messages, child_ctx = self._context_factory.isolated(plan, step)
		child_turn_runner = ReActTurnRunner(self._llm, self._registry, self._pm, child_ctx, child_messages)
		step.status = StepStatus.RUNNING
		configured_max = max(_MIN_STEP_ROUNDS, self._ctx.config.max_rounds // _MAX_STEP_ROUNDS_RATIO)
		step_start_time = time.time()
		step_timeout = self._ctx.config.step_timeout
		collected_events: list[Event] = []
		for _ in range(configured_max):
			child_ctx.check_cancelled()
			if step_timeout > 0 and time.time() - step_start_time > step_timeout:
				step.status = StepStatus.FAILED
				return StepExecutionResult(
					step_id=step.step_id,
					result=f"Step execution timeout ({step_timeout}s)",
					events=collected_events,
					input_tokens=child_ctx.state.total_input_tokens,
					output_tokens=child_ctx.state.total_output_tokens,
					isolated=True,
				)
			child_ctx.state.current_round += 1
			try:
				turn_result = None
				async for item in child_turn_runner.run(
					emit_tool_events=False,
					event_filter=por_visible_event,
				):
					if isinstance(item, ReActTurnResult):
						turn_result = item
					else:
						collected_events.append(item)
			except Exception as e:
				step.status = StepStatus.FAILED
				return StepExecutionResult(
					step_id=step.step_id,
					result=f"LLM call failed: {e}",
					events=collected_events,
					input_tokens=child_ctx.state.total_input_tokens,
					output_tokens=child_ctx.state.total_output_tokens,
					isolated=True,
				)
			if turn_result is None:
				step.status = StepStatus.FAILED
				return StepExecutionResult(
					step_id=step.step_id,
					result="LLM call failed: no result",
					events=collected_events,
					input_tokens=child_ctx.state.total_input_tokens,
					output_tokens=child_ctx.state.total_output_tokens,
					isolated=True,
				)
			content = turn_result.content
			if not turn_result.has_tool_calls:
				step.status = StepStatus.DONE
				return StepExecutionResult(
					step_id=step.step_id,
					result=content,
					events=collected_events,
					input_tokens=child_ctx.state.total_input_tokens,
					output_tokens=child_ctx.state.total_output_tokens,
					isolated=True,
				)
		step.status = StepStatus.FAILED
		return StepExecutionResult(
			step_id=step.step_id,
			result="Step exceeded sub-loop round limit",
			events=collected_events,
			input_tokens=child_ctx.state.total_input_tokens,
			output_tokens=child_ctx.state.total_output_tokens,
			isolated=True,
		)

class PORRunner:
	"""POR 计划执行器。"""

	def __init__(self, runtime: PlanRuntime) -> None:
		self._llm = runtime.llm_caller
		self._messages = runtime.message_store
		self._registry = runtime.registry
		self._pm = runtime.plugin_manager
		self._ctx = runtime.ctx
		self._plan: Plan | None = None
		self._checkpoint_recorder = PlanCheckpointRecorder(runtime)
		self._step_context_factory = StepContextFactory(runtime)
		self._step_runner = StepRunner(runtime, self._step_context_factory)
		self._step_result_reducer = StepResultReducer(runtime)

	async def run(self, plan: Plan, user_message: str) -> AsyncIterator[Event]:
		"""使用预先构建的 Plan 执行 POR 流程。"""
		self._plan = plan
		if not self._plan.steps:
			yield Event(type=EventType.ERROR, content="Plan created with empty steps list")
			return
		self._messages.append({
			"role": "assistant",
			"content": f"Plan created: {self._plan.goal} ({len(self._plan.steps)} steps)",
		})
		yield Event(type=EventType.PLAN_CREATED, content=self._plan.goal,
					steps=[{"step_id": s.step_id, "description": s.description} for s in self._plan.steps])
		await self._pm.on_plan_created(self._ctx, {
			"goal": self._plan.goal,
			"steps": [{"step_id": s.step_id, "description": s.description} for s in self._plan.steps],
		})
		await self._save_plan_checkpoint(CheckpointStatus.RUNNING, phase="plan_created")
		async for event in self._execute_plan(user_message):
			yield event

	async def resume(self, run_id: str, user_message: str = "") -> AsyncIterator[Event]:
		"""Resume a POR run from the latest plan/step checkpoint."""
		resume_state = await load_plan_checkpoint(self._ctx.services.checkpoint_store, run_id)
		if not resume_state:
			yield Event(type=EventType.ERROR, content=f"No POR checkpoint found for run_id={run_id}")
			return
		self._ctx.state.metadata["run_id"] = run_id
		self._plan = resume_state.plan
		self._messages.append({
			"role": "assistant",
			"content": f"Plan resumed: {self._plan.goal} ({len(self._plan.steps)} steps)",
		})
		yield Event(
			type=EventType.PLAN_CREATED,
			content=self._plan.goal,
			steps=[{"step_id": s.step_id, "description": s.description, "status": s.status.value} for s in self._plan.steps],
			metadata={"resumed": True, "phase": resume_state.phase, "current_step_id": resume_state.current_step_id},
		)
		await self._save_plan_checkpoint(
			CheckpointStatus.RUNNING,
			current_step_id=resume_state.current_step_id,
			phase="resume",
		)
		async for event in self._execute_plan(user_message):
			yield event

	async def _execute_plan(self, user_message: str) -> AsyncIterator[Event]:
		"""计划执行循环。"""
		while True:
			self._ctx.check_cancelled()
			steps = get_next_steps(self._plan)
			if not steps:
				async for event in self._finalize_plan(user_message):
					yield event
				return
			if len(steps) == 1:
				async for event in self._execute_and_observe(steps[0], user_message):
					yield event
			else:
				async for event in self._execute_parallel(steps):
					yield event

	async def _execute_and_observe(self, step: PlanStep, user_message: str) -> AsyncIterator[Event]:
		"""执行单个步骤并观察结果。"""
		yield Event(type=EventType.STEP_START, step_id=step.step_id, content=step.description)
		await self._save_plan_checkpoint(CheckpointStatus.RUNNING, current_step_id=step.step_id, phase="step_start")
		step_result, step_events = await self._execute_single_step(step)
		for ev in step_events:
			yield ev
		event, goal_achieved = await self._handle_step_result(step, step_result)
		yield event
		if goal_achieved:
			async for ev in self._finalize_plan(user_message):
				yield ev

	async def _execute_parallel(self, steps: list[PlanStep]) -> AsyncIterator[Event]:
		"""使用隔离 runtime 并行执行多个就绪步骤。

		每个步骤拥有自己的子 MessageStore 和 ExecutionContext，避免消息顺序竞态
		以及轮次计数冲突。
		"""
		for step in steps:
			yield Event(type=EventType.STEP_START, step_id=step.step_id, content=step.description)
			# 使用隔离上下文并行运行步骤
		tasks = [self._execute_step_isolated(step) for step in steps]
		results = await asyncio.gather(*tasks, return_exceptions=True)
		for step, result in zip(steps, results):
			if isinstance(result, Exception):
				step.status = StepStatus.FAILED
				step_output = StepExecutionResult(
					step_id=step.step_id,
					result=f"Step execution error: {result}",
					isolated=True,
				)
			else:
				step_output = result
			for ev in step_output.events:
				yield ev
			self._merge_step_result(step_output)
			event, _ = await self._handle_step_result(step, step_output.result)
			yield event
		has_failed = any(s.status == StepStatus.FAILED for s in steps)
		if has_failed and should_replan(self._plan):
			failed_id = next(s.step_id for s in steps if s.status == StepStatus.FAILED)
			await self._do_replan(failed_id)

	async def _execute_step_isolated(self, step: PlanStep) -> StepExecutionResult:
		"""使用隔离的 MessageStore 和 ExecutionContext 执行单个步骤。"""
		return await self._step_runner.run_isolated(self._plan, step)

	def _merge_step_result(self, step_result: StepExecutionResult) -> None:
		"""Reduce an isolated step result into the parent execution context deterministically."""
		self._step_result_reducer.merge(step_result)

	async def _handle_step_result(self, step: PlanStep, step_result: str) -> tuple[Event, bool]:
		"""处理步骤结果：observe → mark → replan，返回 (event, goal_achieved)。"""
		remaining = get_remaining_count(self._plan)
		observation = await observe_step(
			step.step_id, step.status, step_result, step.description,
			self._plan.goal, remaining, self._ctx.utility_llm)
		if observation.action == "replan":
			mark_step_failed(self._plan, step.step_id, step_result)
			if should_replan(self._plan):
				await self._do_replan(step.step_id)
			await self._pm.on_step_completed(self._ctx, {"step_id": step.step_id, "status": "failed", "result": step_result})
			await self._save_plan_checkpoint(CheckpointStatus.RUNNING, current_step_id=step.step_id, phase="step_failed")
			return Event(type=EventType.STEP_COMPLETED, step_id=step.step_id, content=step_result), False
		elif observation.action == "done":
			mark_step_done(self._plan, step.step_id, step_result)
			await self._pm.on_step_completed(self._ctx, {"step_id": step.step_id, "status": "done", "result": step_result})
			await self._save_plan_checkpoint(CheckpointStatus.COMPLETED, current_step_id=step.step_id, phase="goal_done")
			return Event(type=EventType.STEP_COMPLETED, step_id=step.step_id, content=step_result), True
		else:
			mark_step_done(self._plan, step.step_id, step_result)
			await self._pm.on_step_completed(self._ctx, {"step_id": step.step_id, "status": "done", "result": step_result})
			await self._save_plan_checkpoint(CheckpointStatus.RUNNING, current_step_id=step.step_id, phase="step_done")
			return Event(type=EventType.STEP_COMPLETED, step_id=step.step_id, content=step_result), False

	async def _do_replan(self, failed_step_id: int) -> None:
		"""执行重规划。"""
		self._plan = await replan(self._plan, failed_step_id, self._ctx.utility_llm)
		await self._save_plan_checkpoint(CheckpointStatus.RUNNING, current_step_id=failed_step_id, phase="replan")

	async def _execute_single_step(self, step: PlanStep) -> tuple[str, list[Event]]:
		"""执行单个步骤（子 ReAct 循环），返回 (result, intermediate events)。

		设计：步骤消息有意累积在共享 MessageStore 中。后续步骤需要前置步骤上下文
		（工具结果、中间推理）来做决策，因此不要隔离串行步骤消息。
		"""
		return await self._step_runner.run_shared(self._plan, step)

	async def _finalize_plan(self, user_message: str) -> AsyncIterator[Event]:
		"""计划完成后生成最终摘要。"""
		completed = [s for s in self._plan.steps if s.status == StepStatus.DONE]
		summary_parts = [f"目标：{self._plan.goal}", f"已完成 {len(completed)}/{len(self._plan.steps)} 个步骤"]
		for s in completed:
			summary_parts.append(f"- 步骤 {s.step_id}：{s.result}")
		self._messages.append({"role": "user", "content": (
			"所有计划步骤都已执行。请根据结果给出最终摘要。\n" + "\n".join(summary_parts)
		)})
		try:
			messages = self._pm.transform_messages(self._messages.get_all(), self._ctx, "")
			message, _ = await self._llm.call(self._ctx, messages, None)
			content = message.get("content", "") or "\n".join(summary_parts)
		except Exception:
			content = "\n".join(summary_parts)
		yield Event(type=EventType.STREAM_END, content=content)
		await self._pm.on_round_end(self._ctx, user_message, content, [])
		await self._save_plan_checkpoint(CheckpointStatus.COMPLETED, phase="finalize")
		yield Event(type=EventType.DONE, content=content)

	async def _save_plan_checkpoint(
		self,
		status: CheckpointStatus,
		current_step_id: int | None = None,
		phase: str = "por",
	) -> None:
		if not self._plan:
			return
		await self._checkpoint_recorder.save(self._plan, status, current_step_id=current_step_id, phase=phase)


def _por_visible_events(events: list[Event]) -> list[Event]:
	return [ev for ev in events if por_visible_event(ev)]
