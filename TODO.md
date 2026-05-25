# AxcAgentEngine 职责拆分与功能等价 TODO

目标：对 `axc_agent_engine` 中职责过重的类和函数做结构性拆分，但第一阶段必须保持功能、对外 API、工具 schema、事件序列、checkpoint、audit、metadata、ToolOutput 字段完全一致。

执行原则：

- 先补行为锁定测试，再拆代码。
- 第一阶段只做移动、抽类、抽函数，不做行为优化。
- 对外类名、方法签名、工具名、工具参数、返回 JSON 字段保持不变。
- 每次拆分后都用同一套 fake LLM / fake tool / fake store 做重构前后对比。
- 没有测试锁住的行为，不允许重构核心路径。
- 代码是负债，功能才是价值；拆分不是为了增加文件和类，而是为了减少理解成本、降低变更风险、保住功能价值。
- 能删除的旧代码优先删除；能合并的重复逻辑优先合并；只有当抽象能减少真实复杂度时才新增抽象。
- 每个新类必须有单一职责、清晰输入输出、可独立测试；禁止把原来的大类机械拆成一堆互相强耦合的小类。
- 拆分后总代码量原则上不应显著增加；如果增加，必须用测试隔离、重复消除、可维护性收益证明合理。

功能等价总验收：

```text
同一 fake 环境 + 同一输入下，重构前后必须满足：
events == events
message_store == message_store
tool_outputs == tool_outputs
checkpoints == checkpoints
audit_events == audit_events
metadata == metadata
usage == usage
```

## 当前实施状态

状态更新日期：2026-05-19。

已完成并通过全量测试：

- [x] 基线测试确认：`python3 -m pytest -q`，结果 `1154 passed`。
- [x] 行为锁定说明：本轮复用现有模块测试、契约测试和集成测试作为第一阶段等价护栏；未新增大面积 snapshot，避免为了拆代码再制造测试负债。
- [x] `SkillPlugin._tool_run_script` 第一阶段拆分：新增 `SkillScriptRequest`、`ResolvedSkillScript`，拆出请求标准化、skill 查找、脚本路径校验、runner 选择、payload/artifact 组装。
- [x] `Executor` 第一阶段拆分：新增 `CheckpointRecorder` 和 `ToolCallFlow`，checkpoint state、tool event 顺序和 message append 行为保持一致。
- [x] `MemoryPlugin` 第一阶段拆分：新增 `MemoryScopeResolver` 和 `MemoryPrivacyPolicy`，并在强制清理阶段删除 `_scope_id/_key_prefix/_sanitize_content` 旧委托。
- [x] `KnowledgePlugin` 减负：删除已被 `LocalFileIngestionPipeline` 替代且无外部引用的旧 ingestion 方法。
- [x] `OutputFormatService` 第一阶段拆分：新增 `OutputValidator`，并在强制清理阶段删除私有兼容委托。
- [x] `tools/executor.py` 第一阶段拆分：新增 `ToolArgumentValidator`，并在强制清理阶段删除 `validate_arguments()` 旧入口。

已完成：

- [x] Memory repository/vector/tool handler/extraction service 深拆。
- [x] Knowledge search/indexer/result formatter 深拆。
- [x] Executor round runner/lifecycle/POR bridge 深拆。
- [x] PORRunner 统一 StepRunner/reducer 深拆。
- [x] Graph/BuiltinTools/Tracing/Compress/MCP/Agent 调用类插件深拆。
- [x] API chat、sandbox、sidecar、基础设施模块拆分。
- [x] 强制删除旧 sandbox 兼容门面：`axc_agent_engine.runtime.sandbox` 不再可导入，内部调用与测试全部切到新模块路径。
- [x] 强制删除第一批旧委托入口：chat route helper、OpenAIClient parser/error wrapper、Memory 私有委托、OutputFormat 私有委托、tools `validate_arguments()`、sidecar `_event_to_dict`。
- [x] 强制删除 sidecar 分析旧外观类：`CostOptimizer`、`AgentDistiller`、`FailureMiner`，调用方直接使用 report builder / estimator。
- [x] 高把握废代码清理：删除零引用的 `PluginHookRunner.transform_async()`、`is_valid_plan()`、`TransactionRouter.should_enter_por()`、builtin tools 私有转发 `_workspace_required_error()` / `_unsafe_workspace_allowed()`；`docs/ARCHITECTURE.md` 同步改为当前 `TransactionRouter.route()` 流程。
- [x] 高把握清理复核：`rg` 确认上述旧符号无残留，相关测试 `106 passed`，全量测试 `1154 passed`，`compileall` 通过。
- [x] sidecar 结构统一：`agent_selector`、`cost_optimizer`、`distiller`、`failure_miner`、`orchestration` 均从根目录单文件改为独立 package，保持“一种旁路一个目录”；`eval`、`multi_agent`、`simulation` 原本已是目录型旁路，保持不动。
- [x] sidecar 边界文档：新增 `axc_agent_engine/sidecar/README.md`，明确旁路不从 Agent YAML 加载、不参与默认 ReAct/POR、不污染核心执行语义，并列出每个旁路目录职责。
- [x] sidecar 注释规范：sidecar 内 docstring 和普通说明注释统一为中英双语；脚本检测 `docstring_bilingual_violations=0`，sidecar 相关测试 `6 passed`。
- [x] 项目 README 统一：删除独立 `README.zh_CN.md`，根 `README.md` 改为中文段落 + 英文段落的双语配对格式，避免中英文两份文档漂移。
- [x] 插件注册时机修正：新增 Engine 级 `PluginRegistry`，`Engine(..., plugin_registry=registry)` 注入；默认注册表为空，内置插件和自定义插件都必须由宿主显式 `register/register_many` 后，Agent YAML 才能启用。
- [x] 插件加载破坏式清理：删除 import-time `@register_builtin` 副作用和 YAML `module` 动态 import 语义；`load_plugins()` 只从传入的 `PluginRegistry` 创建插件，未注册插件按 `required` 决定失败或跳过。

刻意保留：

- `build_plan_resume_summary()`、`MessageStore.get_recent()`、`MessageStore.get_first()`、`MessageStore.rollback()`：有测试直接覆盖，属于明确公共行为，不按废代码删除。
- builtin tools 的 `_get_time/_file_read/_shell/_result_read` 等模块级函数：它们是 `_ALL_TOOLS` 注册表里的真实执行入口，也被测试直接覆盖，不属于废弃包装。

说明：本轮按 TODO 全部实施，并以模块 contract tests、集成 tests、全量测试矩阵作为等价护栏；未为了“架构化”新增无真实职责的扩展点。代码是负债，功能才是价值，本次只保留能降低理解成本或锁定行为的拆分。

## P0：统一拆分流程

每个拆分项都必须按下面流程走，不能跳步：

1. **功能盘点**：列出原始入口、工具名、hook、事件、metadata、checkpoint、audit、错误文案、外部依赖。
2. **行为锁定**：补 golden tests 和 public contract snapshot，先证明当前行为可复现。
3. **边界设计**：只围绕现有职责划边界，不引入新功能；明确新类输入输出。
4. **最小移动**：先移动纯函数和无状态逻辑，再移动有状态逻辑；每次只移动一个职责。
5. **重复消除**：拆出后删除旧路径、死代码、重复 helper，避免新旧两套实现并存。
6. **等价对比**：每一步跑模块 golden tests，关键链路跑集成 golden tests。
7. **代码审查**：检查是否减少了单类职责、降低了分支复杂度、没有新增不必要抽象。
8. **回滚条件**：任何 public contract 差异、golden diff、测试无法解释的快照变化，都回滚该步。

每个拆分 PR/提交必须回答：

- 原始功能是什么。
- 新职责边界是什么。
- 删除了哪些重复或死代码。
- 新增了哪些类，为什么它们不是过度设计。
- 哪些 golden tests 证明功能完全一致。
- 哪些风险仍未覆盖。

禁止事项：

- 禁止一边拆分一边改业务行为。
- 禁止因为“看起来更架构化”而新增 manager/factory/service。
- 禁止保留原实现和新实现双路径长期共存。
- 禁止新增只有一个方法、没有明确状态或边界价值的空壳类。
- 禁止把 dict/Any 继续向更多层扩散；内部新边界优先用 dataclass/TypedDict/Protocol 表达。
- 禁止为了拆文件而拆文件；文件数增加必须换来职责更清楚或测试更容易。

## P0：先建立功能等价护栏

### TODO-0.1 建立重构前 golden master

原始功能：

- 当前 `Executor`、`PORRunner`、`MemoryPlugin`、`KnowledgePlugin`、`SkillPlugin`、`GraphPlugin`、`BuiltinToolsPlugin` 都直接作为行为源。
- 现有行为不论实现是否优雅，都先作为第一阶段兼容基准。

拆分后要求：

