"""GoalReachedStop — LLM 判断目标路径是否完整"""
from axc_agent_engine.sidecar.multi_agent.stop_condition.llm_based import LLMBasedStop

GOAL_CHECK_PROMPT = """请判断以下讨论是否已形成完整可行的行动路径。
如果已有从当前状态到目标的清晰步骤，回复"REACHED"。
如果路径不完整或缺少关键步骤，回复"NOT_YET"并简要说明缺少什么。
格式：第一行 REACHED 或 NOT_YET，第二行说明。

讨论内容：
{content}"""


class GoalReachedStop(LLMBasedStop):
	prompt_template = GOAL_CHECK_PROMPT
	success_keyword = "REACHED"
	success_reason = "LLM 判断目标路径已完整"
	recent_limit = 8
