"""ConsensusStop — LLM 判断共识终止"""
from axc_agent_engine.sidecar.multi_agent.stop_condition.llm_based import LLMBasedStop

CONSENSUS_PROMPT = """请判断以下讨论是否已达成共识。
如果各方观点已趋于一致或已形成明确结论，回复"YES"。
如果仍有分歧或讨论不充分，回复"NO"。
只回复 YES 或 NO。

讨论内容：
{content}"""


class ConsensusStop(LLMBasedStop):
	prompt_template = CONSENSUS_PROMPT
	success_keyword = "YES"
	success_reason = "LLM 判断已达成共识"