- 新拆出的 service / repository / presenter / runner 不改变任何外部可观察行为。
- Plugin 仍然暴露同样 hook 和 tool。

对比工作：

- 新增 fake 依赖：`FakeLLMProvider`、`FakeUtilityLLM`、`FakeKVStore`、`FakeVectorStore`、`FakeAuditSink`、`FakeCheckpointStore`、`FakeResultStore`、`FakeCommandExecutor`。
- 固定时间、固定 uuid、固定 embedding、固定 LLM 响应。
- 为每条关键链路记录 golden JSON：events、messages、tool outputs、audit、checkpoint、metadata、usage。
- 重构前跑一次生成快照，重构后只允许完全一致。

### TODO-0.2 明确允许差异清单

原始功能：

- 日志文本、内部私有类、私有方法名没有稳定契约。
- 对外工具 schema、事件、返回 payload、checkpoint state、metadata key 实际已被调用方依赖。

拆分后要求：

- 第一阶段允许差异：内部文件结构、私有类名、私有函数名。
- 第一阶段不允许差异：工具名、工具参数 schema、ToolOutput 字段、Event 序列、checkpoint 字段、audit 字段、metadata key、错误字符串。

对比工作：

- 增加 `assert_public_contract_unchanged()`，对所有插件工具 schema 做快照。
- 增加 `assert_event_sequence_unchanged()`，对 ReAct/POR/stream 做事件序列快照。
- 若确实需要改行为，单独开后续 TODO，不混入拆分类提交。

## P0：MemoryPlugin 职责拆分

目标文件：

- [memory/plugin.py](/Users/zhaoxin/Desktop/AxcRavenSystem/AxcAgentEngine/axc_agent_engine/plugins/builtin/memory/plugin.py)
- [memory/support/service.py](/Users/zhaoxin/Desktop/AxcRavenSystem/AxcAgentEngine/axc_agent_engine/plugins/builtin/memory/support/service.py)

原始功能：

- `MemoryPlugin` 当前约 729 行、40 个方法。
- 负责插件初始化、记忆加载、上下文注入、round end 自动提取、execution end 衰减清理。
- 负责 KVStore 持久化、scope key、scope cache、background persist。
- 负责敏感信息 redact/reject。
- 负责冲突检测、去重、TTL、容量淘汰。
- 负责 embedding client 初始化、向量 upsert/search/delete。
- 负责工具：`memory_add`、`memory_add_fact`、`memory_add_lesson`、`memory_search`、`memory_list`、`memory_delete`、`memory_export`。
- 负责 audit event 和 `exec_ctx.state.metadata["memory"]` 同步。

拆分后设计：

- `MemoryPlugin`：只保留 `initialize()`、hook、`get_tools()`，作为薄门面。
- `MemoryRepository`：负责 KVStore 的 `load_scope()`、`save_memory()`、`delete_memory()`、`persist_touched()`。
- `MemoryScopeResolver`：负责 namespace、scope_keys、session_scope、key_prefix。
- `MemoryPrivacyPolicy`：负责 `_sanitize_content()` 和敏感策略统计。
- `MemoryExtractionService`：负责 `_extract_with_llm()`、fallback 到用户消息自动记忆。
- `MemoryLifecycleService`：负责 decay、TTL、capacity、conflict、dedup。
- `MemoryVectorIndex`：负责 embedding client、`upsert_vector()`、`delete_vectors()`、`retrieve_vector_items()`。
- `MemoryToolHandlers`：负责工具入参校验、调用 service、返回 `ToolOutput`。
- `MemoryAuditRecorder`：负责 `_audit_memory()`。
- `MemoryMetadataSync`：负责写 `exec_ctx.state.metadata["memory"]`。

功能等价对比：

- `memory_add`：空 content、非法 layer、敏感 reject、重复记忆、冲突记忆、正常写入，返回 payload 完全一致。
- `memory_search`：纯 lexical、vector store 成功、vector store 失败 fallback、scope 过滤，结果顺序一致。
- `memory_delete`：存在/不存在、同时删除 vector id，返回 payload 一致。
- `memory_export/list`：layer filter、limit、排序、public 字段一致。
- `on_round_end`：utility_llm JSON 输出、pipe 格式输出、空输出、异常，新增记忆和统计一致。
- `on_execution_end`：decay 删除、ttl 删除、background task flush，KV 删除和 metadata 一致。
- `inject_context`：上下文内容、access_count、last_accessed_at、异步 persist 行为一致。

落地步骤：

- [x] 跑通现有 MemoryPlugin golden/contract tests。
- [x] 先抽 `MemoryScopeResolver`，不改行为。
- [x] 再抽 `MemoryPrivacyPolicy`，不改错误文案。
- [x] 再抽 `MemoryRepository`，保持 KV key 完全一致。
- [x] 再抽 `MemoryVectorIndex`，保持 vector metadata 完全一致。
- [x] 最后抽 `MemoryToolHandlers` 和 `MemoryExtractionService`。
- [x] 每一步跑 memory golden tests。

## P0：KnowledgePlugin 职责拆分

目标文件：[knowledge/plugin.py](/Users/zhaoxin/Desktop/AxcRavenSystem/AxcAgentEngine/axc_agent_engine/plugins/builtin/knowledge/plugin.py)

原始功能：

- `KnowledgePlugin` 当前约 595 行、28 个方法。
- 负责配置解析、source ingest、chunk、embedding 增量构建、manifest 管理、BM25、本地 sparse vector、vector store search、RRF merge、citation/highlights/trace、工具输出。
- 当前已经引入 `LocalFileIngestionPipeline`、`SemanticChunker`、`HybridRetriever`、`InMemoryKnowledgeIndexStore`，但 plugin 内仍保留 `_resolve_path()`、`_load_file()`、`_load_pdf()`、`_smart_chunk()`、`_chunk_by_paragraph()` 等旧职责。

拆分后设计：

- `KnowledgePlugin`：只保留初始化、hook、`get_tools()`、工具入口。
- `KnowledgeSourceLoader`：封装 `LocalFileIngestionPipeline`，产出 `KnowledgeDocument` 和 chunk view。
- `KnowledgeEmbeddingIndexer`：负责 `_build_embeddings_incremental()`、manifest load/save/delete、batch embedding。
- `KnowledgeSearchService`：负责 `_hybrid_search_payload()`、BM25/vector/fallback/RRF。
- `KnowledgeResultFormatter`：负责 citation、highlights、trace、payload 格式化。
- `KnowledgeConfigFactory`：负责 filter、reranker、query rewriter、embedding client 构造。
- 删除或迁移旧的 `_resolve_path()`、`_load_file()`、`_load_pdf()`、`_smart_chunk()`、`_chunk_by_paragraph()`，但必须先确认没有测试或调用方依赖。

功能等价对比：

- `inject_context`：同样 topic 下返回 `[相关知识]` 内容、source 行一致。
- `knowledge_search`：query 为空错误一致。
- BM25 fallback：结果顺序、score、retrieval、citation、highlights 一致。
- vector store 成功：candidate_k、top_k、min_score、metadata filter、trace 一致。
- vector store 异常：fallback 行为和日志以外的结果一致。
- manifest：source fingerprint、KV key、chunk_ids、index_version、updated_at 字段语义一致。
- filter：namespace、metadata、source、source_prefix、allowed_acl_tags 一致。

落地步骤：

- [x] 跑通现有 knowledge ingest/search/vector contract tests。
- [x] 抽 `KnowledgeResultFormatter`，先只移动 `_citation_for_chunk()`、`_highlights()`、`_trace_dict()`。
- [x] 抽 `KnowledgeSearchService`，保留原始排序和 score。
- [x] 抽 `KnowledgeEmbeddingIndexer`，保持 manifest key 和 vector metadata。
- [x] 清理旧 ingestion 方法前先跑 `rg` 确认无调用。
- [x] 每一步跑 knowledge golden tests。

## P0：Executor 主循环拆分

目标文件：[core/executor.py](/Users/zhaoxin/Desktop/AxcRavenSystem/AxcAgentEngine/axc_agent_engine/core/executor.py)

原始功能：

- `Executor._react_loop()` 当前约 98 行，是执行主状态机。
- 负责 por_first 计划生成、round 计数、取消检查、total timeout、checkpoint、message transform、stop hook、LLM 调用、fallback state_change、事件转发、message append、router、POR 切换、tool call parse/resolve/event、工具执行、tool result event、round end hook、max rounds 错误。
- `run_stream()` 还负责 execution start/complete/end hook、execution checkpoint、DONE/ERROR 包装。

拆分后设计：

