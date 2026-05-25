"""Compress 插件提示词。"""

SUMMARY_PROMPT = (
	"将以下对话压缩为简洁摘要，保留：\n"
	"1. 用户核心需求\n"
	"2. 关键事实和数据\n"
	"3. 已完成的操作\n"
	"4. 用户偏好和约束\n"
	"5. 待办任务\n\n"
	"对话：\n{conversation}\n\n"
	"摘要（不超过{max_length}字）："
)
