from axc_agent_engine.sidecar.eval import (
	AnnotationReply,
	AnnotationMatcher,
	EvalCase,
	EvalDataset,
	EvalResult,
	EvalRunner,
	InMemoryAnnotationStore,
	InMemoryEvalStore,
)
from axc_agent_engine.sidecar.eval.judge import LLMJudge
from axc_agent_engine.sidecar.eval.report import generate_report
from axc_agent_engine.sidecar.eval.runner import EvalCaseExecutor, EvalScorer
from axc_agent_engine.core.events import Event


class FakeAgent:
	async def stream(self, message):
		from axc_agent_engine.core.events import Event
		yield Event.done(f"answer:{message}")


class FakeEngine:
	def __init__(self):
		self.agent = FakeAgent()

	def get_agent(self, name):
		return self.agent if name == "agent" else None


async def test_eval_store_annotation_and_dataset_runner():
	store = InMemoryEvalStore()
	annotations = InMemoryAnnotationStore()
	await annotations.save_reply(AnnotationReply(case_id="c1", answer="answer:hello"))
	runner = EvalRunner(FakeEngine(), "agent", store=store, annotation_store=annotations)
	dataset = EvalDataset("suite", [EvalCase(input="hello", case_id="c1")])

	results = await runner.run_dataset(dataset, run_id="run")

	assert len(results) == 1
	assert results[0].score == 1.0
	assert (await store.list_cases("suite"))[0].case_id == "c1"
	assert (await store.list_results("run"))[0].actual_output == "answer:hello"


async def test_eval_store_replaces_results_by_case_id():
	store = InMemoryEvalStore()
	await store.save_result("run", EvalResult(case_id="c1", input="a", actual_output="old"))
	await store.save_result("run", EvalResult(case_id="c1", input="a", actual_output="new"))
	results = await store.list_results("run")
	assert len(results) == 1
	assert results[0].actual_output == "new"


async def test_annotation_matcher_exact_and_lexical_match():
	store = InMemoryAnnotationStore()
	await store.save_reply(AnnotationReply(
		case_id="a1",
		answer="cached answer",
		metadata={"input": "how do I reset my password"},
	))
	matcher = AnnotationMatcher(store, threshold=0.6)

	exact = await matcher.match("how do I reset my password")
	assert exact is not None
	assert exact.method == "exact"
	assert exact.reply.answer == "cached answer"

	lexical = await matcher.match("how can I reset my password")
	assert lexical is not None
	assert lexical.method == "lexical"


async def test_annotation_matcher_uses_embedding_provider():
	class Embeddings:
		async def embed(self, texts):
			assert texts == ["billing question", "invoice payment issue"]
			return [[1.0, 0.0], [0.95, 0.05]]

		async def close(self):
			pass

	store = InMemoryAnnotationStore()
	await store.save_reply(AnnotationReply(
		case_id="a1",
		answer="billing answer",
		metadata={"input": "invoice payment issue"},
	))
	matcher = AnnotationMatcher(store, embedding_provider=Embeddings(), threshold=0.9)

	match = await matcher.match("billing question")
	assert match is not None
	assert match.method == "vector"
	assert match.reply.answer == "billing answer"


async def test_eval_runner_missing_agent_and_stored_suite_errors():
	import pytest

	runner = EvalRunner(FakeEngine(), "missing")
	with pytest.raises(ValueError, match="未找到"):
		await runner.run_cases([EvalCase(input="x")])

	runner = EvalRunner(FakeEngine(), "agent")
	with pytest.raises(ValueError, match="EvalStore"):
		await runner.run_suite("suite")


async def test_eval_case_executor_records_tools_usage_and_errors():
	class Agent:
		async def stream(self, message):
			yield Event.tool_call("search", "1", {})
			yield Event.cost_update(4, 5)
			yield Event.error("bad")
			yield Event.done("done")

	result = await EvalCaseExecutor().run(Agent(), EvalCase(input="q"), "c1")
	assert result.actual_tools == ["search"]
	assert result.input_tokens == 4
	assert result.output_tokens == 5
	assert result.error == "bad"
	assert result.actual_output == "done"

	class RaisingAgent:
		async def stream(self, message):
			raise RuntimeError("boom")
			yield

	result = await EvalCaseExecutor().run(RaisingAgent(), EvalCase(input="q"), "c2")
	assert result.error == "boom"


async def test_eval_scorer_custom_llm_and_auto_paths():
	scorer = EvalScorer()
	result = EvalResult(case_id="c", input="q", actual_output="a", actual_tools=["read"])
	await scorer.score(EvalCase(input="q"), result, custom_judge=lambda c, r: 0.7)
	assert result.score == 0.7

	class Judge:
		async def judge(self, case, result):
			return 0.8, "ok"

	await scorer.score(EvalCase(input="q", expected_output="a"), result, judge=Judge())
	assert result.score == 0.8
	assert result.judge_reason == "ok"
	assert scorer.auto_score(EvalCase(input="q", expected_output="a", expected_tools=["read"]), result) == 1.0


async def test_llm_judge_parse_and_error_paths():
	class LLM:
		def __init__(self, response="", fail=False):
			self.response = response
			self.fail = fail
		async def ask(self, prompt):
			if self.fail:
				raise RuntimeError("down")
			return self.response

	score, reason = await LLMJudge(LLM("1.5|great")).judge(EvalCase(input="i", expected_output="e"), EvalResult("c", "i", "a"))
	assert score == 1.0
	assert reason == "great"
	assert LLMJudge._parse_response("0.4 maybe")[0] == 0.4
	assert LLMJudge._parse_response("unclear")[0] == 0.5
	score, reason = await LLMJudge(LLM(fail=True)).judge(EvalCase(input="i"), EvalResult("c", "i", "a"))
	assert score == 0.0
	assert "评估失败" in reason


def test_generate_report_summary_and_empty():
	empty = generate_report([])
	assert empty.total_cases == 0
	report = generate_report([
		EvalResult("c1", "i", "a", score=1.0, input_tokens=1, output_tokens=2, duration_ms=3),
		EvalResult("c2", "i", "b", score=0.2, input_tokens=4, output_tokens=5, duration_ms=6),
	], pass_threshold=0.6)
	assert report.passed == 1
	assert report.failed == 1
	assert report.avg_score == 0.6
	assert "评估报告: 2 用例" in report.summary()