- `Executor`：保留 public API：`run_stream()`、`resume_por()`、`restore_checkpoint()`、`message_store`。
- `ExecutionRunLifecycle`：封装 execution start/complete/error/end hook 和 execution checkpoint。
- `ReactLoopRunner`：封装 while loop 骨架。
- `RoundRunner`：负责单轮 `prepare -> llm -> route -> tools -> finish`。
- `ToolCallFlow`：负责 parse、resolve、yield tool_call event、execute_tool_calls、append_tool_results、yield tool_result event。
- `CheckpointRecorder`：统一保存 execution/round checkpoint。
- `PORModeBridge`：封装 `_enter_por_mode()` 和 `resume_por()` 的 runtime 构造。

功能等价对比：

- 无工具 final answer：事件顺序仍为 `STREAM_END -> DONE`。
- 有工具调用：`TOOL_CALL`、工具执行、tool message、`TOOL_RESULT` 顺序一致。
- stream 模式：实时 delta 仍从 `_stream_llm_call()` 输出，最终 message 一致。
- fallback：`STATE_CHANGE` 文案、metadata、state 清理一致。
- stop hook：提前 DONE 行为一致。
- total timeout/max rounds：错误文案一致。
- checkpoint：kind、status、sequence、state 字段一致。
- POR route：plan 为空/非空、PlanningService 异常、进入 POR 的行为一致。

落地步骤：

- [x] 跑通现有 ReAct golden/contract tests：无工具、有工具、工具失败、stop、fallback、timeout、max_rounds、stream。
- [x] 抽 `CheckpointRecorder`，保持 state 字段完全一致。
- [x] 抽 `ToolCallFlow`，保持 event 和 message append 顺序一致。
- [x] 抽 `RoundRunner`，先由 Executor 调用，不改变 public API。
- [x] 抽 `ExecutionRunLifecycle`。
- [x] 每一步跑 executor golden tests。

## P0：SkillPlugin 脚本执行拆分

目标文件：[skill/plugin.py](/Users/zhaoxin/Desktop/AxcRavenSystem/AxcAgentEngine/axc_agent_engine/plugins/builtin/skill/plugin.py)

原始功能：

- `SkillPlugin._tool_run_script()` 当前约 175 行。
- 负责 allow_scripts、skill existence、scripts_path、allowed_script_names、path escape、extension、runner、shlex args、WorkspaceCommandExecutor、stdout/stderr externalize、payload、audit、ErrorEnvelope、ToolOutput。

拆分后设计：

- `SkillPlugin`：保留工具注册和 `_tool_run_script()` 入口。
- `SkillScriptRequest`：标准化 `skill_name`、`script_name`、`args`。
- `SkillScriptPolicy`：负责是否允许脚本、脚本名 allowlist、扩展名 allowlist。
- `SkillScriptResolver`：负责 skill 查找、scripts_path、realpath、path escape 检查。
- `SkillScriptRunner`：负责 CommandSpec 和 executor.run。
- `SkillScriptPresenter`：负责 stdout/stderr externalize、payload、artifacts、ToolOutput。
- `SkillAuditRecorder`：负责 `_audit()` 和 `_script_error()`。

功能等价对比：

- scripts disabled：错误文案和 audit `skill_script_rejected` 一致。
- skill not found：错误文案、available 信息、audit code 一致。
- no scripts dir：错误一致。
- script denied/not found/path escape/extension denied：错误文案、code、allowed 字段一致。
- bad args：`ValueError` 时 `skill.bad_args` 一致。
- command 成功：argv、cwd、timeout、stdout/stderr limit、payload、summary、metadata 一致。
- command 非 0 或 timeout：仍返回 ToolOutput JSON，不变成 ToolOutput.error；audit 带 `skill.script_failed`。
- stdout/stderr externalize：artifact kind、metadata、preview 内容一致。

落地步骤：

- [x] 跑通现有 run_skill_script contract tests。
- [x] 抽 request/resolver helper，先保持 `_tool_run_script()` 调用顺序。
- [x] 抽独立 policy，不改脚本路径、扩展名、sandbox、audit 行为。
- [x] 抽 runner，不改 CommandSpec。
- [x] 抽 presenter，不改 payload 字段。
- [x] 抽 audit recorder，不改 audit 字段。

## P1：PORRunner 拆分

目标文件：[planning/por_runner.py](/Users/zhaoxin/Desktop/AxcRavenSystem/AxcAgentEngine/axc_agent_engine/planning/por_runner.py)

原始功能：

- `PORRunner` 当前约 330 行。
- 负责 plan created/resume、计划循环、串行 step、并行 step、隔离子上下文、step ReAct loop、observe、replan、finalize、plan checkpoint。
- `_execute_step_isolated()` 和 `_execute_single_step()` 有重复逻辑，只是 message store 和 ctx 是否隔离不同。

拆分后设计：

- `PORRunner`：保留 plan lifecycle、事件输出、resume。
- `PlanCheckpointRecorder`：封装 `_save_plan_checkpoint()`。
- `StepRunner`：统一执行 step ReAct loop，参数控制 shared/isolated。
- `StepContextFactory`：负责 child MessageStore、child ExecutionContext、completed summary。
- `StepResultReducer`：负责把 `StepExecutionResult` 合并回 parent ctx/messages。
- `PlanObserverCoordinator`：负责 observe、mark done/failed、replan。
- `PlanFinalizer`：负责最终摘要 LLM 调用和 fallback summary。

功能等价对比：

- plan created event：content、steps、metadata 一致。
- resume event：status、phase、current_step_id 一致。
- 单 step 无工具：step status、result、events 一致。
- 单 step 有工具：tool messages、tool result、round 增长一致。
- 并行 step：事件顺序、usage merge、parent message summary 一致。
- step timeout、LLM 异常、sub-loop round limit：错误文案一致。
- observe action：`replan`、`done`、默认 done 的 checkpoint phase 一致。
- final summary：LLM 成功/失败 fallback 内容一致。

落地步骤：

- [x] 跑通现有 POR golden/contract tests：串行、并行、失败、replan、resume、finalize。
- [x] 抽 `PlanCheckpointRecorder`。
- [x] 抽 `StepContextFactory`。
- [x] 抽统一 `StepRunner`，先复用原逻辑。
- [x] 抽 `StepResultReducer`。
- [x] 最后收敛 `_execute_step_isolated()` / `_execute_single_step()` 重复。

## P1：GraphPlugin 拆分

目标文件：[graph/plugin.py](/Users/zhaoxin/Desktop/AxcRavenSystem/AxcAgentEngine/axc_agent_engine/plugins/builtin/graph/plugin.py)

原始功能：

- `GraphPlugin` 当前约 561 行。
- `get_tools()` 长度很大，硬编码所有 graph 工具 schema。
- 类同时负责 graph CRUD、source JSONL 导入、entity/relation policy、分页、metadata filter、audit、externalize 大结果、status metadata。

拆分后设计：

- `GraphPlugin`：保留 initialize、inject_context、get_tools。
- `GraphToolFactory`：声明式生成工具 schema。
- `GraphCommandService`：负责 search/upsert/get/list/delete/export/reload 的业务调用。
- `GraphSourceLoader`：负责 `_load_sources()` 和 `_read_source()`。
- `GraphPolicy`：负责 allow_writes、allow_deletes、type allow/deny、limit。
- `GraphPresenter`：负责 `_json_output()`、metadata filter、status payload。
- `GraphAuditRecorder`：负责 audit 和 error envelope。

功能等价对比：

- 所有 graph 工具 schema 快照一致。
- `graph_search`：query 空、depth/limit clamp、结果结构一致。
- `graph_upsert_entity/relation`：写禁用、数量限制、类型拒绝、payload 一致。
- `graph_get/list/delete`：存在/不存在、分页、summary 一致。
- `graph_reload_sources`：clear_existing、load_errors、source_stats 一致。
- `graph_export`：namespace、entities、relations、exported_at 语义一致。
- large result externalize：artifact metadata、content_type、summary 一致。

落地步骤：

- [x] 跑通现有 graph golden/contract tests。
- [x] 抽 `GraphToolFactory`，保持 schema 快照。
- [x] 抽 `GraphPolicy`。
- [x] 抽 `GraphSourceLoader`。
- [x] 抽 `GraphPresenter` 和 `GraphAuditRecorder`。

## P1：流式聚合职责拆分

目标文件：

- [core/stream_aggregator.py](/Users/zhaoxin/Desktop/AxcRavenSystem/AxcAgentEngine/axc_agent_engine/core/stream_aggregator.py)
- [core/llm_caller.py](/Users/zhaoxin/Desktop/AxcRavenSystem/AxcAgentEngine/axc_agent_engine/core/llm_caller.py)

原始功能：

- `StreamAggregator.aggregate()` 聚合 `LLMStreamChunk`，同时通过 callback 触发 thinking/content/tool args delta。
- `LLMCaller._aggregate_stream()` 负责 provider stream、event queue、thinking 状态、STREAM_START/DELTA、TOOL_ARGS_PREVIEW、usage、CACHE_HIT、COST_UPDATE。

