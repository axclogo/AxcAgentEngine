<h1 align="center">AxcAgentEngine</h1>

<p align="center">
  <b>无 UI 的 Agent 执行引擎 · 可插拔 ReAct 与 POR 循环</b>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.11%2B-blue" alt="python"/>
  <img src="https://img.shields.io/pypi/v/axc-agent-engine" alt="pypi"/>
  <img src="https://img.shields.io/badge/license-Apache--2.0-green" alt="license"/>
  <img src="https://img.shields.io/badge/API-OpenAI%20compatible-orange" alt="api"/>
</p>

<p align="center">
  <a href="#-快速开始">快速开始</a> ·
  <a href="#-核心能力">核心能力</a> ·
  <a href="docs/ARCHITECTURE.md">架构</a> ·
  <a href="docs/PLUGIN_DEVELOPMENT.md">插件开发</a> ·
  <a href="examples/README.md">示例</a>
</p>

<p align="center">
  <a href="README.md">🇬🇧 English</a>
</p>

---

AxcAgentEngine 是无 UI 的 Agent 执行引擎。执行循环由策略插件提供。官方随包提供 **ReAct**（逐轮调用模型与工具）和 **POR**（制定计划、按依赖执行、观察结果并重规划）。宿主也可以注册自己的循环。引擎统一处理插话、取消和检查点。

## 🚀 快速开始

```bash
# 在本仓库根目录安装当前版本并运行下方示例
pip install .

# 按需安装 HTTP API、MCP 或 Burr 工作流依赖
pip install ".[api,mcp,workflow]"
```

需要 Python 3.11 或更高版本。

```python
from axc_agent_engine import AgentModels, Engine, LLMConfig, PluginRegistry
from axc_agent_engine.llm.client import OpenAIClient
from axc_agent_engine.plugins.builtin import BuiltinToolsPlugin, PORPlugin

registry = PluginRegistry()
registry.register_many([PORPlugin, BuiltinToolsPlugin])

engine = Engine(plugin_registry=registry)
models = AgentModels(default=OpenAIClient(LLMConfig(
    base_url="https://api.openai.com/v1",
    api_key="sk-xxx",
    model="gpt-4o",
)))
agent = engine.load_agent_template("./examples/01_basic_chat/agent.yaml").instantiate(models=models)

# 非流式
result = await agent.chat("Analyze last month's sales data")

# 流式
async for event in agent.stream("Build a REST API for user management"):
    if event.type == "stream_delta":
        print(event.content, end="")
    elif event.type == "tool_call":
        print(f"\n[Tool: {event.tool_name}]")
    elif event.type == "plan_created":
        print(f"\n[Plan: {event.content}, {len(event.steps)} steps]")

await engine.close()
```


