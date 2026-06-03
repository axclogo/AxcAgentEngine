"""Builtin plugin configuration schemas.
中文：内置插件配置 Schema。"""
from __future__ import annotations

from axc_agent_engine.core.constants import MAX_CALL_DEPTH
from axc_agent_engine.plugins.config_schema import (
	PluginConfigField,
	array_field,
	config_field,
	config_schema,
	object_field,
)


def _string_item() -> PluginConfigField:
	return config_field("", "文本项", "string", "数组中的文本项", label_en="String item")


def _object_item(children: list[PluginConfigField] | None = None) -> PluginConfigField:
	return config_field("", "对象项", "object", "数组中的对象项", label_en="Object item", children=children or [])


BUILTIN_TOOLS_CONFIG_SCHEMA = config_schema(
	"builtin_tools",
	"内置工具",
	"声明内置工具加载列表和延迟加载列表。",
	[
		array_field("load", "加载工具", "要加载的内置工具名列表；为空时加载默认工具集。", _string_item(), label_en="Load tools"),
		array_field("defer", "延迟工具", "通过 tool_search 按需激活的工具名列表。", _string_item(), label_en="Deferred tools"),
	],
	display_name_en="Builtin tools",
)


COMPRESS_CONFIG_SCHEMA = config_schema(
	"compress",
	"上下文治理",
	"控制上下文窗口、摘要、召回、工具结果压缩和边界持久化。",
	[
		object_field("tool_result", "工具结果", "工具结果内联和外部化阈值。", [
			config_field("max_inline_tokens", "最大内联 Token", "integer", "超过该值的工具消息会被压缩；兼容旧字段 snip_threshold。", label_en="Max inline tokens", default=1200),
			config_field("artifact_threshold_tokens", "产物阈值 Token", "integer", "超过该值的工具输出会尝试外部化为 artifact。", label_en="Artifact threshold tokens", default=4000),
		], label_en="Tool result"),
		object_field("recent_window", "近期窗口", "保留最近轮次的原始上下文窗口。", [
			config_field("rounds", "保留轮次", "integer", "最近保留的对话轮数；兼容旧字段 micro_compact_keep_recent。", label_en="Rounds", default=4),
		], label_en="Recent window"),
		object_field("context_window", "上下文窗口", "输入上下文打包预算。", [
			config_field("max_input_tokens", "最大输入 Token", "integer", "打包后的最大输入 token 预算。", label_en="Max input tokens", default=24000),
			config_field("reserve_output_tokens", "预留输出 Token", "integer", "为模型输出预留的 token 数。", label_en="Reserve output tokens", default=4000),
		], label_en="Context window"),
		object_field("summary", "摘要", "会话摘要触发和长度控制。", [
			config_field("enabled", "启用摘要", "boolean", "是否启用会话摘要。", label_en="Enabled", default=True),
			config_field("after_rounds", "触发轮次", "integer", "达到该轮数后生成摘要；兼容旧字段 summary_after_rounds。", label_en="After rounds", default=8),
			config_field("keep_recent_rounds", "摘要后保留轮次", "integer", "摘要后继续保留的最近轮次；兼容旧字段 summary_keep_recent。", label_en="Keep recent rounds", default=3),
			config_field("max_tokens", "最大摘要 Token", "integer", "摘要最大长度；兼容旧字段 summary_max_length。", label_en="Max tokens", default=800),
			config_field("max_failures", "最大失败次数", "integer", "摘要连续失败达到该值后熔断；兼容旧字段 max_compact_failures。", label_en="Max failures", default=3),
		], label_en="Summary"),
		object_field("file_restore", "文件恢复", "摘要场景下恢复最近读取文件片段。", [
			config_field("enabled", "启用文件恢复", "boolean", "是否把最近读取文件摘要注入上下文。", label_en="Enabled", default=True),
			config_field("max_files", "最大文件数", "integer", "最多缓存的文件数量。", label_en="Max files", default=5),
			config_field("max_chars_per_file", "单文件最大字符", "integer", "每个文件缓存的最大字符数。", label_en="Max chars per file", default=4000),
			config_field("max_total_chars", "总最大字符", "integer", "所有文件缓存的总字符上限。", label_en="Max total chars", default=12000),
		], label_en="File restore"),
		object_field("tool_summary", "工具摘要", "聚合工具观察并生成摘要。", [
			config_field("enabled", "启用工具摘要", "boolean", "是否启用工具结果摘要。", label_en="Enabled", default=False),
			config_field("keep", "保留条数", "integer", "保留的工具摘要条数。", label_en="Keep", default=8),
			config_field("max_chars", "最大字符", "integer", "单次工具摘要最大字符数。", label_en="Max chars", default=1200),
			config_field("max_observations", "最大观察数", "integer", "一次摘要最多纳入的工具观察数量。", label_en="Max observations", default=20),
		], label_en="Tool summary"),
		object_field("durable_tools", "持久工具结果", "压缩后仍必须保留的关键工具结果。", [
			array_field("names", "工具名", "按工具名保留关键结果。", _string_item(), label_en="Tool names", default=["agent_call", "knowledge_search"]),
			array_field("capabilities", "能力", "按 capability 保留关键结果。", _string_item(), label_en="Capabilities", default=["agent_call", "knowledge_search"]),
			config_field("keep", "保留条数", "integer", "最多保留的持久工具结果数量。", label_en="Keep", default=12),
			config_field("max_chars", "最大字符", "integer", "每条持久结果最大字符数。", label_en="Max chars", default=4000),
		], label_en="Durable tools"),
		object_field("recall", "召回", "从资源或历史消息召回相关上下文。", [
			config_field("enabled", "启用召回", "boolean", "是否启用上下文召回。", label_en="Enabled", default=True),
			config_field("resource", "资源名", "string", "召回资源名称；为空时使用本地回退召回。", label_en="Resource", default=""),
			config_field("top_k", "召回数量", "integer", "最多召回的条目数。", label_en="Top K", default=12),
			config_field("token_limit", "召回 Token 上限", "integer", "召回上下文 token 预算。", label_en="Token limit", default=4000),
			config_field("full_threshold", "完整阈值", "number", "高相关度条目使用完整内容的分数阈值。", label_en="Full threshold", default=0.72),
			config_field("compressed_threshold", "压缩阈值", "number", "低于该阈值的条目会被过滤或压缩。", label_en="Compressed threshold", default=0.35),
		], label_en="Recall"),
		object_field("boundary", "边界持久化", "跨轮次保存摘要、缓存和压缩边界。", [
			config_field("enabled", "启用边界", "boolean", "是否保存压缩边界状态。", label_en="Enabled", default=True),
			config_field("resource", "资源名", "string", "外部边界存储资源名；为空时由 kv_store 或内存存储决定。", label_en="Resource", default=""),
		], label_en="Boundary"),
	],
	display_name_en="Context compression",
)