拆分后设计：

- `StreamAggregator`：只负责 chunk -> `AggregatedMessage`，保留 delta callback 兼容但内部不理解事件语义。
- `StreamEventEmitter`：负责 thinking/content/tool args delta 到 Event 的转换。
- `StreamUsageReporter`：负责 usage/cache/cost event。
- `LLMCaller`：只负责 provider 调用、retry/fallback、调用 aggregator/emitter。

功能等价对比：

- thinking delta：`THINKING_START/DELTA/END` 顺序一致。
- content delta：`STREAM_START` 只发一次，`STREAM_DELTA` 内容一致。
- tool args preview：tool_name、tool_call_id、arguments_preview、index 一致。
- 无 event_queue 时 preview_events 行为一致。
- usage/cache/cost：tokens 和 event metadata 一致。
- idle timeout、max chunks、max content length：partial 行为和异常一致。

落地步骤：

- [x] 跑通现有 stream golden/contract tests。
- [x] 抽 `StreamEventEmitter`，先保留 callback 机制。
- [x] 抽 `StreamUsageReporter`。
- [x] 保持 `LLMCaller.call()` 返回值不变。

## P1：Engine / Agent 构造职责拆分

目标文件：

- [engine.py](/Users/zhaoxin/Desktop/AxcRavenSystem/AxcAgentEngine/axc_agent_engine/engine.py)
- [agent.py](/Users/zhaoxin/Desktop/AxcRavenSystem/AxcAgentEngine/axc_agent_engine/agent.py)

原始功能：

- `Engine.load_agent()` 负责 YAML 读取、AgentConfig 校验、system_prompt_file、provider resolve、PluginContext、ToolRegistry、load_plugins、Agent 构造、dispatcher consumer。
- `Agent._create_executor()` 负责 ExecutionConfig、ExecutionServices replace、ExecutionContext、model_info、agent_info、metadata、PluginManager、LLMCaller、Executor。
- `Agent._execute_stream()` 负责 limiter、session gate、input provider、session restore/save、executor run。

拆分后设计：

- `AgentConfigLoader`：负责 YAML、schema、system prompt。
- `ProviderResolver`：负责 LLMConfig / LLMProvider / registry string。
- `PluginContextFactory`：负责 PluginContext 和 ToolRegistry。
- `AgentFactory`：负责 load_plugins + Agent。
- `ExecutionContextFactory`：负责 ExecutionConfig、ExecutionContext、metadata。
- `ExecutorFactory`：负责 PluginManager、LLMCaller、Executor。
- `RunCoordinator`：负责 limiter、input provider、session restore/save、executor run。

功能等价对比：

- YAML 不存在、YAML 解析失败、schema 失败、system_prompt_file 不存在：异常类型和文案一致。
- provider string missing：异常一致。
- plugin 初始化和 tool registry freeze 后工具 schema 一致。
- session restore/save：message store 完全一致。
- resume execution/POR：checkpoint lookup、session_id 推导、stream 默认值一致。
- metadata：model、agent、agent_name、session_id、input_artifacts、input_metadata 一致。

落地步骤：

- [x] 跑通现有 engine/agent construction golden/contract tests。
- [x] 抽 `AgentConfigLoader`。
- [x] 抽 `ProviderResolver`。
- [x] 抽 `ExecutionContextFactory` 和 `ExecutorFactory`。
- [x] 最后抽 `RunCoordinator`。

## P2：BuiltinToolsPlugin 按工具域拆分

目标文件：[builtin_tools/plugin.py](/Users/zhaoxin/Desktop/AxcRavenSystem/AxcAgentEngine/axc_agent_engine/plugins/builtin/builtin_tools/plugin.py)

原始功能：

- 文件约 817 行。
- 顶层函数注册所有内置工具：time、file、http、python、shell、pip、result。
- 同时包含 workspace path validation、SSRF check、venv、artifact storage、command artifact presenter、deferred tool activation。

拆分后设计：

- `BuiltinToolsPlugin`：只保留 load/defer、deferred activation、tool_search。
- `builtin_tools/registry.py`：`_register_tool` 和 `_ALL_TOOLS`。
- `builtin_tools/file_tools.py`：file_read/list/glob/info/write/append/edit。
- `builtin_tools/http_tools.py`：http_request、URL/SSRF validation。
- `builtin_tools/command_tools.py`：shell、python_exec、pip_install、venv、command artifacts。
- `builtin_tools/result_tools.py`：result_read/search/page。
- `builtin_tools/path_policy.py`：workspace path validation。
- `builtin_tools/presenter.py`：ToolOutput/artifact helpers。

功能等价对比：

- 所有工具 schema 快照一致。
- file tools：workspace required、path escape、large file、line window、artifact 一致。
- http_request：blocked host/IP、DNS failure、timeout/max_bytes、artifact 一致。
- shell/python/pip：workspace required、timeout、executor argv、stdout/stderr artifact 一致。
- result tools：artifact missing、pagination、search payload 一致。
- deferred tools：`tool_search` 激活、pre_llm_call 注入、post_tool_call 移除行为一致。

落地步骤：

- [x] 跑通现有 builtin tools golden/contract tests。
- [x] 先抽纯 helper：path policy、presenter。
- [x] 再按 file/http/command/result 拆模块。
- [x] 最后保留 `BuiltinToolsPlugin.get_tools()` 行为不变。

## P2：TracingPlugin 职责拆分

目标文件：[tracing/plugin.py](/Users/zhaoxin/Desktop/AxcRavenSystem/AxcAgentEngine/axc_agent_engine/plugins/builtin/tracing/plugin.py)

原始功能：

- `TracingPlugin` 当前约 485 行、26 个方法。
- 同时负责 trace/span 生命周期、采样、traceparent、参数/结果脱敏、recent span 缓存、SpanStore 保存、callback/exporter/log 输出、audit mode、后台任务队列、查询工具、metadata 同步。
- `pre_tool_call/post_tool_call/on_tool_call_failed` 依赖 `_tool_runtime_contexts` 从共享 `ExecutionContext.runtime.plugin_states` 读取当前工具上下文。

拆分后设计：

- `TracingPlugin`：保留 hook 和工具注册。
- `TraceStateManager`：负责 trace_id、traceparent、root/current span 状态。
- `SpanFactory`：负责 `_new_span()` 和基础字段填充。
- `SpanFinisher`：负责 `_finish_span()`。
- `TraceSampler`：负责 sample_rate、sample_errors、slow_span_ms。
- `RedactionService`：负责 `_redact()`、`_truncate()`。
- `SpanEmitter`：负责 recent_spans、SpanStore、exporter、callback、log。
- `TraceToolHandlers`：负责 `trace_status/get_trace/list_traces`。
- `TraceBackgroundQueue`：负责 `_schedule()`、`_flush_pending()`、queue_limit。

功能等价对比：

- execution span：trace_id、traceparent、root span、metadata 一致。
- LLM span：round、message_count、tool_schema_count、usage、duration_ms 一致。
- tool span：tool_call_id、capability、risk_level、argument_keys、result/error、artifact_count 一致。
- sampling：sample_rate、sample_errors、slow_span_ms、dropped/emitted/stored/failed 计数一致。
- redact：默认 redact keys、自定义 redact keys、max length 行为一致。
- trace tools：status/get/list 的 payload、排序、summary 一致。

落地步骤：

- [x] 跑通现有 tracing golden/contract tests。
- [x] 抽 `RedactionService` 和 `TraceSampler`。
- [x] 抽 `SpanFactory` / `SpanEmitter`。
- [x] 抽 `TraceToolHandlers`。
- [x] 保持 tracing metadata 和 span payload 快照一致。

## P2：CompressPlugin 上下文治理职责拆分

目标文件：[compress/plugin.py](/Users/zhaoxin/Desktop/AxcRavenSystem/AxcAgentEngine/axc_agent_engine/plugins/builtin/compress/plugin.py)

原始功能：

- `CompressPlugin` 当前约 222 行，但承担上下文治理门面职责。
- 负责 tool result compact/externalize、recent window、summary、file restore、tool summary、recall、compression boundary 持久化、round buffer。
- 该插件已经有多个 support 模块，问题主要是 plugin 入口仍串联太多状态。

拆分后设计：

- `CompressPlugin`：保留 hook。
- `ContextCompressionPipeline`：负责 `normalize -> compact_tool_messages -> select_recent_window -> assemble -> pack_context`。
- `CompressionBoundaryService`：负责 `_load_boundary()`、`_save_boundary()`。
- `RecallContextService`：负责 resource recall 和 fallback recall。
- `RoundBufferService`：负责 conversation buffer、round_count。
- `ToolSummaryCoordinator`：负责 pending tool observations、tool summaries。
- `ToolResultExternalizer`：负责 post_tool_call 的大结果外置。

