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
from axc_agent_engine.core.react_kernel import ReActKernel
from axc_agent_engine.core.react_loop import ReActTurnResult, por_visible_event
from axc_agent_engine.runtime.checkpoint import CheckpointStatus
from axc_agent_engine.planning.checkpointing import plan_from_state, save_plan_checkpoint
from axc_agent_engine.planning.graph_runtime import PORGraphRuntime
from axc_agent_engine.planning.graph_state import PORGraphResult, PORGraphState
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
	"""POR step execution output before it is reduced into the parent context.
中文：此文档说明相关引擎组件的行为。"""
	step_id: int
	result: str
	events: list[Event] = field(default_factory=list)
	input_tokens: int = 0
	output_tokens: int = 0
	isolated: bool = False


class StepRunner:
	def __init__(self, runtime: PlanRuntime) -> None:
		self._llm = runtime.llm_caller
		self._messages = runtime.message_store
		self._registry = runtime.registry
		self._pm = runtime.plugin_manager
		self._ctx = runtime.ctx
		self._shared_kernel = ReActKernel(
			llm_caller=self._llm,
			registry=self._registry,
			plugin_manager=self._pm,
			ctx=self._ctx,
			messages=self._messages,
		)

	async def run_shared(self, plan: Plan, step: PlanStep) -> tuple[str, list[Event]]:
		step.status = StepStatus.RUNNING
		self._messages.append({"role": "user", "content": build_step_prompt(plan, step)})
		configured_max = max(_MIN_STEP_ROUNDS, self._ctx.config.max_rounds // _MAX_STEP_ROUNDS_RATIO)
		remaining_total = self._ctx.config.max_rounds - self._ctx.state.current_round
		max_step_rounds = min(configured_max, max(_MIN_STEP_ROUNDS, remaining_total))
		collected_events: list[Event] = []
		if self._ctx.state.current_round >= self._ctx.config.max_rounds:
			step.status = StepStatus.FAILED
			return "步骤超过总轮次限制", collected_events
		try:
			turn_result = None
			async for item in self._shared_kernel.run_step(
				max_rounds=max_step_rounds,
				step_timeout=self._ctx.config.step_timeout,
				emit_tool_events=False,
				event_filter=por_visible_event,
			):
				if isinstance(item, ReActTurnResult):
					turn_result = item
				else:
					collected_events.append(item)
		except Exception as e:
			step.status = StepStatus.FAILED
			return f"LLM 调用失败：{e}", collected_events
		if turn_result is None:
			step.status = StepStatus.FAILED
			return "LLM 调用失败：没有结果", collected_events
		content = turn_result.content
		if turn_result.failed:
			step.status = StepStatus.FAILED
			return content, collected_events
		step.status = StepStatus.DONE
		return content, collected_events

	async def run_isolated(self, plan: Plan, step: PlanStep) -> StepExecutionResult:
		child_messages, child_ctx = self._isolated_context(plan, step)
		child_kernel = ReActKernel(
			llm_caller=self._llm,
			registry=self._registry,
			plugin_manager=self._pm,
			ctx=child_ctx,
			messages=child_messages,
		)
		step.status = StepStatus.RUNNING
		configured_max = max(_MIN_STEP_ROUNDS, self._ctx.config.max_rounds // _MAX_STEP_ROUNDS_RATIO)
		collected_events: list[Event] = []
		try:
			turn_result = None
			async for item in child_kernel.run_step(
				max_rounds=configured_max,
				step_timeout=self._ctx.config.step_timeout,
				emit_tool_events=False,
				event_filter=por_visible_event,
				increment_parent_round=True,
			):
				if isinstance(item, ReActTurnResult):
					turn_result = item
				else:
					collected_events.append(item)
		except Exception as e:
			return self._isolated_result(step, f"LLM 调用失败：{e}", collected_events, child_ctx, failed=True)
		if turn_result is None:
			return self._isolated_result(step, "LLM 调用失败：没有结果", collected_events, child_ctx, failed=True)
		if turn_result.failed:
			return self._isolated_result(step, turn_result.content, collected_events, child_ctx, failed=True)
		return self._isolated_result(step, turn_result.content, collected_events, child_ctx, failed=False)

	def _isolated_result(self, step: PlanStep, result: str, events: list[Event], child_ctx, failed: bool) -> StepExecutionResult:
		step.status = StepStatus.FAILED if failed else StepStatus.DONE
		return StepExecutionResult(
			step_id=step.step_id,
			result=result,
			events=events,
			input_tokens=child_ctx.state.total_input_tokens,
			output_tokens=child_ctx.state.total_output_tokens,
			isolated=True,
		)

	def merge(self, step_result: StepExecutionResult) -> None:
		if step_result.input_tokens or step_result.output_tokens:
			self._ctx.add_usage(step_result.input_tokens, step_result.output_tokens)
		if step_result.isolated:
			self._messages.append({
				"role": "assistant",
				"content": f"[POR 步骤 {step_result.step_id} 结果]\n{step_result.result}",
			})

	def _isolated_context(self, plan: Plan, step: PlanStep):
		from axc_agent_engine.core.message_store import MessageStore as ChildMessageStore
		child_messages = ChildMessageStore()
		child_messages.init_system_prompt(self._ctx.config.system_prompt)
		completed = [s for s in plan.steps if s.status == StepStatus.DONE]
		if completed:
			summary = "\n".join(f"步骤 {s.step_id}：{s.result}" for s in completed[-3:])
			child_messages.append({"role": "system", "content": f"[已完成步骤]\n{summary}"})
		child_messages.append({"role": "user", "content": build_step_prompt(plan, step)})
		return child_messages, self._ctx.fork_for_child({"por_step_id": step.step_id})


class PORExecutionService:
	"""POR behavior service used by the pydantic-graph runtime.
中文：此文档说明相关引擎组件的行为。"""

	def __init__(self, runtime: PlanRuntime) -> None:
		self._llm = runtime.llm_caller
		self._messages = runtime.message_store
		self._registry = runtime.registry
		self._pm = runtime.plugin_manager
		self._ctx = runtime.ctx
		self._plan: Plan | None = None
		self._step_runner = StepRunner(runtime)

	async def announce_plan(self, state: PORGraphState) -> None:
		self._plan = state.plan
		if not self._plan.steps:
			return
		if state.resumed:
			return
		self._messages.append({
			"role": "assistant",
			"content": f"计划已创建：{self._plan.goal}（{len(self._plan.steps)} 个步骤）",
		})
		state.events.append(Event(type=EventType.PLAN_CREATED, content=self._plan.goal,
					steps=[{"step_id": s.step_id, "description": s.description} for s in self._plan.steps]))
		await self._pm.on_plan_created(self._ctx, {
			"goal": self._plan.goal,
			"steps": [{"step_id": s.step_id, "description": s.description} for s in self._plan.steps],
		})
		await self._save_plan_checkpoint(CheckpointStatus.RUNNING, phase="plan_created")

	async def select_steps(self, state: PORGraphState) -> None:
		state.should_continue = False
		if not state.plan.steps:
			state.error = "计划创建后步骤列表为空"
			return
		self._plan = state.plan
		state.next_steps = []
		while True:
			self._ctx.check_cancelled()
			steps = get_next_steps(self._plan)
			if steps:
				state.next_steps = steps
				state.current_step = steps[0] if len(steps) == 1 else None
				return
			async for event in self._finalize_plan(state.user_message):
				state.events.append(event)
			state.finalized = True
			return

	async def execute_step(self, state: PORGraphState) -> None:
		if state.error or state.finalized:
			return
		if len(state.next_steps) == 1:
			step = state.next_steps[0]
			state.current_step = step
			state.step_result = ""
			yielded_events = []
			yielded_events.append(Event(type=EventType.STEP_START, step_id=step.step_id, content=step.description))
			await self._save_plan_checkpoint(CheckpointStatus.RUNNING, current_step_id=step.step_id, phase="step_start")
			step_result, step_events = await self._execute_single_step(step)
			state.step_result = step_result
			state.events.extend(yielded_events)
			state.events.extend(step_events)
			return
		async for event in self._execute_parallel(state.next_steps):
			state.events.append(event)
		state.current_step = None

	async def observe_step(self, state: PORGraphState) -> None:
		if state.error or state.finalized or not state.current_step:
			return
		event, state.goal_achieved = await self._handle_step_result(state.current_step, state.step_result)
		state.events.append(event)

	async def replan_step(self, state: PORGraphState) -> None:
		state.should_continue = False
		if state.error or state.finalized:
			return
		if state.goal_achieved:
			async for event in self._finalize_plan(state.user_message):
				state.events.append(event)
			state.finalized = True
			return
		if any(step.status == StepStatus.FAILED for step in state.next_steps) and should_replan(self._plan):
			failed_id = next(step.step_id for step in state.next_steps if step.status == StepStatus.FAILED)
			await self._do_replan(failed_id)
		state.should_continue = True

	async def finalize_plan(self, state: PORGraphState) -> PORGraphResult:
		return PORGraphResult(events=state.events, final_content=state.final_content, error=state.error)

	async def _execute_parallel(self, steps: list[PlanStep]) -> AsyncIterator[Event]:
		"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
使用隔离 runtime 并行执行多个就绪步骤。

		每个步骤拥有自己的子 MessageStore 和 ExecutionContext，避免消息顺序竞态
		以及轮次计数冲突。
		"""
		for step in steps:
			yield Event(type=EventType.STEP_START, step_id=step.step_id, content=step.description)
			#English: Source note. 中文：使用隔离上下文并行运行步骤
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
			self._step_runner.merge(step_output)
			event, _ = await self._handle_step_result(step, step_output.result)
			yield event
	async def _execute_step_isolated(self, step: PlanStep) -> StepExecutionResult:
		"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
使用隔离的 MessageStore 和 ExecutionContext 执行单个步骤。"""
		return await self._step_runner.run_isolated(self._plan, step)

	async def _handle_step_result(self, step: PlanStep, step_result: str) -> tuple[Event, bool]:
		"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
处理步骤结果：observe → mark → replan，返回 (event, goal_achieved)。"""
		remaining = get_remaining_count(self._plan)
		observation = await observe_step(
			step.step_id, step.status, step_result, step.description,
			self._plan.goal, remaining, self._ctx.utility_llm)
		if observation.action == "replan":
			mark_step_failed(self._plan, step.step_id, step_result)
			await self._pm.on_step_completed(self._ctx, {"step_id": step.step_id, "status": "failed", "result": step_result})
			await self._save_plan_checkpoint(CheckpointStatus.RUNNING, current_step_id=step.step_id, phase="step_failed")
			return Event(type=EventType.STEP_COMPLETED, step_id=step.step_id, content=step_result), False
		elif observation.action == "done":
			if not observation.step_ok:
				mark_step_failed(self._plan, step.step_id, step_result)
				await self._pm.on_step_completed(self._ctx, {"step_id": step.step_id, "status": "failed", "result": step_result})
				await self._save_plan_checkpoint(CheckpointStatus.RUNNING, current_step_id=step.step_id, phase="step_failed")
				return Event(type=EventType.STEP_COMPLETED, step_id=step.step_id, content=step_result), False
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
		"""English: This documentation describes the related engine component behavior.
中文：执行重规划。"""
		self._plan = await replan(self._plan, failed_step_id, self._ctx.utility_llm)
		await self._save_plan_checkpoint(CheckpointStatus.RUNNING, current_step_id=failed_step_id, phase="replan")

	async def _execute_single_step(self, step: PlanStep) -> tuple[str, list[Event]]:
		"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
执行单个步骤（子 ReAct 循环），返回 (result, intermediate events)。

		设计：步骤消息有意累积在共享 MessageStore 中。后续步骤需要前置步骤上下文
		（工具结果、中间推理）来做决策，因此不要隔离串行步骤消息。
		"""
		return await self._step_runner.run_shared(self._plan, step)

	async def _finalize_plan(self, user_message: str) -> AsyncIterator[Event]:
		"""English: This documentation describes the related engine component behavior.
中文：计划完成后生成最终摘要。"""
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
		run_id = self._ctx.state.metadata.get("run_id", "")
		sequence = self._ctx.state.current_round * 1000 + (current_step_id or 0)
		try:
			await save_plan_checkpoint(
				self._ctx.services.checkpoint_store,
				run_id,
				sequence,
				self._plan,
				status=status,
				current_step_id=current_step_id,
				metadata={"phase": phase},
			)
		except Exception as e:
			logger.warning(f"POR checkpoint save error: {e}")


class PORRunner:
	"""POR plan runner backed by pydantic-graph.
中文：此文档说明相关引擎组件的行为。"""

	def __init__(self, runtime: PlanRuntime) -> None:
		self._service = PORExecutionService(runtime)
		self._graph_runtime = PORGraphRuntime(self._service)

	async def run(self, plan: Plan, user_message: str) -> AsyncIterator[Event]:
		"""English: Bilingual documentation follows.
中文：以下为双语文档说明。
使用预先构建的 Plan 执行 POR 流程。"""
		async for event in self._graph_runtime.run(plan, user_message):
			yield event

	async def run_from_checkpoint_state(
		self,
		checkpoint_state: dict,
		user_message: str,
		run_id: str = "",
	) -> AsyncIterator[Event]:
		plan = plan_from_state(checkpoint_state)
		if not plan:
			yield Event.error(f"No POR checkpoint found for run_id={run_id}")
			return
		current_step_id = _checkpoint_step_id(checkpoint_state.get("current_step_id"))
		for step in plan.steps:
			if step.status == StepStatus.RUNNING:
				step.status = StepStatus.PENDING
			if current_step_id is not None and step.step_id == current_step_id and step.status != StepStatus.DONE:
				step.status = StepStatus.PENDING
		initial_event = Event(
			type=EventType.PLAN_CREATED,
			content=plan.goal,
			steps=[{"step_id": s.step_id, "description": s.description, "status": s.status.value} for s in plan.steps],
			metadata={
				"resumed": True,
				"phase": str(checkpoint_state.get("phase") or "por"),
				"current_step_id": current_step_id,
			},
		)
		async for event in self._graph_runtime.run(
			plan,
			user_message,
			initial_events=[initial_event],
			resumed=True,
		):
			yield event


def _checkpoint_step_id(value) -> int | None:
	if value is None:
		return None
	try:
		return int(value)
	except (TypeError, ValueError):
		return None