MEMORY_CONFIG_SCHEMA = config_schema(
	"memory",
	"记忆系统",
	"控制记忆提取、存储、召回和隐私。",
	[
		config_field("context_budget", "上下文预算", "integer", "注入记忆上下文的字符预算。", label_en="Context budget", default=2000),
		config_field("decay_half_life", "衰减半衰期", "integer", "记忆重要性衰减半衰期天数。", label_en="Decay half life", default=7),
		config_field("dedup_threshold", "去重阈值", "number", "内容相似度达到该阈值时视为重复。", label_en="Dedup threshold", default=0.85),
		config_field("min_content_length", "最小内容长度", "integer", "自动提取记忆时的最小内容长度。", label_en="Min content length", default=50),
		config_field("namespace", "命名空间", "string", "记忆命名空间。", label_en="Namespace", default="default"),
		array_field("scope_keys", "作用域键", "从执行 metadata 中取作用域的 key 列表。", _string_item(), label_en="Scope keys", default=["tenant_id", "user_id", "agent_name"]),
		config_field("session_scope", "会话作用域", "boolean", "是否把 session_id 纳入记忆作用域。", label_en="Session scope", default=False),
		config_field("sensitive_policy", "敏感信息策略", "string", "敏感内容处理策略。", label_en="Sensitive policy", default="redact", enum=["redact", "reject", "allow"]),
		array_field("sensitive_patterns", "敏感模式", "自定义敏感信息正则列表。", _string_item(), label_en="Sensitive patterns", default=[r"[\w.\-+]+@[\w.\-]+\.[A-Za-z]{2,}", r"\b(?:\+?\d[\d\s\-()]{7,}\d)\b", r"\b\d{13,19}\b"]),
		config_field("max_memories", "最大记忆数", "integer", "内存缓存中保留的最大记忆数量。", label_en="Max memories", default=10000),
		config_field("ttl_days", "TTL 天数", "integer", "记忆过期天数，0 表示不过期。", label_en="TTL days", default=0),
		config_field("auto_extract", "自动提取", "boolean", "是否在轮次结束时自动提取记忆。", label_en="Auto extract", default=True),
	],
	display_name_en="Memory",
)