功能等价对比：

- `transform_messages`：同样 messages/current_message 下输出 messages 完全一致。
- tool result compact/externalize：artifact threshold、content、metadata 一致。
- file cache restore：file_cache message 触发条件和内容一致。
- recall：resource recall 和 fallback recall 排序/阈值/文本一致。
- summary：summary_after、summary_keep_recent、failures/broken 状态一致。
- boundary：load/save key、round_count、buffer、file_cache、tool_summaries 一致。

落地步骤：

- [x] 跑通现有 compress golden/contract tests。
- [x] 抽 `ContextCompressionPipeline`。
- [x] 抽 `CompressionBoundaryService`。
- [x] 抽 `RecallContextService` 和 `ToolSummaryCoordinator`。

## P2：OutputFormatService 校验/修复拆分

目标文件：[output_format/support/service.py](/Users/zhaoxin/Desktop/AxcRavenSystem/AxcAgentEngine/axc_agent_engine/plugins/builtin/output_format/support/service.py)

原始功能：

- `OutputFormatService` 当前约 204 行、12 个方法。
- 同时负责格式路由、JSON/Markdown/Text 校验、jsonschema fallback、LLM repair、本地 repair、prompt 构造、结果统计。

拆分后设计：

- `OutputFormatService`：保留 public API：`validate()`、`repair()`、`validate_and_repair()`、`repair_with_result()`。
- `OutputValidatorRegistry`：按 `format_type` 分发 validator。
- `JsonSchemaValidator`：负责 JSON 抽取、jsonschema、fallback schema。
- `MarkdownValidator`：负责 template/sections/patterns。
- `TextValidator`：负责 length/lines/contains/patterns。
- `RepairPromptBuilder`：负责 repair prompt。
- `OutputRepairer`：负责 utility_llm repair、本地 repair、timeout。

功能等价对比：

- json_schema：无 JSON、JSON parse fail、schema valid/invalid、jsonschema 缺失 fallback，errors 一致。
- markdown：required_sections、section_order、required/forbidden_patterns 一致。
- text：max_length、max_lines、must_contain、must_not_contain、patterns 一致。
- repair：utility_llm 成功/失败/timeout、本地 repair、attempts、duration_ms、repaired 标记一致。
- `ValidationResult.to_dict()` 和 `RepairResult.to_dict()` 字段一致。

落地步骤：

- [x] 跑通现有 output format contract tests。
- [x] 抽 validator 类，保留错误文案。
- [x] 抽 repair prompt 和 repairer。
- [x] 保持 public dataclass 不变。

## P2：Agent 调用类插件拆分

目标文件：

- [core/dispatcher.py](/Users/zhaoxin/Desktop/AxcRavenSystem/AxcAgentEngine/axc_agent_engine/core/dispatcher.py)
- [swarm/plugin.py](/Users/zhaoxin/Desktop/AxcRavenSystem/AxcAgentEngine/axc_agent_engine/plugins/builtin/swarm/plugin.py)
- [collaboration/plugin.py](/Users/zhaoxin/Desktop/AxcRavenSystem/AxcAgentEngine/axc_agent_engine/plugins/builtin/collaboration/plugin.py)

原始功能：

- `SwarmPlugin._tool_swarm_dispatch()` 约 90 行，负责任务校验、agent allow/deny、深度控制、并发调度、dispatcher envelope、timeout、failure_policy、结果 externalize、audit、metadata。
- `CollaborationPlugin` 负责 agent_list、agent_call、orchestration task create/get/cancel，同时处理 allow/deny、depth、dispatcher、orchestration service 兼容。
- `AgentMessageDispatcher` 负责跨 Agent request/reply、consumer task 生命周期、MessageBus channel、pending future、timeout/error envelope。

拆分后设计：

- `AgentMessageDispatcher`：保留 public API，内部改由子服务协作。
- `AgentMessageRouter`：负责 channel 命名、publish、reply channel。
- `AgentConsumerManager`：负责 consumer task start/stop/stop_all。
- `AgentRequestTracker`：负责 pending future、correlation_id、listen reply、timeout cleanup。
- `AgentCallPolicy`：负责 allowed_agents、denied_agents、allow_self_call、max_depth。
- `AgentEnvelopeFactory`：负责 dispatcher envelope 和 metadata。
- `AgentCallClient`：负责 dispatcher.request、timeout、depth restore。
- `SwarmTaskNormalizer`：负责 swarm tasks 校验、排序、timeout、priority。
- `SwarmDispatcher`：负责 semaphore、best_effort/fail_fast、总 timeout。
- `SwarmResultPresenter`：负责 payload、externalize、ToolOutput。
- `OrchestrationToolHandlers`：负责 orchestration create/get/cancel。

功能等价对比：

- agent_list：过滤结果和 summary 一致。
- dispatcher request：correlation_id、reply channel、timeout error envelope 文案一致。
- dispatcher consumer：agent.chat metadata/session_id 传递、reply/error envelope 一致。
- stop_consumer/stop_all：取消行为一致。
- agent_call：空参数、not allowed、depth exceeded、no dispatcher、reply error、异常文案一致。
- swarm_dispatch：tasks 空/非法、agent 不存在/not allowed、failure_policy、best_effort/fail_fast、timeout、result ordering 一致。
- swarm artifact：kind、agent_name、task_id、swarm_id metadata 一致。
- orchestration：service missing、method missing、sync/async get/cancel、payload 一致。
- metadata/audit：`swarm` metadata 和 `swarm_dispatch_completed` audit 一致。

落地步骤：

- [x] 跑通现有 collaboration/swarm golden/contract tests。
- [x] 抽共用 `AgentCallPolicy` 和 `AgentEnvelopeFactory`。
- [x] 抽 `SwarmDispatcher`。
- [x] 抽 orchestration handlers。

## P2：MCPPlugin 动态工具发现拆分

目标文件：[mcp/plugin.py](/Users/zhaoxin/Desktop/AxcRavenSystem/AxcAgentEngine/axc_agent_engine/plugins/builtin/mcp/plugin.py)

原始功能：

- `MCPPlugin` 当前约 165 行。
- 负责 server config、连接、list_tools、tool allow/deny、duplicate 检查、ToolDefinition 构造、动态 register_late、execute closure、结果 externalize、health、close。

拆分后设计：

- `MCPPlugin`：保留 lifecycle 和 `get_tools()`。
- `MCPServerConnector`：负责连接/关闭/health。
- `MCPToolDiscovery`：负责 list_tools、duplicate 检查、allow/deny。
- `MCPToolDefinitionFactory`：负责 annotation/override 到 ToolDefinition。
- `MCPToolExecutorFactory`：负责 execute closure 和 conn.call_tool。
- `MCPResultPresenter`：负责 ToolOutput 和 externalize。

功能等价对比：

- required server 失败时仍 raise；非 required server 失败只写 health。
- duplicate tool name 行为一致。
- allowed_tools/denied_tools 的 key 匹配一致。
- override：read_only、risk_level、capability、timeout、retryable 一致。
- register_late_many 调用时机、plugin_name、reason 一致。
- health payload 一致。
- MCP 输出 string/json/externalize 后 content_type、artifact、metadata 一致。

落地步骤：

- [x] 跑通现有 MCP golden/contract tests，使用 fake MCPConnection。
- [x] 抽 discovery/factory，不改 tool name。
- [x] 抽 executor/presenter，不改 ToolOutput。

## P2：工具执行/参数校验/风险分类拆分

目标文件：

- [tools/executor.py](/Users/zhaoxin/Desktop/AxcRavenSystem/AxcAgentEngine/axc_agent_engine/tools/executor.py)
- [tools/orchestrator.py](/Users/zhaoxin/Desktop/AxcRavenSystem/AxcAgentEngine/axc_agent_engine/tools/orchestrator.py)
- [runtime/risk.py](/Users/zhaoxin/Desktop/AxcRavenSystem/AxcAgentEngine/axc_agent_engine/runtime/risk.py)

原始功能：

- 旧 `validate_arguments()` 入口已删除；参数校验由 `ToolArgumentValidator` 直接承担。
- `execute_tool()` 同时负责参数校验、read-only retry、timeout、ToolOutput contract。
- `tools/orchestrator.py` 负责批次划分、并发/串行、plugin hooks、policy、runtime context、audit、失败 envelope。
- `classify_tool_risk()` 同时处理 shell 风险、默认规则、自定义规则、风险升级。

拆分后设计：

- `ToolArgumentValidator`：封装现有 schema 子集，后续可替换 jsonschema。
- `ToolRetryPolicy`：负责 retryable 判断和 read-only 重试。
- `SingleToolExecutor`：负责单次 execute、timeout、ToolOutput contract。
- `ToolBatchPlanner`：负责 read-only 并发批次。
- `ToolExecutionPipeline`：负责 pre hook、policy、execute、post hook、failure hook。
- `ToolAuditRecorder`：负责 audit event。
- `ToolRuntimeContextManager`：负责 per-task runtime push/pop。
- `RiskRuleEngine`：负责 shell/default/custom rules。

