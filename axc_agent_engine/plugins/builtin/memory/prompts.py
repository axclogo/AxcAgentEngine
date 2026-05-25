"""Memory 插件提示词。"""

EXTRACT_PROMPT = (
	"从以下对话中提取值得长期记忆的事实（不含临时性信息）。\n"
	"每条事实一行，格式：importance|内容\n"
	"importance 为 0.0-1.0 的浮点数表示重要程度。\n"
	"如果没有值得记忆的事实，返回空。\n\n"
	"对话：\n{conversation}\n\n"
	"事实列表："
)