KNOWLEDGE_CONFIG_SCHEMA = config_schema(
	"knowledge",
	"知识库",
	"控制知识源加载、混合检索、过滤、重排和 query rewrite；外部资源通过 mounts 注入。",
	[
		array_field("sources", "知识源", "本地知识源路径列表。", _string_item(), label_en="Sources"),
		config_field("chunk_size", "切块大小", "integer", "知识文档切块最大字符数。", label_en="Chunk size", default=512),
		config_field("chunk_overlap", "切块重叠", "integer", "相邻切块的重叠字符数。", label_en="Chunk overlap", default=50),
		config_field("namespace", "命名空间", "string", "知识库命名空间。", label_en="Namespace", default=""),
		config_field("filters", "默认过滤器", "object", "默认检索过滤器。", label_en="Filters", default={}),
		config_field("candidate_k", "候选数量", "integer", "混合检索候选数量。", label_en="Candidate K", default=30),
		config_field("include_trace", "包含追踪", "boolean", "检索结果是否默认包含 trace 信息。", label_en="Include trace", default=False),
		config_field("metadata", "默认元数据", "object", "导入知识源时附加的默认 metadata。", label_en="Metadata", default={}),
		object_field("rerank", "重排", "检索结果重排配置。", [
			config_field("mode", "模式", "string", "重排模式。外部 reranker 只能通过 mounts['knowledge.reranker'] 注入。", label_en="Mode", default="score", enum=["score", "llm"]),
		], label_en="Rerank"),
		object_field("query_rewrite", "查询改写", "查询改写配置。", [
			config_field("enabled", "启用查询改写", "boolean", "是否使用 utility LLM 做查询改写。", label_en="Enabled", default=False),
		], label_en="Query rewrite"),
	],
	display_name_en="Knowledge",
)


SAFETY_CONFIG_SCHEMA = config_schema(
	"safety",
	"安全防护",
	"输入清洗、提示注入检测和工具输出脱敏。",
	[
		config_field("prompt_injection", "提示注入检测", "boolean", "是否启用提示注入检测。", label_en="Prompt injection", default=True),
		config_field("pii_masking", "PII 脱敏", "boolean", "是否对工具输出做敏感信息脱敏。", label_en="PII masking", default=False),
		config_field("input_sanitize", "输入清洗", "boolean", "是否清洗用户输入中的标签和过长内容。", label_en="Input sanitize", default=True),
	],
	display_name_en="Safety",
)


RISK_GUARD_CONFIG_SCHEMA = config_schema(
	"risk_guard",
	"风险分级",
	"为工具调用应用自定义风险规则。",
	[
		array_field("rules", "规则", "自定义风险规则列表。", _object_item(), label_en="Rules"),
	],
	display_name_en="Risk guard",
)


