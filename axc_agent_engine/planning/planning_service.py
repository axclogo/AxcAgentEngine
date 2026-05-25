"""PlanningService — 从 LLM 响应内容中提取结构化计划。

计划只从 content 里的结构化 JSON（goal + steps）识别。
这里不做 tool_call 识别；POR 是运行时路由模式，不是工具。
"""
import logging
from dataclasses import replace
from typing import Any

from axc_agent_engine.core.errors import SchemaError
from axc_agent_engine.planning.planner import Plan, create_plan, validate_plan
from axc_agent_engine.utils.json_utils import extract_json_object

logger = logging.getLogger(__name__)

class PlanningService:
	"""为 POR 路由创建并提取执行计划。"""

	@staticmethod
	async def generate_plan(llm_caller: Any, ctx: Any, goal: str) -> Plan:
		"""调用当前配置的 LLM 生成 Plan 对象。

		服务通过 LLMCaller 消费标准化 LLMResponse；OpenAI 响应结构不能离开
		provider 实现层。
		"""
		prompt = (
			"请为用户任务创建一个简洁的执行计划。只返回 JSON，结构如下：\n"
			'{"goal":"...","steps":[{"step_id":1,"description":"...","depends_on":[],"tools_needed":[]}]}\n'
			"不要调用工具。用户任务：\n"
			f"{goal}"
		)
		messages = [
			{"role": "system", "content": "你是运行时 POR 路由的规划服务，只负责生成结构化执行计划。"},
			{"role": "user", "content": prompt},
		]
		plan_ctx = replace(ctx, config=replace(ctx.config, stream=False))
		message, _events = await llm_caller.call(plan_ctx, messages, None)
		plan = PlanningService.detect_plan(message)
		if plan and plan.steps:
			return plan
		return create_plan(goal, [{"step_id": 1, "description": goal, "depends_on": [], "tools_needed": []}])

	@staticmethod
	def detect_plan(message: dict[str, Any]) -> Plan | None:
		"""从 LLM 响应内容中识别结构化计划。

		只识别包含 goal + steps 的 JSON 块；找到返回 Plan，否则返回 None。
		"""
		content = message.get("content", "") or ""
		if not content or "goal" not in content or "steps" not in content:
			return None
		data = extract_json_object(content)
		goal = data.get("goal", "")
		steps = data.get("steps", [])
		if goal and isinstance(steps, list) and steps:
			plan = create_plan(goal, steps)
			try:
				validate_plan(plan)
			except SchemaError as e:
				logger.warning("[planning] Invalid plan ignored: %s", e)
				return None
			return plan
		return None
