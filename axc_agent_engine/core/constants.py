"""模块级常量 — 收敛代码库里的魔法字符串。"""

# MessageStore 标记
PLUGIN_CONTEXT_TAG = "[plugin_context]"

# 压缩标记，供 CompressPlugin 识别压缩内容
COMPRESS_MARKER_SNIP = "[COMPRESSED:snip]"
COMPRESS_MARKER_MICRO = "[COMPRESSED:micro]"

# 流式聚合的 chunk 安全上限。
# 20000 个 chunk × 平均约 20 字符 = ~400KB，可覆盖绝大多数 LLM 响应。
STREAM_MAX_CHUNKS = 20_000

# 流式聚合的最大内容长度（约 500KB）。
# 防止异常长的 LLM 响应导致内存膨胀。
STREAM_MAX_CONTENT_LENGTH = 512_000

# 工具执行默认值
DEFAULT_TOOL_TIMEOUT = 120

# POR 规划限制。
# 3 次重规划用于平衡恢复尝试与无限循环风险。
MAX_REPLAN_COUNT = 3

# Agent 协作深度限制。
# 防止无限递归 Agent 调用（A→B→A→...）。
MAX_CALL_DEPTH = 3