HUMAN_IN_THE_LOOP_CONFIG_SCHEMA = config_schema(
	"human_in_the_loop",
	"人工审批",
	"危险工具调用审批和 ask_human 工具配置。",
	[
		config_field("risk_threshold", "风险阈值", "string", "达到该风险等级时请求人工审批。", label_en="Risk threshold", default="dangerous", enum=["safe", "moderate", "dangerous", "blocked"]),
		config_field("timeout", "审批超时", "integer", "等待人工审批或回复的秒数。", label_en="Timeout", default=300),
		array_field("auto_approve", "自动批准工具", "无需审批的工具名列表。", _string_item(), label_en="Auto approve"),
		config_field("ask_human", "启用询问工具", "boolean", "是否暴露 ask_human 工具。", label_en="Ask human", default=True),
	],
	display_name_en="Human in the loop",
)


COLLABORATION_CONFIG_SCHEMA = config_schema(
	"collaboration",
	"Agent 协作",
	"Agent 间调用和旁路推演工具配置。",
	[
		config_field("max_depth", "最大调用深度", "integer", "Agent 嵌套调用最大深度。", label_en="Max depth", default=MAX_CALL_DEPTH),
		config_field("timeout", "调用超时", "number", "Agent 调用默认超时秒数。", label_en="Timeout", default=60.0),
		array_field("allowed_agents", "允许 Agent", "允许调用的 Agent 名称列表；为空表示不限制。", _string_item(), label_en="Allowed agents"),
		array_field("denied_agents", "拒绝 Agent", "禁止调用的 Agent 名称列表。", _string_item(), label_en="Denied agents"),
		config_field("expose_agent_list", "暴露 Agent 列表", "boolean", "是否暴露 agent_list 工具。", label_en="Expose agent list", default=True),
		config_field("allow_self_call", "允许自调用", "boolean", "是否允许 Agent 调用自身。", label_en="Allow self call", default=False),
		config_field("orchestration_resource", "推演资源", "string", "旁路多 Agent 推演服务资源名。", label_en="Orchestration resource", default="orchestration"),
	],
	display_name_en="Collaboration",
)


SWARM_CONFIG_SCHEMA = config_schema(
	"swarm",
	"并行调度",
	"并行 fan-out 调度多个 Agent 的配置。",
	[
		config_field("max_concurrent", "最大并发", "integer", "并行调度的最大并发任务数。", label_en="Max concurrent", default=5),
		config_field("max_depth", "最大调用深度", "integer", "Agent 嵌套调用最大深度。", label_en="Max depth", default=MAX_CALL_DEPTH),
		config_field("timeout", "总超时", "number", "swarm 总超时秒数。", label_en="Timeout", default=60.0),
		config_field("task_timeout", "单任务超时", "number", "单任务默认超时；默认继承 timeout。", label_en="Task timeout", default=60.0),
		config_field("allow_self_call", "允许自调用", "boolean", "是否允许 Agent 调用自身。", label_en="Allow self call", default=False),
		array_field("allowed_agents", "允许 Agent", "允许调用的 Agent 名称列表；为空表示不限制。", _string_item(), label_en="Allowed agents"),
		array_field("denied_agents", "拒绝 Agent", "禁止调用的 Agent 名称列表。", _string_item(), label_en="Denied agents"),
		config_field("max_result_bytes", "最大结果字节", "integer", "超过该字节数的结果会尝试外部化。", label_en="Max result bytes", default=256_000),
		config_field("failure_policy", "失败策略", "string", "任务失败时的聚合策略。", label_en="Failure policy", default="best_effort", enum=["best_effort", "fail_fast"]),
	],
	display_name_en="Swarm",
)


