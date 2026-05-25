"""存储无关的评测仓储。
Storage-neutral evaluation stores.
"""
from __future__ import annotations

import asyncio
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
	from axc_agent_engine.sidecar.eval.runner import EvalCase, EvalResult


@dataclass
class AnnotationReply:
	"""评测用例的参考答案或评分规则。
	Reference answer or rubric for an eval case.
	"""
	case_id: str
	answer: str = ""
	rubric: str = ""
	metadata: dict = field(default_factory=dict)


@runtime_checkable
class EvalStore(Protocol):
	"""持久化评测用例和结果，但不规定具体数据库。
	Persists eval cases and results without dictating a database.
	"""
	async def save_case(self, suite_id: str, case: "EvalCase") -> None: ...
	async def list_cases(self, suite_id: str) -> list["EvalCase"]: ...
	async def save_result(self, run_id: str, result: "EvalResult") -> None: ...
	async def list_results(self, run_id: str) -> list["EvalResult"]: ...
	async def delete_run(self, run_id: str) -> None: ...


@runtime_checkable
class AnnotationStore(Protocol):
	"""按 case_id 存储参考标注。
	Stores reference annotations keyed by case_id.
	"""
	async def save_reply(self, reply: AnnotationReply) -> None: ...
	async def get_reply(self, case_id: str) -> AnnotationReply | None: ...
	async def list_replies(self) -> list[AnnotationReply]: ...


class InMemoryEvalStore:
	"""有容量边界的内存 EvalStore 实现。
	Bounded in-memory EvalStore implementation.
	"""

	def __init__(self, max_suites: int = 100, max_runs: int = 1000) -> None:
		self._cases: OrderedDict[str, list["EvalCase"]] = OrderedDict()
		self._results: OrderedDict[str, list["EvalResult"]] = OrderedDict()
		self._max_suites = max_suites
		self._max_runs = max_runs
		self._lock = asyncio.Lock()

	async def save_case(self, suite_id: str, case: "EvalCase") -> None:
		if not suite_id:
			raise ValueError("suite_id is required")
		async with self._lock:
			cases = self._cases.setdefault(suite_id, [])
			cases[:] = [item for item in cases if item.case_id != case.case_id]
			cases.append(case)
			self._cases.move_to_end(suite_id)
			while self._max_suites > 0 and len(self._cases) > self._max_suites:
				self._cases.popitem(last=False)

	async def list_cases(self, suite_id: str) -> list["EvalCase"]:
		async with self._lock:
			cases = self._cases.get(suite_id, [])
			if suite_id in self._cases:
				self._cases.move_to_end(suite_id)
			return list(cases)

	async def save_result(self, run_id: str, result: "EvalResult") -> None:
		if not run_id:
			raise ValueError("run_id is required")
		async with self._lock:
			results = self._results.setdefault(run_id, [])
			results[:] = [item for item in results if item.case_id != result.case_id]
			results.append(result)
			self._results.move_to_end(run_id)
			while self._max_runs > 0 and len(self._results) > self._max_runs:
				self._results.popitem(last=False)

	async def list_results(self, run_id: str) -> list["EvalResult"]:
		async with self._lock:
			results = self._results.get(run_id, [])
			if run_id in self._results:
				self._results.move_to_end(run_id)
			return list(results)

	async def delete_run(self, run_id: str) -> None:
		async with self._lock:
			self._results.pop(run_id, None)


class InMemoryAnnotationStore:
	"""内存 AnnotationStore 实现。
	In-memory AnnotationStore implementation.
	"""

	def __init__(self) -> None:
		self._replies: OrderedDict[str, AnnotationReply] = OrderedDict()
		self._lock = asyncio.Lock()

	async def save_reply(self, reply: AnnotationReply) -> None:
		if not reply.case_id:
			raise ValueError("case_id is required")
		async with self._lock:
			self._replies[reply.case_id] = reply
			self._replies.move_to_end(reply.case_id)

	async def get_reply(self, case_id: str) -> AnnotationReply | None:
		async with self._lock:
			reply = self._replies.get(case_id)
			if reply:
				self._replies.move_to_end(case_id)
			return reply

	async def list_replies(self) -> list[AnnotationReply]:
		async with self._lock:
			return list(self._replies.values())
