"""L3 session summary management."""
from __future__ import annotations

from axc_agent_engine.plugins.builtin.compress.context.models import ContextSummary
from axc_agent_engine.plugins.builtin.compress.prompts import SUMMARY_PROMPT


class SessionSummarizer:
	"""Circuit-broken summary generator using utility_llm."""

	def __init__(self, max_tokens: int = 800, max_failures: int = 3) -> None:
		self.state = ContextSummary()
		self.max_tokens = max_tokens
		self.max_failures = max_failures

	async def generate(self, utility_llm, conversation: list[str]) -> str:
		if self.state.broken or not utility_llm or not conversation:
			return self.state.content
		prompt = SUMMARY_PROMPT.format(conversation="\n".join(conversation), max_length=self.max_tokens)
		try:
			self.state.content = await utility_llm.ask(prompt)
			self.state.failures = 0
		except Exception:
			self.state.failures += 1
			if self.state.failures >= self.max_failures:
				self.state.broken = True
		return self.state.content


def summary_message(summary: str) -> dict[str, str] | None:
	if not summary:
		return None
	return {"role": "system", "content": f"[会话历史摘要]\n{summary}"}
