"""POR planning prompt templates.
中文：此文档说明相关引擎组件的行为。"""

OBSERVE_PROMPT = (
	"评估以下步骤的执行结果。\n"
	"目标：{goal}\n"
	"步骤 {step_id}：{description}\n"
	"结果：{result}\n"
	"剩余步骤数：{remaining}\n\n"
	"用 JSON 格式回复：\n"
	'{{"step_ok": true/false, "goal_achieved": true/false, '
	'"action": "continue/replan/done", "reason": "简要原因"}}'
)

REPLAN_PROMPT = (
	"原计划目标: {goal}\n"
	"已完成步骤:\n{completed}\n"
	"失败步骤 {failed_id}: {failed_error}\n"
	"剩余待执行步骤:\n{pending}\n\n"
	"请生成新的执行步骤替换剩余步骤，用 JSON 数组格式：\n"
	'[{{"step_id": N, "description": "...", "depends_on": [], "tools_needed": []}}]'
)