OUTPUT_FORMAT_CONFIG_SCHEMA = config_schema(
	"output_format",
	"输出格式",
	"结果结构化、约束校验和自动修复配置。",
	[
		config_field("type", "类型", "string", "输出格式类型。", label_en="Type", default=""),
		config_field("schema", "Schema", "object", "JSON Schema 或结构化输出契约。", label_en="Schema", default={}),
		config_field("template", "模板", "string", "输出模板文本。", label_en="Template", default=""),
		config_field("constraints", "约束", "string", "输出必须满足的额外约束。", label_en="Constraints", default=""),
		config_field("strict", "严格模式", "boolean", "是否严格执行输出校验。", label_en="Strict", default=False),
		config_field("auto_repair", "自动修复", "boolean", "校验失败时是否尝试自动修复；兼容旧字段 repair。", label_en="Auto repair", default=True),
		config_field("repair_attempts", "修复次数", "integer", "自动修复最大尝试次数。", label_en="Repair attempts", default=1),
		config_field("repair_timeout", "修复超时", "number", "单次修复超时秒数。", label_en="Repair timeout", default=30),
		config_field("max_repair_chars", "最大修复字符", "integer", "送入修复流程的最大字符数。", label_en="Max repair chars", default=3000),
		config_field("max_output_chars", "最大输出字符", "integer", "最终输出字符上限，0 表示不限制。", label_en="Max output chars", default=0),
		config_field("schema_id", "Schema ID", "string", "Schema 标识；兼容旧字段 contract_name。", label_en="Schema ID", default=""),
		config_field("schema_version", "Schema 版本", "string", "Schema 版本。", label_en="Schema version", default=""),
	],
	display_name_en="Output format",
)


TRACING_CONFIG_SCHEMA = config_schema(
	"tracing",
	"链路追踪",
	"标准化 trace/span 采集、采样、脱敏和导出配置。",
	[
		config_field("output", "输出", "string", "trace 输出目标。", label_en="Output", default="log", enum=["log", "store", "callback", "exporter"]),
		config_field("include_arguments", "包含参数", "boolean", "span 中是否记录工具参数。", label_en="Include arguments", default=False),
		config_field("include_result", "包含结果", "boolean", "span 中是否记录工具结果。", label_en="Include result", default=False),
		config_field("max_argument_length", "参数最大长度", "integer", "单个参数记录最大长度。", label_en="Max argument length", default=2000),
		config_field("max_result_length", "结果最大长度", "integer", "结果记录最大长度。", label_en="Max result length", default=200),
		config_field("max_error_length", "错误最大长度", "integer", "错误记录最大长度。", label_en="Max error length", default=2000),
		config_field("sample_rate", "采样率", "number", "trace 采样率，范围 0 到 1。", label_en="Sample rate", default=1.0),
		config_field("sample_errors", "采样错误", "boolean", "是否强制采样错误 span。", label_en="Sample errors", default=True),
		config_field("slow_span_ms", "慢 Span 阈值", "integer", "达到该耗时毫秒数的 span 会被采样，0 表示关闭。", label_en="Slow span ms", default=0),
		config_field("recent_limit", "最近 Span 数", "integer", "内存中保留的最近 span 数。", label_en="Recent limit", default=200),
		config_field("queue_limit", "队列上限", "integer", "异步导出队列上限。", label_en="Queue limit", default=1000),
		array_field("redact_keys", "脱敏键", "额外脱敏字段名；会与内置敏感键合并。", _string_item(), label_en="Redact keys"),
		config_field("audit_mode", "审计模式", "boolean", "是否以审计模式记录 trace。", label_en="Audit mode", default=False),
	],
	display_name_en="Tracing",
)


COST_STATISTICS_CONFIG_SCHEMA = config_schema(
	"cost_statistics",
	"成本统计",
	"统计 LLM token 和工具调用数量。",
	[],
	display_name_en="Cost statistics",
)


REFLEXION_CONFIG_SCHEMA = config_schema(
	"reflexion",
	"自我反思",
	"每轮后基于工具调用进行自我反思。",
	[
		config_field("start_after_round", "开始轮次", "integer", "从该轮次后开始反思。", label_en="Start after round", default=3),
		config_field("max_reflection_len", "最大反思长度", "integer", "保留的反思文本最大长度。", label_en="Max reflection length", default=200),
	],
	display_name_en="Reflexion",
)


REPETITION_GUARD_CONFIG_SCHEMA = config_schema(
	"repetition_guard",
	"重复防护",
	"检测重复工具调用、重复回复和重复工具结果。",
	[
		array_field("rules", "规则", "重复检测规则列表。", _object_item([
			config_field("type", "类型", "string", "规则类型。", label_en="Type", enum=["same_call", "same_tool", "total_tool", "response_pattern", "result_pattern"]),
			config_field("limit", "限制", "integer", "触发重复防护的次数阈值。", label_en="Limit", default=None),
			config_field("pattern", "模式", "string", "响应或结果匹配用正则。", label_en="Pattern", default=None),
		]), label_en="Rules", default=[
			{"type": "same_call", "limit": 3},
			{"type": "same_tool", "limit": 20},
			{"type": "total_tool", "limit": 100},
		]),
	],
	display_name_en="Repetition guard",
)


