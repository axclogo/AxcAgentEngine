"""EvalRunner — 评估执行器"""
from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable

from axc_agent_engine.core.events import EventType

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
	from axc_agent_engine.sidecar.eval.store import AnnotationStore, EvalStore
	from axc_agent_engine.sidecar.eval.judge import LLMJudge


@dataclass
class EvalCase:
	"""评估用例。
	Evaluation case.
	"""
	input: str
	expected_output: str | None = None
	expected_tools: list[str] = field(default_factory=list)
	metadata: dict[str, Any] = field(default_factory=dict)
	case_id: str = ""


@dataclass
class EvalResult:
	"""评估结果。
	Evaluation result.
	"""
	case_id: str
	input: str
	actual_output: str
	actual_tools: list[str] = field(default_factory=list)
	score: float = 0.0
	judge_reason: str = ""
	input_tokens: int = 0
	output_tokens: int = 0
	duration_ms: int = 0
	error: str = ""


@dataclass
class EvalDataset:
	"""带名称的评测用例集合。
	Named collection of evaluation cases.
	"""
	suite_id: str
	cases: list[EvalCase] = field(default_factory=list)
	metadata: dict[str, Any] = field(default_factory=dict)


class EvalCaseExecutor:
	"""运行一个评测用例，并记录输出、工具、usage 和错误。
	Runs one eval case and records output, tools, usage, and error.
	"""

	async def run(self, agent: Any, case: EvalCase, case_id: str) -> EvalResult:
		start = time.time()
		actual_tools: list[str] = []
		actual_output = ""
		error = ""
		input_tokens = 0
		output_tokens = 0
		try:
			async for event in agent.stream(case.input):
				if event.type == EventType.TOOL_CALL:
					actual_tools.append(event.tool_name)
				elif event.type == EventType.DONE:
					actual_output = event.content
				elif event.type == EventType.COST_UPDATE:
					input_tokens = event.metadata.get("input_tokens", 0)
					output_tokens = event.metadata.get("output_tokens", 0)
				elif event.type == EventType.ERROR:
					error = event.content
		except Exception as e:
			error = str(e)
		duration_ms = int((time.time() - start) * 1000)
		return EvalResult(
			case_id=case_id, input=case.input,
			actual_output=actual_output, actual_tools=actual_tools,
			input_tokens=input_tokens, output_tokens=output_tokens,
			duration_ms=duration_ms, error=error,
		)


class EvalScorer:
	"""使用自定义、LLM 或确定性匹配为评测结果打分。
	Scores eval results with custom, LLM, or deterministic matching.
	"""

	async def score(
		self,
		case: EvalCase,
		result: EvalResult,
		judge: "LLMJudge | None" = None,
		custom_judge: Callable[[EvalCase, EvalResult], float] | None = None,
	) -> None:
		if custom_judge:
			result.score = custom_judge(case, result)
		elif judge and case.expected_output:
			score, reason = await judge.judge(case, result)
			result.score = score
			result.judge_reason = reason
		else:
			result.score = self.auto_score(case, result)

	def auto_score(self, case: EvalCase, result: EvalResult) -> float:
		score = 0.0
		parts = 0
		if case.expected_output:
			parts += 1
			if result.actual_output.strip() == case.expected_output.strip():
				score += 1.0
		if case.expected_tools:
			parts += 1
			expected_set = set(case.expected_tools)
			actual_set = set(result.actual_tools)
			if expected_set.issubset(actual_set):
				score += 1.0
			elif expected_set & actual_set:
				score += len(expected_set & actual_set) / len(expected_set)
		if parts == 0:
			return 1.0 if not result.error else 0.0
		return score / parts


class EvalRunner:
	"""评估执行器。
	Evaluation runner.
	"""

	def __init__(
		self,
		engine: Any,
		agent_name: str,
		store: "EvalStore | None" = None,
		annotation_store: "AnnotationStore | None" = None,
	) -> None:
		self._engine = engine
		self._agent_name = agent_name
		self._store = store
		self._annotation_store = annotation_store
		self._case_executor = EvalCaseExecutor()
		self._scorer = EvalScorer()

	async def run_cases(
		self,
		cases: list[EvalCase],
		judge_llm: Any = None,
		custom_judge: Callable[[EvalCase, EvalResult], float] | None = None,
		run_id: str = "",
	) -> list[EvalResult]:
		"""执行评估用例。
		Run evaluation cases.
		"""
		agent = self._engine.get_agent(self._agent_name)
		if not agent:
			raise ValueError(f"Agent '{self._agent_name}' 未找到")
		from axc_agent_engine.sidecar.eval.judge import LLMJudge
		judge = LLMJudge(judge_llm) if judge_llm else None
		run_id = run_id or uuid.uuid4().hex[:12]
		results = []
		for i, case in enumerate(cases):
			case_id = case.case_id or f"case_{i}"
			case = await self._apply_annotation(case, case_id)
			result = await self._run_single(agent, case, case_id)
			await self._scorer.score(case, result, judge, custom_judge)
			results.append(result)
			if self._store:
				await self._store.save_result(run_id, result)
		return results

	async def run_dataset(
		self,
		dataset: EvalDataset,
		judge_llm: Any = None,
		custom_judge: Callable[[EvalCase, EvalResult], float] | None = None,
		run_id: str = "",
	) -> list[EvalResult]:
		if self._store:
			for case in dataset.cases:
				await self._store.save_case(dataset.suite_id, case)
		return await self.run_cases(dataset.cases, judge_llm=judge_llm, custom_judge=custom_judge, run_id=run_id or dataset.suite_id)

	async def run_suite(
		self,
		suite_id: str,
		judge_llm: Any = None,
		custom_judge: Callable[[EvalCase, EvalResult], float] | None = None,
		run_id: str = "",
	) -> list[EvalResult]:
		if not self._store:
			raise ValueError("EvalStore is required to run a stored suite")
		cases = await self._store.list_cases(suite_id)
		return await self.run_cases(cases, judge_llm=judge_llm, custom_judge=custom_judge, run_id=run_id or suite_id)

	async def _run_single(self, agent: Any, case: EvalCase, case_id: str) -> EvalResult:
		"""执行单个用例。
		Run one evaluation case.
		"""
		return await self._case_executor.run(agent, case, case_id)

	@staticmethod
	def _auto_score(case: EvalCase, result: EvalResult) -> float:
		"""自动评分：精确匹配 + 工具匹配。
		Automatic scoring: exact match plus tool match.
		"""
		return EvalScorer().auto_score(case, result)

	async def _apply_annotation(self, case: EvalCase, case_id: str) -> EvalCase:
		if not self._annotation_store or case.expected_output:
			if not case.case_id:
				case.case_id = case_id
			return case
		reply = await self._annotation_store.get_reply(case_id)
		if not reply:
			if not case.case_id:
				case.case_id = case_id
			return case
		metadata = {**case.metadata, "annotation": reply.metadata}
		return EvalCase(
			input=case.input,
			expected_output=reply.answer,
			expected_tools=list(case.expected_tools),
			metadata=metadata,
			case_id=case_id,
		)
