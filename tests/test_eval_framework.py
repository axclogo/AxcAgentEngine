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