HOOKS_CONFIG_SCHEMA = config_schema(
	"hooks",
	"声明式钩子",
	"声明式配置 LLM、工具、错误、计划事件的规则。",
	[
		array_field("rules", "规则", "钩子规则列表。", _object_item([
			config_field("event", "事件", "string", "触发事件。", label_en="Event", enum=["pre_tool_call", "post_tool_call", "pre_llm_call", "on_error", "on_plan_created", "on_step_completed"]),
			config_field("condition", "条件", "string", "安全表达式条件。", label_en="Condition", default=""),
			config_field("action", "动作", "string", "满足条件后执行的动作。", label_en="Action", default=""),
			config_field("params", "参数", "object", "动作参数。", label_en="Params", default={}),
		]), label_en="Rules"),
	],
	display_name_en="Hooks",
)


MCP_CONFIG_SCHEMA = config_schema(
	"mcp",
	"MCP 工具",
	"连接 MCP Server 并将远端工具注册进引擎。",
	[
		array_field("servers", "服务器", "MCP Server 配置列表。", config_field("", "服务器项", "object", "单个 MCP Server 配置。", label_en="Server item", children=[
			config_field("name", "名称", "string", "MCP Server 名称。", label_en="Name", default="unnamed"),
			config_field("transport", "传输", "string", "MCP 传输类型。", label_en="Transport", default=None, enum=["stdio", "http", "sse", "sdk"]),
			config_field("command", "命令", "string", "stdio 传输启动命令；由宿主或插件运行时决定。", label_en="Command", default=None),
			array_field("args", "参数", "stdio 命令参数。", _string_item(), label_en="Args"),
			config_field("env", "环境变量", "object", "stdio 进程环境变量。", label_en="Environment", default={}),
			config_field("url", "URL", "string", "HTTP/SSE MCP 服务地址；由宿主或插件运行时决定。", label_en="URL", default=None),
				config_field("headers", "请求头", "object", "HTTP/SSE 请求头。", label_en="Headers", default={}),
				config_field("timeout", "超时", "number", "连接和请求超时秒数；由具体 transport 决定默认值。", label_en="Timeout", default=None),
			]), label_en="Servers"),
		array_field("allowed_tools", "允许工具", "允许暴露的 MCP 工具名列表。", _string_item(), label_en="Allowed tools"),
		array_field("denied_tools", "拒绝工具", "禁止暴露的 MCP 工具名列表。", _string_item(), label_en="Denied tools"),
		config_field("tool_overrides", "工具覆盖", "object", "按工具名覆盖 read_only/risk_level/capability/timeout/retryable。", label_en="Tool overrides", default={}),
		config_field("capability", "默认能力", "string", "MCP 工具默认 capability。", label_en="Capability", default=""),
		config_field("risk_level", "默认风险", "string", "MCP 工具默认风险等级。", label_en="Risk level", default="moderate", enum=["safe", "moderate", "dangerous", "blocked"]),
		config_field("read_only", "默认只读", "boolean", "MCP 工具默认是否只读。", label_en="Read only", default=False),
		config_field("max_result_bytes", "最大结果字节", "integer", "超过该字节数的 MCP 输出会尝试外部化。", label_en="Max result bytes", default=512_000),
	],
	display_name_en="MCP tools",
)