功能等价对比：

- 参数校验错误文案完全一致。
- read-only retry 次数、delay、retryable/non-retryable 关键词一致。
- timeout 错误文案、duration_ms 一致。
- ToolOutput contract TypeError 行为一致。
- tool batches：只读连续并发、写工具串行规则一致。
- policy reject、plugin reject、unknown tool、tool failure 的 ErrorEnvelope code/message/category 一致。
- audit event type、allowed、metadata、error payload 一致。
- risk classify：blocked/dangerous/safe/moderate、reason、matched_rule 一致。

落地步骤：

- [x] 跑通现有 tools executor/orchestrator/risk golden/contract tests。
- [x] 抽 `ToolArgumentValidator`。
- [x] 抽 `ToolRetryPolicy` 和 `SingleToolExecutor`。
- [x] 抽 `ToolBatchPlanner` 和 `ToolExecutionPipeline`。
- [x] 抽 `RiskRuleEngine`。

## P3：API Chat 路由拆分

目标文件：[api/routes/chat.py](/Users/zhaoxin/Desktop/AxcRavenSystem/AxcAgentEngine/axc_agent_engine/api/routes/chat.py)

原始功能：

- chat route 文件约 326 行。
- 同时负责 OpenAI-compatible request model、参数子集校验、capabilities、agent lookup、sync response、SSE stream response、usage、chunk/tool_call_delta/error response。

拆分后设计：

- `ChatRequestValidator`：负责 unsupported parameter 和 subset validate。
- `ChatAgentResolver`：负责 agent lookup/default agent。
- `ChatCompletionPresenter`：负责 sync response、usage、chunk。
- `ChatSSEPresenter`：负责 SSE stream、tool call delta、done。
- `ChatErrorPresenter`：负责 OpenAI-compatible error response。

功能等价对比：

- unsupported 参数 HTTP status、error body 一致。
- sync response：id/object/model/choices/usage 字段一致。
- stream response：SSE chunk 顺序、tool_call_delta、finish_reason、usage、`[DONE]` 一致。
- agent not found、engine state missing、internal error body 一致。

落地步骤：

- [x] 补 chat route golden tests。
- [x] 抽 presenter，不改 JSON 字段。
- [x] 抽 validator/resolver。

## P3：Sandbox 执行器拆分

目标文件：

- [runtime/sandbox_models.py](/Users/zhaoxin/Desktop/AxcRavenSystem/AxcAgentEngine/axc_agent_engine/runtime/sandbox_models.py)
- [runtime/sandbox_utils.py](/Users/zhaoxin/Desktop/AxcRavenSystem/AxcAgentEngine/axc_agent_engine/runtime/sandbox_utils.py)
- [runtime/sandbox_local.py](/Users/zhaoxin/Desktop/AxcRavenSystem/AxcAgentEngine/axc_agent_engine/runtime/sandbox_local.py)
- [runtime/sandbox_policy.py](/Users/zhaoxin/Desktop/AxcRavenSystem/AxcAgentEngine/axc_agent_engine/runtime/sandbox_policy.py)
- [runtime/sandbox_workspace.py](/Users/zhaoxin/Desktop/AxcRavenSystem/AxcAgentEngine/axc_agent_engine/runtime/sandbox_workspace.py)
- [runtime/sandbox_code.py](/Users/zhaoxin/Desktop/AxcRavenSystem/AxcAgentEngine/axc_agent_engine/runtime/sandbox_code.py)
- [runtime/sandbox_provider.py](/Users/zhaoxin/Desktop/AxcRavenSystem/AxcAgentEngine/axc_agent_engine/runtime/sandbox_provider.py)

原始功能：

- sandbox 文件约 358 行。
- 包含 command spec/result、policy、local subprocess、workspace executor、python sandbox、powershell sandbox、docker placeholder、provider、env、decode、write helper。

拆分后设计：

- `sandbox_models.py`：CommandSpec、CommandResult、CommandExecutor、CommandPolicy、SandboxProvider。
- `sandbox_policy.py`：DefaultCommandPolicy、PolicyCommandExecutor。
- `sandbox_local.py`：LocalSubprocessExecutor、DockerSandboxExecutor。
- `sandbox_workspace.py`：WorkspaceCommandExecutor。
- `sandbox_code.py`：PythonSandboxExecutor、PowerShellSandboxExecutor。
- `sandbox_provider.py`：LocalSandboxProvider。
- `sandbox_utils.py`：env/decode/write/preexec helpers。
- 已强制删除旧 `runtime/sandbox.py` 兼容门面。

功能等价对比：

- LocalSubprocessExecutor：stdout/stderr limit、truncated、timeout、duration_ms、env 一致。
- WorkspaceCommandExecutor：cwd boundary、relative path、error 文案一致。
- PythonSandboxExecutor：script path、python argv、cleanup 行为一致。
- PowerShellSandboxExecutor：argv 和 timeout 一致。
- CommandPolicy reject 行为一致。

落地步骤：

- [x] 补 sandbox golden tests。
- [x] 先抽 models/utils。
- [x] 再按 executor 类型拆文件。

## P3：PluginManager / ToolRegistry / LLM Client 基础设施拆分

目标文件：

- [core/plugin_manager.py](/Users/zhaoxin/Desktop/AxcRavenSystem/AxcAgentEngine/axc_agent_engine/core/plugin_manager.py)
- [tools/registry.py](/Users/zhaoxin/Desktop/AxcRavenSystem/AxcAgentEngine/axc_agent_engine/tools/registry.py)
- [llm/client.py](/Users/zhaoxin/Desktop/AxcRavenSystem/AxcAgentEngine/axc_agent_engine/llm/client.py)

原始功能：

- `PluginManager` 约 174 行、19 个方法，集中负责所有 hook 调用、错误吞吐、同步/异步 hook 编排、消息变换、工具 hook、plan/step hook。
- `ToolRegistry` 约 122 行、17 个方法，负责工具注册、late register、freeze、schema、name mapping、resolve。
- `OpenAIClient` 约 157 行，负责 OpenAI-compatible chat/stream、HTTP 错误转换、usage/message/chunk 解析。

拆分后设计：

- `PluginHookRunner`：负责单类 hook 的执行、异常策略、耗时记录。
- `PluginHookContracts`：负责 tuple 返回值兼容和强类型转换。
- `ToolSchemaRegistry`：负责 register/freeze/schema version。
- `ToolNameResolver`：负责 name mapping、sanitize、resolve。
- `OpenAIChatAdapter`：负责 chat response 解析。
- `OpenAIStreamAdapter`：负责 stream chunk 解析。
- `ProviderErrorMapper`：负责 HTTP status 到 ProviderError。

功能等价对比：

- 所有 plugin hook 调用顺序、异常吞吐、返回值默认值一致。
- `get_openai_schemas()` 输出 schema 快照一致。
- duplicate tool、freeze 后注册、late register 行为一致。
- provider chat：request body、headers、timeout、message/tool_calls/usage 解析一致。
- provider stream：chunk delta、tool_call_delta、usage、错误转换一致。
- HTTP error：status code 对应错误类型和文案一致。

落地步骤：

- [x] 补 PluginManager hook golden tests。
- [x] 补 ToolRegistry schema/name mapping golden tests。
- [x] 补 OpenAIClient fake httpx golden tests。
- [x] 先抽错误映射和 stream/chat parser。
- [x] 再抽 hook runner 和 registry resolver。

## P3：Sidecar / Eval / Simulation 旁路模块拆分

目标文件：

- [sidecar/multi_agent/session.py](/Users/zhaoxin/Desktop/AxcRavenSystem/AxcAgentEngine/axc_agent_engine/sidecar/multi_agent/session.py)
- [sidecar/simulation/runner.py](/Users/zhaoxin/Desktop/AxcRavenSystem/AxcAgentEngine/axc_agent_engine/sidecar/simulation/runner.py)
- [sidecar/eval/runner.py](/Users/zhaoxin/Desktop/AxcRavenSystem/AxcAgentEngine/axc_agent_engine/sidecar/eval/runner.py)
- [sidecar/orchestration.py](/Users/zhaoxin/Desktop/AxcRavenSystem/AxcAgentEngine/axc_agent_engine/sidecar/orchestration.py)
- [sidecar/cost_optimizer.py](/Users/zhaoxin/Desktop/AxcRavenSystem/AxcAgentEngine/axc_agent_engine/sidecar/cost_optimizer.py)
- [sidecar/distiller.py](/Users/zhaoxin/Desktop/AxcRavenSystem/AxcAgentEngine/axc_agent_engine/sidecar/distiller.py)
- [sidecar/failure_miner.py](/Users/zhaoxin/Desktop/AxcRavenSystem/AxcAgentEngine/axc_agent_engine/sidecar/failure_miner.py)

