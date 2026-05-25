"""CausalChainStop — LLM 判断因果链是否完整"""
from axc_agent_engine.sidecar.multi_agent.stop_condition.llm_based import LLMBasedStop

CAUSAL_CHECK_PROMPT = """请判断以下讨论是否已形成完整的因果链分析。
如果已从结果追溯到根本原因，且因果关系清晰完整，回复"COMPLETE"。
如果因果链不完整或有断裂，回复"INCOMPLETE"并简要说明缺少什么。
格式：第一行 COMPLETE 或 INCOMPLETE，第二行说明。

讨论内容：
{content}"""


class CausalChainStop(LLMBasedStop):
	prompt_template = CAUSAL_CHECK_PROMPT
	success_keyword = "COMPLETE"
	success_reason = "LLM 判断因果链已完整"
	recent_limit = 8