GRAPH_CONFIG_SCHEMA = config_schema(
	"graph",
	"知识图谱",
	"实体关系图谱检索、治理和 CRUD 配置。",
	[
		array_field("sources", "图谱源", "图谱数据源路径列表。", _string_item(), label_en="Sources"),
		config_field("namespace", "命名空间", "string", "图谱命名空间。", label_en="Namespace", default="default"),
		config_field("allow_writes", "允许写入", "boolean", "是否允许 upsert 实体和关系。", label_en="Allow writes", default=True),
		config_field("allow_deletes", "允许删除", "boolean", "是否允许删除实体和关系。", label_en="Allow deletes", default=True),
		array_field("allowed_entity_types", "允许实体类型", "允许写入的实体类型列表。", _string_item(), label_en="Allowed entity types"),
		array_field("denied_entity_types", "拒绝实体类型", "禁止写入的实体类型列表。", _string_item(), label_en="Denied entity types"),
		array_field("allowed_relation_types", "允许关系类型", "允许写入的关系类型列表。", _string_item(), label_en="Allowed relation types"),
		array_field("denied_relation_types", "拒绝关系类型", "禁止写入的关系类型列表。", _string_item(), label_en="Denied relation types"),
		config_field("max_entities", "最大实体数", "integer", "图谱最大实体数量。", label_en="Max entities", default=100_000),
		config_field("max_relations", "最大关系数", "integer", "图谱最大关系数量。", label_en="Max relations", default=500_000),
		config_field("max_depth", "最大深度", "integer", "图谱搜索最大深度。", label_en="Max depth", default=3),
		config_field("default_limit", "默认限制", "integer", "列表和搜索默认返回数量。", label_en="Default limit", default=20),
		config_field("max_limit", "最大限制", "integer", "列表和搜索最大返回数量。", label_en="Max limit", default=100),
		config_field("max_name_length", "名称最大长度", "integer", "实体和关系名称最大长度。", label_en="Max name length", default=256),
		config_field("max_description_length", "描述最大长度", "integer", "实体和关系描述最大长度。", label_en="Max description length", default=4000),
		config_field("max_result_bytes", "最大结果字节", "integer", "超过该字节数的图谱结果会尝试外部化。", label_en="Max result bytes", default=256_000),
		config_field("include_metadata", "包含元数据", "boolean", "结果中是否包含 metadata。", label_en="Include metadata", default=True),
		config_field("audit", "启用审计", "boolean", "是否记录图谱写操作审计。", label_en="Audit", default=True),
	],
	display_name_en="Graph",
)


SKILL_CONFIG_SCHEMA = config_schema(
	"skill",
	"技能系统",
	"技能目录加载、过滤和受控脚本执行配置。",
	[
		array_field("paths", "技能路径", "技能目录路径列表。", _string_item(), label_en="Paths"),
		array_field("allowed_skills", "允许技能", "允许加载的技能名列表；为空表示不限制。", _string_item(), label_en="Allowed skills"),
		array_field("denied_skills", "拒绝技能", "禁止加载的技能名列表。", _string_item(), label_en="Denied skills"),
		config_field("allow_scripts", "允许脚本", "boolean", "是否允许运行技能脚本。", label_en="Allow scripts", default=True),
		array_field("allowed_script_names", "允许脚本名", "允许执行的脚本相对路径列表。", _string_item(), label_en="Allowed script names"),
		array_field("allowed_extensions", "允许扩展名", "允许执行的脚本扩展名。", _string_item(), label_en="Allowed extensions", default=[".py", ".sh"]),
		config_field("duplicate_policy", "重复策略", "string", "遇到重复技能名时的处理策略。", label_en="Duplicate policy", default="error", enum=["skip", "replace", "error"]),
		config_field("timeout", "脚本超时", "integer", "脚本执行超时秒数。", label_en="Timeout", default=60),
		config_field("stdout_limit", "标准输出上限", "integer", "脚本 stdout 捕获字符上限。", label_en="Stdout limit", default=1500),
		config_field("stderr_limit", "标准错误上限", "integer", "脚本 stderr 捕获字符上限。", label_en="Stderr limit", default=500),
		config_field("max_skill_content_chars", "技能内容上限", "integer", "单个技能说明内容最大字符数。", label_en="Max skill content chars", default=100_000),
		config_field("max_result_bytes", "最大结果字节", "integer", "超过该字节数的脚本结果会尝试外部化。", label_en="Max result bytes", default=256_000),
	],
	display_name_en="Skill",
)