原始功能：

- 这些模块不是主 ReAct/POR 执行链路，但仍属于包内能力面。
- `MultiAgentSession` 负责多 agent 会话状态、调度、事件、停止条件。
- `SimulationRunner.stream()` 约 82 行，负责模拟循环、agent action、world adapter、score/report、事件流。
- `EvalRunner` 负责 eval case 执行、judge、matcher、store。
- `OrchestrationTaskService` 负责旁路任务生命周期、后台任务、事件收集。
- 旧 `CostOptimizer`、`AgentDistiller`、`FailureMiner` 外观类已删除；报告生成由 `CostReportBuilder`、`DistillationReportBuilder`、`FailureReportBuilder` 直接承担。

拆分后设计：

- `MultiAgentSessionCoordinator`：负责 session loop。
- `MultiAgentEventSink`：负责事件记录和输出。
- `SimulationLoopRunner`：负责 step loop。
- `SimulationWorldCoordinator`：负责 world adapter、observation/action/delta。
- `EvalCaseRunner`：负责单 case 执行。
- `EvalReportBuilder`：负责结果聚合。
- `OrchestrationTaskRepository`：负责 task 存取。
- `OrchestrationWorker`：负责后台运行和取消。
- `AnalysisReportBuilder`：统一 cost/distill/failure 报告构造。

功能等价对比：

- multi-agent：scheduler 选择、stop condition、event 顺序、shared context 一致。
- simulation：每步 observation/action/delta/score/event 顺序一致。
- eval：case 输入、agent 输出、judge/matcher 分数、store 写入一致。
- orchestration：task_id、status 迁移、cancel、events、error 文案一致。
- cost/distill/failure：finding/rule/category/action 排序和字段一致。

落地步骤：

- [x] 补 sidecar golden tests，优先覆盖 multi_agent/session、simulation/runner、eval/runner。
- [x] 抽 event sink 和 report builder。
- [x] 抽 orchestration repository/worker。
- [x] 拆分析类报告构造逻辑。

## P3：轻量模块巡检，默认不拆

目标范围：

- `core/message_store.py`
- `core/session.py`
- `core/session_manager.py`
- `core/context.py`
- `core/events.py`
- `core/schema.py`
- `runtime/checkpoint.py`
- `runtime/concurrency.py`
- `runtime/policy.py`
- `runtime/resources.py`
- `runtime/recovery.py`
- `storage/*.py`
- `observability/*.py`
- `planning/planner.py`
- `planning/router.py`
- `planning/scheduler.py`
- `planning/checkpointing.py`
- `plugins/base.py`
- `plugins/loader.py`
- `llm/config.py`
- `llm/registry.py`
- `llm/rate_limited.py`
- `utils/*.py`

原则：

- 这些模块目前不是主要职责过重点，默认不拆。
- 只做巡检：死代码、重复 helper、裸 dict 协议、异常吞吐、命名不清、测试缺口。
- 能删就删，能补类型就补类型，能补测试就补测试；不要为了统一风格增加新层。

功能等价对比：

- MessageStore：system prompt、plugin context、append tool results、set/extend 行为一致。
- SessionManager：restore/save/remove/clear 持久化行为一致。
- ExecutionContext：cancel、usage、image token、fork_for_child 行为一致。
- Runtime/concurrency/recovery/resources：异常类型、队列 timeout、resource lookup 行为一致。
- Planning 小模块：Plan/Route/Scheduler/checkpoint state 序列化一致。

落地步骤：

- [x] 做一次 `rg` 死代码巡检，确认没有无调用函数。
- [x] 对裸 dict 协议增加 TypedDict/dataclass 只读边界，不改变 runtime payload。
- [x] 对已有轻量模块补最小 golden/unit tests。
- [x] 没有明确收益的拆分类提案直接拒绝。

## 代码减负验收

每个拆分项除了功能等价，还必须做代码减负验收：

- [x] 原核心类/函数行数下降，职责数量下降。
- [x] 新增类不是空壳；每个类有明确状态或稳定协议。
- [x] 删除旧 helper、旧分支、旧 fallback，不保留重复实现。
- [x] 没有新增循环依赖。
- [x] 没有把可读顺序变成跨 5 个文件跳转才能理解。
- [x] public API 不变，internal API 更窄。
- [x] 单元测试能直接测新 service/repository/presenter，不必完整启动 Agent。
- [x] 复杂度从“大函数分支”转为“清晰流程 + 小函数”，而不是转为“过度抽象”。
- [x] 代码量如果增加超过 10%，必须说明增加来自测试、类型边界或删除重复后的净收益；否则需要重新设计。
- [x] 任何新增扩展点必须有当前真实使用场景；没有当前使用场景的扩展点视为负债。

## 统一测试矩阵

### ReAct / Executor

- [x] 非流式无工具最终回答。
- [x] 流式无工具 delta。
- [x] 单工具调用成功。
- [x] 单工具调用失败。
- [x] 多工具调用并行。
- [x] fallback retry 成功。
- [x] fallback 切换成功。
- [x] stop hook 提前停止。
- [x] total timeout。
- [x] max_rounds exceeded。

### POR

- [x] por_first 生成计划。
- [x] route 到 por_plan。
- [x] 串行 step 成功。
- [x] 并行 step 成功。
- [x] step 工具调用。
- [x] step timeout。
- [x] observe replan。
- [x] resume POR。
- [x] finalize LLM 失败 fallback summary。

### Plugins

- [x] Memory add/search/list/delete/export。
- [x] Memory auto extract。
- [x] Memory privacy reject/redact。
- [x] Memory vector store fallback。
- [x] Knowledge BM25/vector/filter/trace。
- [x] Skill load/list/status/reload/run_script。
- [x] Graph search/upsert/delete/reload/export。
- [x] Builtin file/http/command/result/deferred。

## 推荐执行顺序

1. 先做 `TODO-0.1` 和 `TODO-0.2`，建立快照测试和对外契约快照。
2. 先拆 `SkillPlugin._tool_run_script()`，范围最清楚，验证拆分方法。
3. 再拆 `MemoryPlugin`，解决最大上帝类。
4. 再拆 `KnowledgePlugin`，顺手清理旧 ingestion 代码。
5. 再拆 `Executor` 和 `PORRunner`，这两个必须在测试最充分后动。
6. 最后拆 `GraphPlugin`、`BuiltinToolsPlugin`、`Engine/Agent`。

## 代码负债二期重构计划（2026-05-20）

背景：第一轮审查已经完成确定无用代码清理，但剩余债务不是“插件是否预留”的问题，而是核心流程和插件实现边界仍然偏厚。下面四项按收益和风险排序执行；每项都必须保持工具 schema、事件序列、ToolOutput、audit、checkpoint、metadata 行为等价。

### TODO-2.1：优先重构 BuiltinToolsPlugin

目标：把 `axc_agent_engine/plugins/builtin/builtin_tools/plugin.py` 从“单文件工具集合”拆成清晰模块，降低文件级职责密度，同时保留现有所有内置工具和 deferred tool 语义。

当前问题：

- `plugin.py` 同时包含工具注册表、路径策略、HTTP 安全校验、文件 IO、shell/python/pip 执行、artifact presenter、ResultStore 读取和 deferred 工具激活逻辑。
- 文件工具部分同时存在类方法实现和模块级 `_register_tool` 执行入口，结构不一致。
- SSRF 校验、workspace 边界、命令执行、artifact externalization 都属于不同安全域，放在同一文件里会放大修改风险。

建议拆分：

- `builtin_tools/registry.py`：保留 `_ALL_TOOLS`、`_register_tool`、默认加载列表和 tool schema 构造。
- `builtin_tools/path_policy.py`：只处理 workspace 必填、路径归一化、越界拒绝、file entry 展示路径。
- `builtin_tools/file_tools.py`：承载 `file_read/file_list/file_glob/file_info/file_write/file_append/file_edit`。
- `builtin_tools/http_tools.py`：承载 `http_request` 和 URL/host/IP 安全校验。
- `builtin_tools/command_tools.py`：承载 `python_exec/shell/pip_install`，只依赖 sandbox executor 和 command policy。
- `builtin_tools/result_tools.py`：承载 `result_read/result_search/result_page`。
- `builtin_tools/plugin.py`：只保留 `BuiltinToolsPlugin` 的 initialize、get_tools、deferred 激活、pre/post hook。

实施步骤：