示例 01–07 的宿主注册 `PORPlugin`，模板启用 `plugins.por`，显式设置 `runtime.strategy: por` 和 `plugins.por.mode: auto`。纯 ReAct 的对应组合是 `ReActPlugin`、`plugins.react.enabled: true` 和 `runtime.strategy: react`。只启用一个策略时可省略 `runtime.strategy`，多个策略同时启用时必须点名。自定义循环遵循同一契约。 [自定义循环示例](examples/08_custom_loop) · [策略契约](docs/PLUGIN_DEVELOPMENT.md#execution-strategies)

## ✨ 核心能力

- **POR 策略插件** —— `por_first`（插件默认值）先规划，`auto` 从 ReAct 开始，识别到结构化计划后进入 POR
- **ReAct 策略插件** —— 思考、工具调用、观察的标准循环
- **插件体系** —— 宿主显式注册插件类，YAML 按需启用
- **工具协议** —— 所有工具返回 `ToolOutput`；只读并发，写串行；模型函数名安全映射
- **中断恢复** —— `WorkflowRuntime` + `CheckpointStore` + Agent resume API；Burr 通过 `axc-agent-engine[workflow]` 可选启用
- **OpenAI API 子集** —— Provider 协议 + OpenAI-compatible HTTP 客户端 / API 子集
- **记忆与知识** —— 四层记忆（KV、去重、衰减、图 hook）+ 插件自管混合检索
- **运行时绑定** —— 模型、挂载资源、请求 metadata、YAML overrides 分通道传入
- **MCP** —— stdio、JSON-RPC HTTP、官方 SDK transport
- **人工介入** —— 审批队列、`ask_human` 工具
- **会话操作** —— 通过 `plugins.sessions` 读取、发消息、停止和新建宿主会话
- **Sidecar 套件** —— 多 Agent、仿真、评测、成本统计、失败挖掘、轨迹蒸馏

<details>
<summary>完整能力矩阵</summary>

| 能力 | 实现 |
| --- | --- |
| ReAct 循环 | `ReActPlugin` |
| POR 规划 | `PORPlugin` 支持 `auto` / `por_first` |
| 中断恢复 | 默认 `MemoryWorkflowRuntime`；安装 `axc-agent-engine[workflow]` 后可使用 `BurrWorkflowRuntime` |
| 插件系统 | 宿主注册类，YAML 按需启用 |
| LLM Provider | Provider 协议 + OpenAI-compatible HTTP |
| 并行工具 | 只读并发，写串行 |
| 工具输出 | 强制 `ToolOutput` |
| 工具名映射 | Provider 负责模型安全映射 |
| 上下文压缩 | 内置 `compress` 插件 |
| 记忆 | 四层 + KV 持久化 + 去重 + 衰减 |
| 知识库 | 插件自管检索，支持本地 sources 或挂载索引/资源 |
| MCP | stdio / JSON-RPC HTTP / 官方 SDK |
| 人工审批 | 审批队列 + `ask_human` |
| Sidecar | 多 Agent / 仿真 / 评测 / 成本 / 失败挖掘 / 蒸馏 |
| API 服务 | OpenAI Chat Completions 明确子集 |

</details>

## 📦 文档

| | |
| --- | --- |
| [架构](docs/ARCHITECTURE.md) | 引擎与插件边界 |
| [API](docs/API.md) | HTTP API 子集说明 |
| [插件开发](docs/PLUGIN_DEVELOPMENT.md) | 写自己的插件 |
| [会话操作](docs/SESSIONS.md) | 宿主会话工具 |
| [安全模型](docs/SECURITY_MODEL.md) | 能力、风险、workspace |
| [示例](examples/README.md) | 7 个端到端 demo |
| [贡献](CONTRIBUTING.md) · [安全](SECURITY.md) · [LICENSE](LICENSE) | Apache-2.0 |

## Agent YAML

```yaml
name: "data-analyst"
description: "Data analysis assistant"

runtime:
  strategy: por
  max_rounds: 500
  thinking: "auto"
  workspace: "/tmp/agent-workspace"
  allowed_capabilities:
    - "file_read"
    - "file_write"
    - "http_request"

system_prompt: |
  You are a data analysis assistant...

plugins:
  por: {enabled: true, mode: auto}
  builtin_tools:
    enabled: true
    load: ["get_time", "file_read", "file_write", "http_request", "artifact_read"]
    defer: ["file_write", "http_request"]

  knowledge:
    enabled: true
    sources: ["./docs"]
    namespace: "default"

  memory:
    enabled: true
    namespace: "default"
    scope_keys: ["tenant_id", "user_id", "agent_name"]
    sensitive_policy: "redact"

  compress:
    enabled: true
    summary:
      after_rounds: 8
    durable_tools:
      names: ["agent_call", "knowledge_search"]
      keep: 12

  risk_guard:
    enabled: true
```

要点：

- 插件注册由宿主代码通过 `PluginRegistry` 显式完成；Agent YAML 只启用和配置已注册插件。
- `builtin_tools` 未配置 `load` 时只加载 `get_time`，其他内置工具必须显式启用。
- 带非空 capability 的工具默认拒绝，必须写入 `runtime.allowed_capabilities`。
- 文件和命令类工具默认要求配置 `runtime.workspace`。
- LLM 配置由代码提供，不写在 Agent YAML。
- 官方内置插件的 YAML 只配置行为参数。外部 endpoint、API key、client 对象、索引、store、catalog 都必须在代码里通过 `mounts` 注入。

## Provider 配置

`AgentModels` 接收模型 provider 对象。`OpenAIClient(LLMConfig(...))` 是内置的 OpenAI 兼容 provider；自定义 provider 需要实现 `LLMProvider` 协议（`model`、`tool_name_mapping`、`chat`、`stream`、`ask`、`close`）。

```python
from axc_agent_engine import AgentModels, ConcurrencyConfig, Engine, LLMConfig, PluginRegistry
from axc_agent_engine.llm.client import OpenAIClient
from axc_agent_engine.plugins.builtin import BuiltinToolsPlugin, PORPlugin
from axc_agent_engine.tools.name_mapping import ToolNameMappingConfig

main_model_config = LLMConfig(
    base_url="https://api.openai.com/v1",
    api_key="sk-xxx",
    model="gpt-4o",
    timeout=120,
    max_concurrent_requests=32,
    requests_per_minute=0,
    rate_limit_queue_timeout=10,
    tool_name_mapping=ToolNameMappingConfig(),
)

registry = PluginRegistry()
registry.register_many([PORPlugin, BuiltinToolsPlugin])
engine = Engine(
    plugin_registry=registry,
    concurrency=ConcurrencyConfig(
        max_engine_concurrent_runs=128,
        queue_timeout=30,
    ),
)
agent = engine.load_agent_template("./examples/01_basic_chat/agent.yaml").instantiate(
    models=AgentModels(default=OpenAIClient(main_model_config)),
)
```

工具名映射属于 provider 职责。内部工具名在 LLM 调用前编码为模型安全 function name，在 hooks/工具执行前解码回来。

## 运行时绑定

Agent YAML 描述稳定行为。运行时对象在模板实例化时由代码传入：

```python
agent = template.instantiate(
    models=AgentModels(
        default=gpt5_provider,
        utility=fast_provider,
        fallback=backup_provider,
    ),
    mounts={
        "knowledge.index": tenant_knowledge_index,
        "graph.store": tenant_graph_store,
        "skill.catalog": tenant_skill_catalog,
    },
    metadata={
        "tenant_id": "t_001",
    },
    overrides={
        "plugins.knowledge.namespace": "tenant:t_001",
        "plugins.skill.timeout": 30,
    },
)
```

- `models` 绑定这个 Agent 实例使用的模型 provider；Engine 不再持有模型配置。
- `mounts` 注入宿主持有的运行时资源；同名资源会覆盖 Engine 级资源。
- `metadata` 注入实例元数据；单次 chat/stream 请求的 metadata 仍可覆盖它。
- `overrides` 在插件初始化前 patch 并重新校验 Agent YAML 字段。它只接受 YAML 可序列化值，不能绑定运行时资源。

推荐使用的官方资源槽位是 `knowledge.index`、`graph.store`、`skill.catalog`、`tracing.exporter`。当前知识库插件也支持 `knowledge.documents`、`knowledge.embedding`、`knowledge.vector_store`、`knowledge.reranker` 等高级槽位，但它们仍然是运行时 mounts，不是 YAML 或 `overrides` 值。

## 请求 Metadata 与 Tracing

运行级 metadata 可以通过公开 Agent 入口传入，并进入 `ExecutionContext.state.metadata`。tracing 插件会把这些 metadata 安全复制到每个 span，同时保留 span 顶层的 `run_id`、`session_id`、`span_id`、`parent_span_id`。

```python
async for event in agent.stream_with_messages(
    messages,
    session_id="123",
    llm_options={"temperature": 0.2},
    run_options={"run_id": "run_abc"},
    metadata={
        "external_trace_id": "trace_1001",
        "conversation_id": 123,
        "agent_profile_id": 9,
    },
):
    ...
```

宿主应使用这个机制关联外部 trace 或审计记录。不要把外部关联 ID 塞进 prompt、tool args 或伪造 tool message。

## 取消与 Usage

每次运行都会通过 `run_options.run_id`、`metadata.run_id` 或引擎生成得到 `run_id`。宿主停止运行时应调用 Engine，让嵌套 `agent_call`、swarm、sidecar 内部 Agent run 和正在执行的工具共享同一个取消信号：

```python
engine.cancel_run("run_abc", reason="user_cancelled")
```

流式调用方会收到终态 `cancelled` 事件。非流式调用方会收到 `CancelledError`。终态 `done` 和 `cancelled` 事件都会在 `event.metadata["usage"]` 中携带聚合 usage：

```python
{
    "input_tokens": 1200,
    "output_tokens": 380,
    "total_tokens": 1580,
}
```

宿主应读取这个聚合值，不要从零散 LLM 事件或子 Agent 事件里自行累加。

## 多模态消息

上传、鉴权、OCR、图片识别、媒体 URL、base64 生成都属于宿主职责。引擎只接收标准化 message content parts，并通过 input/provider 边界传递：

```python
messages = [{
    "role": "user",
    "content": [
        {"type": "text", "text": "Explain this screenshot."},
        {"type": "image_url", "image_url": {"url": "https://example.com/screen.png"}},
        {"type": "image_base64", "media_type": "image/png", "data": "..."},
        {"type": "file_ref", "ref": "artifact:123", "metadata": {"name": "report.pdf"}},
    ],
}]
```

不支持的 part type 会直接失败。官方插件不访问宿主媒体服务，也不做部署相关的多模态预处理。

## ToolOutput 视图

`ToolOutput` 明确分离 LLM 上下文视图和 UI 展示视图：

- `content` 继续作为结构化工具结果，给宿主、UI、artifact 和程序化处理使用。
- `llm_view` 是可选的 LLM 文本视图。结构化 JSON 不利于模型理解时，工具应显式提供它。
- `context_view()` 写入 `MessageStore`，供下一轮 LLM 使用。它优先使用 `durable_summary`，其次 `llm_view`，最后使用完整 `content`，并保留 artifact refs。`summary` 是元数据，不替代 LLM 视图。
- `display_view()` 用于 `tool_result` 事件和宿主/UI 展示，暴露完整内容；UI 预览限制属于宿主 UI 层。
- 新代码应显式调用 `context_view()` 或 `display_view()`。

宿主不要 monkey patch `ToolOutput` 来改变 LLM 上下文行为。

工具实现禁止默认截断、省略或只给计数式 `llm_view`。列表类工具必须包含条目标识，例如名称、路径或 id。内容类工具默认返回完整内容，除非调用方显式请求 ranges 或分页。超大结果应外部化到 `ArtifactStore` artifact，`llm_view` 必须给出 `artifact_id` 并提示模型使用 `artifact_read` 或 `artifact_page`，不能输出含糊的省略文本。

官方高收益工具已经提供 LLM 友好的 `llm_view`，包括 `agent_list`、`agent_call`、`knowledge_search`、`graph_search`、`memory_search`、`swarm_dispatch`、`file_read`、`file_list`、`file_glob`、`file_tree`、`list_traces`、`get_trace`、`artifact_search`；它们仍保留结构化 `content` 给宿主使用。

## ArtifactStore

`ArtifactStore` 是大工具结果和工具产物的宿主存储边界。引擎定义协议，并提供 `InMemoryArtifactStore` 给本地开发和测试使用；生产宿主应通过 `Engine(artifact_store=...)` 注入自己的持久化实现。

协议支持三类写入：

- `put_text(...)` / `put_bytes(...)`：把文本或字节内容写入后端。
- `put_file_ref(path, ...)`：只注册文件路径引用，不把大文件读入内存，也不复制一份 10GB 文件。
- `read(...)` / `read_page(...)` / `search(...)`：分别由 `artifact_read`、`artifact_page`、`artifact_search` 暴露给模型继续读取。
- `stat(...)` / `delete(...)` / `delete_run(...)` / `gc(...)`：给宿主管理生命周期。

artifact 可携带 `run_id`、`expires_at`、`durable` 元数据。非 durable artifact 应按 run/session 策略清理；durable artifact 由宿主保留策略决定。

## 持久工具结果与子 Agent 事件

`compress` 插件会把 assistant `tool_calls` 和对应的 tool result 当作原子组保留。持久工具结果还会写入压缩边界，并在上下文打包后重新注入。默认 `agent_call` 和 `knowledge_search` 是持久工具；领域工具可以通过 `compress.durable_tools.names`、`compress.durable_tools.capabilities` 或 `ToolOutput.with_metadata({"durable": True, "durable_summary": "..."})` 标记。

`collaboration.agent_call` 会把子 Agent 活动转发到父运行事件流：

- `sub_agent_start`
- `sub_agent_step`
- `sub_agent_complete`

稳定字段包括 `parent_tool_call_id`、`sub_run_id`、`agent_name`、`agent_id`、`step.type`、`tool_call_id`、`tool_name`、`content`、`artifacts`、`error`、`duration_ms`。前端应直接渲染这些事件，不要解析 `agent_call` tool result 来伪造子 Agent 执行明细。

## API

HTTP API 是 OpenAI Chat Completions 明确子集。

- `POST /v1/chat/completions`
- `GET /v1/agents`
- `GET /v1/capabilities`

请求级 `tools` 和 `tool_choice` 刻意不支持。工具来自 Agent YAML 和插件，引擎统一执行 capability、风险元数据、插件 hooks、workspace policy 和审计事件。

客户端不应假设完整 OpenAI API 等价，请先调用 `/v1/capabilities` 做能力探测。详见 [docs/API.md](docs/API.md)。


## 内置插件

不运行基础 Agent 也不必存在的能力都属于插件。默认 `Engine.plugin_registry` 为空，内置和自定义插件都必须由宿主显式注册。

```python
from axc_agent_engine import AgentModels, Engine, PluginRegistry
from axc_agent_engine.plugins.builtin import BuiltinToolsPlugin, MemoryPlugin, PORPlugin
from my_project.plugins import MyCustomPlugin

registry = PluginRegistry()
registry.register_many([PORPlugin, BuiltinToolsPlugin, MemoryPlugin, MyCustomPlugin])
engine = Engine(plugin_registry=registry)
agent = engine.load_agent_template("./agents/my_agent.yaml").instantiate(models=AgentModels(default=llm))
```

| 插件 | 用途 |
| --- | --- |
| `react` | 逐轮调用模型和工具 |
| `por` | 计划、执行、观察与重规划，支持自动切换 |
| `builtin_tools` | 基础工具和 artifact 分页 |
| `knowledge` | ingestion、语义分块、混合检索、citation、rerank |
| `memory` | 记忆、治理工具、敏感信息策略、衰减、TTL |
| `output_format` | 最终输出契约、校验、修复、审计 |
| `post_process` | 按需在最终输出校验前追加轮次和 token 统计 |
| `graph` | 实体/关系图谱搜索和 CRUD |
| `skill` | 加载 skill，脚本通过 sandbox 执行 |
| `mcp` | MCP server 工具加载和治理 |
| `hooks` | 声明式 LLM/工具 hook 规则 |
| `compress` | 上下文窗口治理、摘要、召回、文件恢复 |
| `human_in_the_loop` | 人工审批和 `ask_human` |
| `risk_guard` | 动态工具风险分级 |
| `safety` | 输入清洗、prompt injection 检测、PII 脱敏 |
| `tracing` | trace/span 采集、审计模式、查询工具 |
| `reflexion` | 轮次结束和运行结束的自我反思 |
| `repetition_guard` | 重复工具调用/回复/结果检测 |
| `cost_statistics` | 输入、输出 token 统计 |
| `collaboration` | Agent 间调用和宿主编排薄入口 |
| `sessions` | 读取、发消息、停止和新建宿主会话 |
| `swarm` | 轻量并行 fan-out |

### 工程任务工具

在 `plugins.builtin_tools.load` 中按需加入 `file_grep`，授予 `file_read` 权限并配置 workspace。命令执行环境需要安装 ripgrep（`rg`）。结果包含路径、行号和匹配行。

`file_edit` 精确匹配原文，包括引号和换行。内联 `file_read` 返回整个文件的 `sha256`；将它作为 `expected_sha256` 传给 `file_edit`、`file_write` 或 `file_append`，发现内容已变时重新读取。跨进程工作区隔离由宿主负责。

本地命令的 stdout 和 stderr 分别最多内联 1 MiB。超出时完整输出保存在命令工作目录的 `.axc/logs/` 下。为工具挂载 `ArtifactStore` 后，可按返回的 ID 用 `artifact_read` 或 `artifact_page` 读取。宿主在制品引用到期后清理日志。

## Sidecar 旁路能力

旁路能力在 `axc_agent_engine.sidecar`，由宿主显式调用，不属于 Agent 核心执行链路。详见 [axc_agent_engine/sidecar/README.md](axc_agent_engine/sidecar/README.md)。

| 包 | 用途 |
| --- | --- |
| `sidecar.multi_agent` | 多 Agent 会话、调度器、停止条件、共享上下文 |
| `sidecar.simulation` | 结构化仿真内核 |
| `sidecar.eval` | 评测 case、标注 store、matcher、runner、report |
| `sidecar.agent_selector` | 宿主侧 Agent 路由和候选评分 |
| `sidecar.distiller` | 从轨迹蒸馏规则、工具偏好和 skill 候选 |
| `sidecar.failure_miner` | 聚类失败并建议修复/eval 覆盖 |
| `sidecar.cost_optimizer` | 成本估算和优化建议 |

会创建内部 Agent run 的 sidecar 入口支持标准运行上下文：

- `EvalRunner.run_cases(..., run_options, metadata, case_run_options, case_metadata)`
- `MultiAgentSession.run/stream(..., run_options, metadata, agent_run_options, agent_metadata)`
- `SimulationRunner.run/stream(..., run_options, metadata, actor_run_options, actor_metadata)`

factory metadata 会为每个 case、agent 或 actor 补充基础 metadata。`EvalCase.metadata` 仍然是评测/评分元数据，不是 request metadata。

## 运行流程

### 加载期

```mermaid
flowchart TD
    A["应用创建 Engine"] --> B["注入共享服务和 PluginRegistry"]
    B --> C["Engine.load_agent_template(agent.yaml)"]
    C --> D["校验 Agent YAML"]
    D --> E["AgentTemplate.instantiate(models, mounts, metadata, overrides)"]
    E --> F["应用 YAML overrides"]
    F --> G["合并 Engine resources 和 mounts"]
    G --> H["构造 PluginContext"]
    H --> I["从 registry 加载启用插件"]
    I --> J["Plugin.initialize(config, ctx)"]
    J --> K["Plugin.get_tools()"]
    K --> L["注册 ToolDefinition"]
    L --> M["Agent 绑定已选策略"]
    M --> N["启动已配置的 dispatcher consumer"]
```

### 单次运行

```mermaid
flowchart TD
    A["Agent.chat / stream / *_with_messages / resume"] --> B["RunRequest.create"]
    B --> C["合并 run_options.run_id 和请求 metadata"]
    C --> D["创建 ExecutionContext"]
    D --> E["MessageStore 初始化本轮消息"]
    E --> F["执行启动钩子并注入插件上下文"]
    F --> G{"选定的执行策略"}
    G -->|自定义插件| Custom["ExecutionLoop.run"]
    G -->|react 或 por auto| H["ReActLoop 轮次"]
    G -->|por_first 或 POR handoff| I["PORRunner / PORGraphRuntime"]
    H --> J["Plugin.transform_messages"]
    J --> K["compress: recent window + durable results + 原子工具组"]
    K --> L["LLMCaller"]
    L --> M["Plugin.pre_llm_call"]
    M --> N["剥离引擎内部 message 字段"]
    N --> O["LLM provider chat/stream"]
    O --> P{"LLM 响应"}
    P -->|最终回答| Q["on_round_end -> on_execution_complete -> done"]
    P -->|tool_calls| R["工具编排器"]
    R --> S["pre_tool_call / execute / post_tool_call"]
    S --> T["ToolOutput -> MessageStore role=tool"]
    T --> U["tool_result event"]
    U --> H
    S -->|agent_call / swarm| V["子 Agent dispatcher"]
    V --> W["sub_agent_start / sub_agent_step / sub_agent_complete"]
    W --> H
    I --> Q
    Q --> X["done/cancelled 事件携带 usage"]
    X --> Y["tracing span 携带 metadata 和 parent_span_id 落库"]
```

## 插件开发

```python
from axc_agent_engine import BasePlugin, ToolDefinition, ToolOutput
from axc_agent_engine.plugins.config_schema import config_field, config_schema

class MyPlugin(BasePlugin):
    name = "my_plugin"
    display_name = "My Plugin"
    priority = 30
    phase = "core"
    config_schema = config_schema(
        "my_plugin",
        "我的插件",
        "配置示例。",
        [
            config_field(
                "api_url",
                "接口地址",
                "string",
				"插件调用的后端接口地址。",
				label_en="API URL",
				default="http://localhost:5000",
			),
		],
        display_name_en="My Plugin",
    )

    def initialize(self, config: dict, ctx) -> None:
        super().initialize(config, ctx)
        self.api_url = config["api_url"]

    def get_tools(self) -> list[ToolDefinition]:
        return [ToolDefinition(
            name="my_tool",
            description="Does something useful",
            parameters={"type": "object", "properties": {"query": {"type": "string"}}},
            execute=self._execute,
        )]

    async def pre_tool_call(self, exec_ctx, tool_name, arguments):
        return True, arguments

    async def _execute(self, args: dict, context: dict) -> ToolOutput:
        data = {"query": args["query"], "matches": [{"title": "Example", "score": 0.92}]}
        return ToolOutput(
            content=data,
            content_type="json",
            summary="Found 1 match",
            llm_view="Search results:\n1. Example | score=0.92",
        )
```

插件必须继承 `BasePlugin`，必须声明 `config_schema`，工具必须返回 `ToolDefinition` 实例，`ToolRegistry` 不接受 dict。

宿主可以通过 `registry.list_plugin_config_schemas()` 或 `registry.get_plugin_config_schema("my_plugin")` 读取已注册插件 schema。加载器在调用 `initialize(config, ctx)` 前按 schema 校验 YAML，并拒绝未知字段。

```yaml
plugins:
  my_plugin:
    enabled: true
    api_url: "http://localhost:5000"
```

## CLI

```bash
export AXC_LLM_BASE_URL="https://api.openai.com/v1"
export AXC_LLM_API_KEY="sk-xxx"
export AXC_LLM_MODEL="gpt-4o"

axc chat --agent ./agents/my_agent.yaml
axc serve --agent ./agents/my_agent.yaml --port 8000
axc --log-level DEBUG --json-logs chat --agent ./agents/my_agent.yaml
```

CLI 日志参数是全局参数，需要放在子命令前。

以下命令按 CLI 的内置插件注册表装配 Agent，初始化配置的插件后输出 JSON，无需调用模型。

```bash
axc inspect --agent examples/01_basic_chat/agent.yaml
```

`strategy.reason` 中，`explicit` 表示模板点名选择，`single_enabled` 表示只启用了一个策略，`runtime_routing` 表示按旧模板的路由配置选择。输出包含插件初始化顺序、已注册工具及能力许可。运行时发现的工具在发现后才可见；自定义权限策略的静态许可显示为 `null`。宿主自行注册插件、挂载资源或注入权限策略时，可以直接调用已装配实例的 `agent.inspect()`。

## 设计决策

- **执行循环由策略插件提供。** Executor 管理单次运行，官方 ReAct 和 POR 插件实现循环，宿主可注册自己的策略。见[执行策略接口](docs/PLUGIN_DEVELOPMENT.md#execution-strategies)。
- **POR 状态转移交给 pydantic-graph。** `PORGraphRuntime` 负责 plan/step/observe/replan 循环；service 负责执行和 checkpoint。
- **Workflow resume 是运行时边界。** 默认使用 `MemoryWorkflowRuntime`；安装 workflow 可选依赖后可选择 `BurrWorkflowRuntime`。
- **插件是运行时扩展边界。** 知识库、记忆、图谱、MCP、输出修复、Skill 都属于插件。
- **推演是旁路。** 多 Agent session、simulation kernel、mode adapter 是宿主驱动 SDK 能力。
- **评测是旁路。** EvalRunner、EvalStore、AnnotationStore、AnnotationMatcher 和 report 是宿主驱动测试框架。
- **显式装载。** 宿主从官方目录选择要注册的类，模板用 `enabled: true` 启用插件。
- **插件 schema 必须声明。** 没有 `config_schema` 的插件不能注册。
- **工具来自插件。** Engine 核心不内置业务工具。
- **工具必须返回 `ToolOutput`。** 非 `ToolOutput` 返回会被拒绝。
- **工具定义必须是 `ToolDefinition`。** 不接受 dict。
- **部署相关协议不进入开源引擎。** 外部 API、部署数据库、鉴权流程、服务发现属于宿主插件。
- **LLM 配置在代码中。** Agent YAML 只描述运行时限制、能力和插件。
- **致命错误不做兜底隐藏。** 配置错误、运行时资源错误、工具协议错误、流程错误都必须明确失败。
- **官方插件保持精简。** 可以强化 Agent 能力，但不持有宿主网络 client、部署 API key、外部服务或部署协议。
- **包 `__init__.py` 只做符号导出。** 实现、注册表、factory、版本常量和协议都放在正常模块中。
- **运行时资源走 mounts。** 不要通过 YAML 或 `overrides` 传资源对象。
- **请求关联走 metadata。** 宿主通过 `metadata` 传外部 trace 标识，tracing span 原样携带落库。
- **API 是明确子集。** 请求级 `tools`、`tool_choice`、`n > 1` 会被拒绝。

## 测试

```bash
.venv/bin/python -m pytest --cov=axc_agent_engine -q
make test-core-cov
```

发布门禁要求总覆盖率不低于 95%，引擎本体覆盖率目标是 98%。

### 向运行中的会话补充消息

启动时由宿主指定唯一的 `run_id`。宿主可以在模型或工具仍在工作时，通过同一 Agent 和会话发消息。重试时沿用同一个消息 ID 和正文。

```python
async for event in agent.stream("检查两份报告", session_id="review", metadata={"run_id": "review-42"}):
    if event.type == EventType.STEP_START and event.step_id == 1:
        receipt = await agent.send_message("review-42", "重点检查第二份",
                                           session_id="review", message_id="change-1")
    if event.type in {EventType.DONE, EventType.CANCELLED, EventType.ERROR}:
        print(event.metadata.get("processed_message_ids", []), event.metadata.get("unprocessed_message_ids", []))
```

`accepted` 表示消息已按顺序排队，`processed` 表示已进入本次运行的消息历史。已启动的工具可以先完成。POR 在运行中发出计划和步骤事件，下次规划时根据补充要求调整剩余步骤。宿主可调用 `engine.cancel_run(run_id)` 取消运行。离线示例可以分别演练正常完成和取消。[可运行示例](examples/09_live_input/run.py)

多个 worker 共用检查点存储时，宿主须在启动或读取快照前为每个运行指定唯一执行者，插话和取消也路由给它；接管前须确保旧执行者已失去写入和调用工具的能力。启用租户记忆隔离后，旧快照缺少作用域时，宿主需从可信记录补回原归属才能恢复。宿主提供持久化 `CheckpointStore` 后，进程重启可以恢复已接收的输入。遇到写工具结果未知，恢复会暂停，错误中给出供宿主核对的调用 ID。[插件与恢复规范](docs/PLUGIN_DEVELOPMENT.md)

从宿主保留的 `cp.forkable` 检查点调用 `await agent.fork_run(run_id, checkpoint_id)`，用 `resume_stream` 独立继续子分支。[执行分叉说明](docs/ARCHITECTURE.md#execution-forks)