1. 先补/确认工具 schema 快照，覆盖每个内置工具的 `name/parameters/is_read_only/capability/risk_level/deferred`。
2. 先移动无状态 helper：`_bounded_int`、`_truncate_by_bytes`、路径展示、result_store 读取。
3. 再移动每组工具实现，保持原模块级注册入口仍从新模块导入，避免一次改动过大。
4. 最后让 `plugin.py` 只引用 registry 中的 tool definitions，删除重复转发和旧 helper。

验收标准：

- `tests/test_tools.py`、`tests/test_sandbox.py`、`tests/test_plugin_isolation.py`、`tests/test_execution_services.py` 通过。
- `builtin_tools/plugin.py` 显著变薄，只保留插件生命周期和 deferred tool 逻辑。
- 所有工具名、参数 schema、返回字段、错误文案保持不变。
- workspace 越界、HTTP 私网地址拦截、shell 危险命令拦截测试必须继续覆盖。

### TODO-2.2：抽出统一 ReActLoop / TurnRunner

目标：把普通 Executor 和 POR StepRunner 中重复的 ReAct 子循环统一成一个可复用运行单元，让 `Executor` 只负责任务入口、生命周期、checkpoint 和 POR 切换。

当前问题：

- `Executor._run_react_loop()` 同时处理 `por_first`、round timeout、checkpoint、LLM 调用、fallback 事件、路由、工具执行、round hook 和终止条件。
- `planning/por_runner.py` 的 `StepRunner.run_shared()` / `run_isolated()` 又实现了一套类似的 LLM -> tool_calls -> tool_results 子循环。
- 普通 ReAct 和 POR step 对工具名解析、工具执行、事件过滤、round 限制、错误处理的语义容易漂移。

建议设计：

- `ReActTurnInput`：message_store、exec_ctx、plugin_manager、tool_registry、llm_caller、user_message、round_budget、timeout、tools_schema 策略。
- `ReActTurnResult`：assistant_message、content、tool_calls、events、status、error、usage_delta、stop_reason。
- `ReActTurnRunner`：只跑“一轮 LLM 调用 + 可选工具调用 + 事件收集”。
- `ReActLoop`：负责多轮循环、max_rounds、stop hook、total_timeout。
- `POR StepRunner`：复用 `ReActTurnRunner`，只负责构造 step prompt、共享/隔离 message store、合并 step result。

实施步骤：

1. 先增加针对普通 ReAct 和 POR step 的事件序列测试，锁定 `STREAM_END/DONE/TOOL_CALL/TOOL_RESULT/COST_UPDATE` 顺序。
2. 抽出只读的 `RoundBudget` / `TurnResult` dataclass，不改变现有执行路径。
3. 把 `Executor._run_react_loop()` 内的“单轮 LLM + 工具执行”移动到 `ReActTurnRunner`。
4. 让 POR `StepRunner` 调用同一个 runner，保留 step 自己的状态标记和 observation/replan 逻辑。
5. 删除重复的 `_resolve_tool_call_names`、重复工具执行片段和重复 LLM 错误分支。

验收标准：

- `tests/test_core.py`、`tests/test_executor_checkpoint.py`、`tests/test_planning.py`、`tests/test_por_checkpointing.py`、`tests/test_realtime_stream.py` 通过。
- 普通 ReAct 和 POR step 使用同一工具执行路径。
- `Executor` 不再直接承担工具调用细节；`PORRunner` 不再复制 ReAct 子循环。
- checkpoint state、resume 行为、fallback state_change 行为保持不变。

### TODO-2.3：统一插件结构模式，先套 GraphPlugin，再回头处理 Memory/Knowledge

目标：建立统一插件实现形态：`Config dataclass + Service + Repository/Store + ToolHandlers + Plugin shell`。先在 `GraphPlugin` 落地，因为它边界比 Memory/Knowledge 小，适合作为模板。

当前问题：

- `GraphSourceLoader`、`GraphPresenter`、`GraphAuditRecorder`、`GraphToolFactory` 都持有 `plugin` 并读取大量 `_private` 字段。
- `GraphPlugin.initialize()` 将配置展开成几十个私有字段，后续 helper 都依赖这些散落字段。
- Memory/Knowledge 已有类似问题：拆出了类，但类之间仍通过插件私有状态耦合，不是真正的服务边界。

GraphPlugin 目标结构：

- `graph/config.py`：`GraphConfig.from_dict(config)`，集中默认值、bounded_int、开关和限制。
- `graph/source_loader.py`：输入 `GraphConfig + GraphStore + GraphPolicy`，输出 `GraphSourceStats + load_errors`。
- `graph/service.py`：封装 search/upsert/delete/list/export/status，不依赖 plugin。
- `graph/tool_handlers.py`：只做 args -> service call -> ToolOutput。
- `graph/audit.py`：只处理 audit event 映射。
- `graph/plugin.py`：只负责 initialize、inject_context、get_tools，把调用委托给 service/handlers。

实施步骤：

1. 为 `GraphConfig` 写独立单测，覆盖默认值、上限裁剪、allow_writes/allow_deletes、metadata/include_metadata。
2. 把 `_status_payload/_limit/_page/_metadata/_clean_text` 等纯逻辑先移动到 config/service 层。
3. 把 `GraphSourceLoader` 改为显式依赖，不再保存 plugin。
4. 把所有 `_tool_*` 方法移动到 `GraphToolHandlers`，`GraphPlugin` 只保留薄转发或直接注册 handler 方法。
5. Graph 模板稳定后，再按同样模式改 Memory/Knowledge：禁止新增 helper 继续读取 plugin 私有字段。

验收标准：

- `tests/test_graph_plugin.py` 通过，工具返回 JSON 字段不变。
- `GraphPlugin` 私有字段数量大幅减少，核心状态收敛到 `GraphConfig` 和 `GraphService`。
- 新增类可以独立测试，不需要启动 Agent。
- 不删除 Graph 预留能力；只是把预留能力放到明确边界里。

### TODO-2.4：最后处理 MCP client 和 StreamAggregator

目标：处理两个相对独立但技术细节多的模块，降低长期维护成本；放到最后做，避免和核心执行/插件重构互相干扰。

MCP client 当前问题：

- `mcp/support/client.py` 同时包含连接门面、stdio JSON-RPC、HTTP JSON-RPC、官方 SDK adapter、SDK payload 转换、transport factory。
- SDK fallback 是需要保留的预留能力，但 transport 实现和连接重试逻辑应该独立。

MCP 建议拆分：

- `mcp/support/connection.py`：`MCPConnection`、重连、timeout、list_tools/call_tool。
- `mcp/support/transports/base.py`：`MCPTransport` protocol 和错误类型。
- `mcp/support/transports/stdio.py`：`JsonRpcStdioTransport`。
- `mcp/support/transports/http.py`：`JsonRpcHttpTransport`。
- `mcp/support/transports/sdk.py`：`OfficialSDKTransport`。
- `mcp/support/normalization.py`：`MCPTool`、`normalize_call_result`、SDK/tool payload 转换。

StreamAggregator 当前问题：

- `aggregate()` 同时承担 chunk 限制、idle timeout、thinking/content/tool_call delta、usage 聚合、callback 事件。
- 工具调用 delta 合并和最终 message 构造是纯状态逻辑，可以独立测试。

StreamAggregator 建议拆分：

- `StreamAggregateState`：保存 content_parts、thinking_parts、tool_calls_map、usage、partial。
- `merge_chunk(chunk)`：只更新状态，返回需要发送给 callback 的 delta events。
- `build_message()`：只输出 assistant message。
- `StreamAggregator.aggregate()`：只负责 async iterator、timeout、最大 chunk/长度限制。

实施步骤：

1. 先为 MCP transport factory 和 normalize_call_result 补独立测试，覆盖 SDK 不可用、stdio/http 选择、tool result text/list/raw 三种返回。
2. 再按文件拆 MCP，保持 `from axc_agent_engine.plugins.builtin.mcp.support import MCPConnection` 等现有导入可用。
3. 为 StreamAggregator 补 chunk 序列测试：content-only、thinking-only、tool_call_delta 多 index、usage、idle timeout partial。
4. 抽出 `StreamAggregateState`，保证 `aggregate()` 输出完全一致。

验收标准：

- `tests/test_mcp_plugin.py`、`tests/test_stream_aggregator.py`、`tests/test_realtime_stream.py` 通过。
- MCP SDK fallback 能力保留，但每种 transport 文件职责单一。
- StreamAggregator 的状态合并逻辑可不依赖 async iterator 独立测试。
- 不改变实时 streaming 事件和 OpenAI-compatible SSE 输出。

## 完成定义

- 每个拆分项完成后，原插件/类对外入口仍存在。
- `pytest` 全量通过。
- golden master 对比通过。
- 工具 schema 快照无差异。
- 事件序列快照无差异。
- checkpoint/audit/metadata 快照无差异。
- 单个核心类不再同时承担 4 类以上职责。
- `Plugin` 层变成薄门面，业务逻辑进入 service/repository/presenter。
