# Raven Agent 执行内核替换说明

本文档用于把 `RavenSystem/microservices/agent` 的执行内核替换为 `AxcAgentEngine`。执行者不需要依赖聊天上下文，按本文档一次性完成迁移。

本次替换范围只包含“执行内核”：LLM 调用、ReAct/POR 主循环、工具调度、流式事件、checkpoint/resume、工具审计、执行状态写入。不要替换 Agent 管理 API、会话 API、知识库管理 API、记忆管理 API、图谱管理 API、技能管理 API、仿真 API、评测 API、开放平台 API、工作区 API 和前端协议入口。

## 目标边界

替换后仍保留 Raven Agent 微服务作为业务壳：

- 保留 `RavenSystem/microservices/agent/app.py` 和所有 router。
- 保留 `AgentService`、`ConversationService`、`MessageService`、`ExecLogService`、`TraceService`、`ExecutionStateService`、`TodoRecoveryService`。
- 保留 Raven 现有数据库模型、WebSocket 协议、会话消息表、执行日志表、trace 表。
- 保留 Raven 现有工具 Provider、知识库、记忆、图谱、技能、协作、swarm、MCP、context_compress、safeguard、output_format、cost_control 的业务实现。
- 用 `AxcAgentEngine` 替换 `RavenSystem/microservices/agent/executor/agent_executor.py` 中 LangGraph 执行循环。

替换后调用链必须变成：

```text
chat_router / open_api_router / internal_router
  -> ChatOrchestrator
  -> RavenEngineAdapter
  -> AxcAgentEngine.Engine / Agent / Executor
  -> RavenLLMHubProvider
  -> RavenToolPlugin / RavenToolBridge
  -> RavenEventBridge
  -> Raven persistence adapters
```

## 不允许改动的外部协议

这些协议不能因为替换内核而变化：

- WebSocket 入参仍使用 `agent_config_id`、`conversation_id`、`request_id`、`content`、`attachments`、`user_id`、`username`。
- WebSocket 出参仍使用现有事件名，例如 `conversation_created`、`stream_start`、`stream_delta`、`stream_end`、`thinking_start`、`thinking_delta`、`thinking_end`、`tool_args_preview`、`tool_call`、`tool_result`、`plan_created`、`step_started`、`step_completed`、`chat_complete`、`error`、`execution_interrupted`、`todo_resume`、`tool_approval_request`。
- `MessageService.add_message` 写入角色仍保持 `user`、`assistant`、`tool_call`、`tool_result`、`thinking`、`sub_agent`。
- 现有 Agent YAML 仍按 `RavenSystem/microservices/agent/executor/yaml_parser.py` 解析，不能要求业务仓库立刻改成 AxcAgentEngine YAML。
- 现有工具引用格式仍保留：`builtin:*`、`bapi:*`、`hologres:*`、`metabase:*`、`feishu:*`、`image:*`、`freecad:*`、`mcp:*`、仓库内 `tools/*.yaml`。

## 需要新增的目录和文件

在 `RavenSystem/microservices/agent` 下新增执行内核适配层：

```text
RavenSystem/microservices/agent/engine_adapter/
  __init__.py
  adapter.py
  config_translator.py
  event_bridge.py
  llm_provider.py
  tool_plugin.py
  tool_bridge.py
  persistence.py
  runtime_models.py
  media.py
  approvals.py
```

各文件职责如下：

- `adapter.py`：对 ChatOrchestrator 暴露唯一入口 `RavenEngineAdapter.run(...)` 和 `cancel(...)`。
- `config_translator.py`：把 Raven runtime agent dict 翻译成 AxcAgentEngine `AgentConfig` 兼容的临时配置对象，不写回数据库。
- `event_bridge.py`：把 AxcAgentEngine `Event` 转成 Raven WebSocket 事件、DB 消息和 Trace 写入。
- `llm_provider.py`：实现 AxcAgentEngine `LLMProvider`，内部调用 Raven `LLMInternalClient`。
- `tool_plugin.py`：实现 AxcAgentEngine `BasePlugin`，把 Raven `tools_config` 注册成 `ToolDefinition`。
- `tool_bridge.py`：把 AxcAgentEngine 工具调用转发给 Raven `execute_tool(...)`，并把结果转成 `ToolOutput`。
- `persistence.py`：实现 `MessagePersistence`、`CheckpointStore`、`AuditSink`、`SpanStore`、`ResultStore` 对 Raven DB/service 的适配。
- `runtime_models.py`：定义迁移层内部 dataclass，避免在多个文件传散乱 dict。
- `media.py`：把 Raven `attachments` 转成 AxcAgentEngine 可传给 LLM 的 OpenAI-compatible multimodal content。
- `approvals.py`：桥接 `tool_approval_request`、`ask_human` 和 Raven WebSocket 用户回复。

## 需要修改的现有文件

必须修改：

- `RavenSystem/microservices/agent/services/chat_orchestrator.py`
- `RavenSystem/microservices/agent/routers/chat_router.py`
- `RavenSystem/microservices/agent/executor/agent_executor.py`
- `RavenSystem/microservices/agent/executor/context_builder.py`
- `RavenSystem/microservices/agent/executor/context.py`
- `RavenSystem/microservices/agent/executor/yaml_parser.py`
- `RavenSystem/microservices/agent/systems/tools/executor.py`
- `RavenSystem/microservices/agent/systems/tools/orchestrator.py`
- `RavenSystem/microservices/agent/services/execution_state_service.py`
- `RavenSystem/microservices/agent/services/trace_service.py`
- `RavenSystem/microservices/agent/services/log_service.py`
- `RavenSystem/microservices/agent/services/message_service.py`

除 `RavenSystem/microservices/agent/routers/chat_router.py` 外不要改 router。`chat_router.py` 必须修改一处：`tool_approval_response` 当前直接访问 `executor.ctx.approval_queue`，替换后必须改为调用 adapter 方法，见第 13 步。

需要修改 AxcAgentEngine 时只允许做通用能力增强，不允许写 Raven 业务逻辑进 AxcAgentEngine：

- `AxcAgentEngine/axc_agent_engine/agent.py`
- `AxcAgentEngine/axc_agent_engine/core/events.py`
- `AxcAgentEngine/axc_agent_engine/core/schema.py`
- `AxcAgentEngine/axc_agent_engine/core/context.py`
- `AxcAgentEngine/axc_agent_engine/tools/context.py`
- `AxcAgentEngine/axc_agent_engine/tools/orchestrator.py`

## 执行顺序

### 1. 建立 AxcAgentEngine 本地依赖入口

修改 `RavenSystem/microservices/agent` 的依赖加载方式，使 Raven 能 import sibling repo 的 `axc_agent_engine`。

必须实现：

```python
# RavenSystem/microservices/agent/engine_adapter/__init__.py
from pathlib import Path
import sys

_ROOT = Path(__file__).resolve().parents[4]
_ENGINE = _ROOT / "AxcAgentEngine"
if str(_ENGINE) not in sys.path:
    sys.path.insert(0, str(_ENGINE))
```

注意：`engine_adapter/__init__.py` 的绝对路径是 `/Users/zhaoxin/Desktop/AxcRavenSystem/RavenSystem/microservices/agent/engine_adapter/__init__.py`，因此 `parents[4]` 必须解析到 `/Users/zhaoxin/Desktop/AxcRavenSystem`。如果执行者把 `engine_adapter` 放在别的位置，必须用下面的断言校验路径。

验收：

```bash
cd /Users/zhaoxin/Desktop/AxcRavenSystem
python3 - <<'PY'
import sys
sys.path.insert(0, "/Users/zhaoxin/Desktop/AxcRavenSystem/RavenSystem")
from microservices.agent.engine_adapter import adapter
from axc_agent_engine import Engine
from pathlib import Path
assert (Path("/Users/zhaoxin/Desktop/AxcRavenSystem/AxcAgentEngine/axc_agent_engine")).exists()
print("ok")
PY
```

### 2. 定义迁移层运行时模型

新增 `runtime_models.py`。

必须定义：

```python
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

@dataclass
class RavenEngineRunRequest:
    agent_config_id: int
    conversation_id: int
    user_id: int
    username: str
    content: str
    attachments: list[dict[str, Any]]
    agent: dict[str, Any]
    messages: list[dict[str, Any]]
    tools_config: list[dict[str, Any]]
    model_ref_id: int
    execution_context: Any | None = None
    request_id: str = ""
    stream: bool = True
    on_event: Callable[[str, dict[str, Any]], Awaitable[None]] | None = None

@dataclass
class RavenEngineRunResult:
    success: bool
    content: str = ""
    error: str = ""
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    cached_tokens: int = 0
    rounds: int = 0
    run_id: str = ""
    raw: dict[str, Any] = field(default_factory=dict)
```

所有适配器入口和出口必须只使用这两个模型，不要继续传十几个散乱参数。

### 3. 实现 RavenLLMHubProvider

新增 `llm_provider.py`，实现 `axc_agent_engine.llm.provider.LLMProvider`。

输入：

- `model_ref_id`
- `fallback_model_ref_id`
- `temperature`
- `max_tokens`
- `timeout`
- `thinking`
- `service_name="agent"`
- `on_fallback`
- `emit_internal: Callable[[str, dict], Awaitable[None]] | None`

内部复用：

- `microservices.llm_hub.client.LLMInternalClient`
- 可参考 `RavenSystem/microservices/agent/executor/llm_adapter.py`

必须实现：

```python
class RavenLLMHubProvider:
    @property
    def model(self) -> str: ...

    @property
    def tool_name_mapping(self): ...

    async def chat(self, messages: list[dict], tools: list[dict] | None = None, **kwargs) -> LLMResponse: ...

    async def stream(self, messages: list[dict], tools: list[dict] | None = None, **kwargs) -> AsyncIterator[LLMStreamChunk]: ...

    async def ask(self, prompt: str, **kwargs) -> str: ...

    async def close(self) -> None: ...
```

字段映射：

```text
Raven LLMInternalClient response choices[0].message.content
  -> LLMResponse.message.content

Raven choices[0].message.tool_calls
  -> LLMResponse.message.tool_calls

Raven usage.input_tokens / prompt_tokens
  -> LLMUsage.input_tokens

Raven usage.output_tokens / completion_tokens
  -> LLMUsage.output_tokens

Raven cache read/write tokens
  -> LLMUsage.cached_tokens；如能区分 read/write，额外在 LLMStreamChunk.raw 或 metadata 中保留 {"cache_type": "read" | "write", "tokens": n}

Raven thinking delta
  -> LLMStreamChunk.thinking_delta

Raven text delta
  -> LLMStreamChunk.content_delta

Raven tool args progress
  -> LLMStreamChunk.tool_call_delta = {"type": "args_delta", "id": ..., "name": ..., "delta": ..., "preview": ...}

Raven completed tool call
  -> final LLMMessage.tool_calls
```

fallback 规则必须沿用旧逻辑：

- 只有 `fallback_model_ref_id > 0` 才能 fallback。
- 仅对 timeout、connection、429、500、502、503、504、rate limit、unavailable、overloaded 触发 fallback。
- fallback 发生时通过 `emit_internal("model_fallback", {...})` 通知 `RavenEventBridge`。
- fallback 后单次调用结束要恢复主模型。
- 第一阶段不要在 `AgentModels(fallback=...)` 再传另一个 fallback provider，避免 Axc `LLMCaller` fallback 和 RavenLLMHubProvider 内部 fallback 双重触发。`AgentModels` 只传 `default=RavenLLMHubProvider(...)`，备用模型由该 provider 内部按 Raven 旧规则处理。

验收：

- 单元测试模拟 `LLMInternalClient.chat` 返回普通文本，`chat()` 输出 `LLMResponse`。
- 单元测试模拟 tool_calls，`chat()` 输出 `LLMMessage.tool_calls`。
- 单元测试模拟 stream delta、thinking delta、usage、tool args preview，`stream()` 输出对应 `LLMStreamChunk`。
- 单元测试模拟主模型超时、备用模型成功，确认触发 fallback 事件。

### 4. 实现媒体附件转换

新增 `media.py`。

输入：

- Raven `content: str`
- Raven `attachments: list[dict]`
- `agent_config_id`

输出：

- OpenAI-compatible user content：

```python
[
    {"type": "text", "text": "..."},
    {"type": "image_url", "image_url": {"url": "..."}},
]
```

实现要求：

- 复用 `MessageService.build_user_content(...)` 的现有逻辑，不能改变附件 URL 签名、鉴权和 media service 路径。
- 无附件时返回纯字符串，保持 AxcAgentEngine 当前 `Agent.stream(message: str)` 可用。
- 有附件时必须走 `Agent.stream_with_messages(messages)`，不要把多模态 content 强转字符串。

当前 AxcAgentEngine `Agent._execute_stream(...)` 已支持 `inject_messages` 并会把完整 `processed.messages` 注入 `executor.message_store`。迁移时必须保留这个行为，并补一条测试：最后一条 user message 的 `content` 为 `list[dict]` 时，`RavenLLMHubProvider` 收到的 messages 中仍是多模态 list，不是字符串化后的内容。

### 5. 实现 RavenToolPlugin

新增 `tool_plugin.py` 和 `tool_bridge.py`。

`RavenToolPlugin` 必须继承 `axc_agent_engine.plugins.base.BasePlugin`。

插件配置来自 `RavenEngineRunRequest.tools_config`，不是来自 AxcAgentEngine YAML 文件。实现方式必须是插件工厂闭包捕获运行时状态：

```python
registry = PluginRegistry()
registry.register_factory(
    "raven_tools",
    lambda: RavenToolPlugin(runtime=raven_tool_runtime),
)
```

`RavenToolPlugin.name` 必须固定为 `"raven_tools"`，否则 `load_plugins(...)` 会因为配置 key 和插件声明名不一致而失败。

`get_tools()` 必须把每个 Raven tool config 转成 `ToolDefinition`：

```text
tc["name"].replace(":", "_")
  -> ToolDefinition.name

tc["description"]
  -> ToolDefinition.description

tc["parameters"]
  -> ToolDefinition.parameters

tc["is_read_only"]
  -> ToolDefinition.is_read_only

tc["timeout"]
  -> ToolDefinition.timeout

tc["defer"]
  -> ToolDefinition.deferred

tc["risk_level"]
  -> ToolDefinition.risk_level
```

必须跳过：

- `tc["type"] == "unknown"`
- name 为空
- name 重复

deferred 工具处理：

- deferred 工具仍注册为 `ToolDefinition(deferred=True)`。
- AxcAgentEngine `ToolRegistry.get_openai_schemas()` 会自动从初始 schema 隐藏 `deferred=True` 的工具。
- 本次只加载 `raven_tools` 插件，不加载 Axc 内置 `builtin_tools` 插件，因此 `RavenToolPlugin` 必须自己额外注册一个非 deferred 的 `tool_search` 工具。
- `tool_search` 行为必须复用 `RavenSystem/microservices/agent/systems/tools/providers/builtin/tool_search.py` 的 `execute_tool_search(arguments, context)`。
- `tool_search` 命中某个 deferred 工具后，下一轮 LLM 调用必须能看到被激活工具的 schema；实现方式是在 `RavenToolPlugin.pre_llm_call(...)` 中把本轮激活的 deferred tool schema 追加到 tools 列表，并在该工具使用后从激活集合移除。

`tool_bridge.py` 必须调用 Raven 现有执行器：

```python
from microservices.agent.systems.tools.executor import execute_tool
```

上下文必须包含：

```python
{
    "repo_id": ctx.repo_id,
    "agent_config_id": ctx.agent_config_id,
    "knowledge_base_ids": ctx.knowledge_base_ids,
    "graph_ids": ctx.graph_ids,
    "agent_call_depth": ctx.agent_call_depth,
    "user_id": ctx.user_id,
    "on_event": event_bridge.emit_from_tool,
    "event_loop": running_loop,
    "deferred_tools": deferred_tools,
    "approval_queue": approval_queue,
}
```

当前 AxcAgentEngine `ToolContext` 没有这些 Raven 业务字段。不要把字段硬塞到 Engine 通用 context；必须在 `ToolDefinition.execute` 的闭包里捕获 `RavenToolRuntime` dataclass。

工具结果映射：

```text
ToolCallResult.success == True and result is dict/list
  -> ToolOutput.json_output(result)

ToolCallResult.success == True and result is str
  -> ToolOutput.text(result)

ToolCallResult.success == False
  -> ToolOutput.error(error)

ToolCallResult.duration_ms
  -> ToolOutput.metadata["duration_ms"]

ToolCallResult.result 大于 2000 字符
  -> summary 用旧 _compress_result 或 AxcAgentEngine ToolOutput.compact_view
```

验收：

- `file_read` 能执行并返回文本。
- `web_search` 能执行并返回 JSON。
- 一个业务 provider，例如 `bapi:*` 或 `hologres:*`，能按原 provider 分发执行。
- dangerous 工具仍触发用户确认。
- deferred 工具不会在初始 tools schema 里全部暴露，但可以被搜索到并执行。

### 6. 实现 Raven 配置翻译器

新增 `config_translator.py`。

输入：

- `agent: dict`
- `model_ref_id: int`
- `conversation_id: int`
- `user_id: int`
- `tools_config: list[dict]`

输出：

- `axc_agent_engine.core.schema.AgentConfig`
- `RavenRuntimeMetadata`

字段映射：

```text
agent["name"]                       -> AgentConfig.name
agent["description"]                -> AgentConfig.description
agent["system_prompt"]              -> AgentConfig.system_prompt
agent["max_rounds"]                 -> runtime.max_rounds
agent["thinking"]                   -> runtime.thinking
agent["parallel_tool_calls"]        -> runtime.parallel_tool_calls
agent["human_in_the_loop"]          -> runtime.human_in_the_loop
agent["stream"]                     -> adapter run stream 参数
agent["planning"].enabled/mode      -> runtime.routing.mode
agent["workspace"] if exists        -> runtime.workspace
settings.LLM_TIMEOUT                -> llm provider timeout
settings.TOOL_APPROVAL_TIMEOUT      -> approval bridge timeout
```

metadata 必须保留：

```text
agent_config_id
conversation_id
user_id
repo_id
knowledge_base_ids
graph_ids
model_ref_id
fallback_model_ref_id
utility_model_ref_id
max_tool_repeat
max_tool_name_repeat
max_tool_total_calls
context_compress
risk_rules
agent_call_depth
request_id
```

插件映射：

```text
agent["memory_config"] / agent["memory"] enabled
  -> 不直接启用 AxcAgentEngine memory plugin；第一阶段继续通过 Raven active_systems 注入上下文和工具。

agent["knowledge_base_ids"]
  -> 不直接启用 AxcAgentEngine knowledge plugin；继续通过 Raven KnowledgeSystem 注入工具。

agent["graph_ids"]
  -> 不直接启用 AxcAgentEngine graph plugin；继续通过 Raven GraphSystem 注入工具。

agent["context_compress"]
  -> 第一阶段保留 Raven context_compress 系统；不要同时启用 Axc compress plugin，避免双重压缩。

agent["safeguard"]
  -> 保留 ChatOrchestrator 输入/输出检查；不要同时启用 Axc safety plugin。

agent["output_format"]
  -> 保留 ChatOrchestrator 输出校验；不要同时启用 Axc output_format plugin。

agent["cost_control"]
  -> 保留 Raven cost_control 系统，Axc cost event 只作为补充写 trace。
```

注意：第一阶段只迁移执行循环，不要同时迁移业务子系统，否则无法定位回归来源。

### 7. 实现 Raven persistence adapters

新增 `persistence.py`。

必须实现以下 AxcAgentEngine protocol：

```python
MessagePersistence
CheckpointStore
AuditSink
SpanStore
ResultStore
```

实现映射：

```text
MessagePersistence.save(session_id, messages)
  -> 不直接覆盖 Raven 消息表；只保存 Engine 内部 session 快照到 execution_state 或新增 engine_session_snapshots 表。

MessagePersistence.load(session_id)
  -> 从 session 快照读；没有快照时返回 []。

CheckpointStore.save(checkpoint)
  -> ExecutionStateService.update_status / 新增 JSON checkpoint 字段。

CheckpointStore.latest(run_id)
  -> 从 ExecutionStateService 取最新 checkpoint。

CheckpointStore.list(run_id)
  -> 返回该 run_id 的 checkpoint 序列。

AuditSink.record(event)
  -> AuditService 或 ExecLogService 追加结构化审计。

SpanStore.save_span(span)
  -> TraceService.start_trace/end_trace 的 adapter；如果 span 已完整，写一条 trace。

ResultStore.put(content, metadata)
  -> 使用 Raven 现有 artifact/result 存储；没有现成表时新增 engine_artifacts 表。
```

如果当前 DB schema 没有 checkpoint JSON 和 artifact 表，需要新增迁移：

```text
engine_checkpoints:
  id
  run_id
  sequence
  status
  kind
  state_json
  created_at

engine_artifacts:
  id
  artifact_id
  content_type
  content
  metadata_json
  created_at
```

不要把大工具结果写入 conversation message content。conversation message 只写 compact view。

### 8. 实现 RavenEventBridge

新增 `event_bridge.py`。

输入：

- AxcAgentEngine `Event`
- `RavenEngineRunRequest`
- DB session factory
- `send_ws` callback
- `ExecLogService`
- `TraceService`
- `MessageService`
- `DeltaThrottle`

输出：

- Raven WebSocket 事件
- Raven DB message
- Raven trace
- Raven exec log

事件映射必须如下：

```text
Axc EventType.STREAM_START
  -> Raven "stream_start"

Axc EventType.STREAM_DELTA
  -> Raven "stream_delta", data.content = event.content

Axc EventType.STREAM_END
  -> Raven "stream_end"

Axc EventType.THINKING_START
  -> Raven "thinking_start"

Axc EventType.THINKING_DELTA
  -> Raven "thinking_delta", data.content = event.content

Axc EventType.THINKING_END
  -> Raven "thinking_end"

Axc EventType.TOOL_ARGS_PREVIEW
  -> Raven "tool_args_preview", data.tool_call_id, partial_args, raw_json

Axc EventType.TOOL_CALL
  -> Raven "tool_call", data.tool_name, arguments, tool_call_id
  -> MessageService.add_message(role="tool_call")
  -> TraceService.start_trace(type="tool_call")

Axc EventType.TOOL_RESULT
  -> Raven "tool_result", data.tool_name, result, tool_call_id, duration_ms
  -> MessageService.add_message(role="tool_result")
  -> TraceService.end_trace(type="tool_call")

Axc EventType.PLAN_CREATED
  -> Raven "plan_created"
  -> TraceService.start_trace(type="plan_create")

Axc EventType.STEP_START
  -> Raven "step_started"
  -> TraceService.start_trace(type="step_execute")

Axc EventType.STEP_COMPLETED
  -> Raven "step_completed"
  -> TraceService.end_trace(type="step_execute")

Axc EventType.CACHE_HIT
  -> 如果 event.metadata.cache_type 存在，映射为 Raven "cache_read" 或 "cache_write"
  -> 如果只有 event.metadata.cached_tokens，映射为 Raven "cache_read"，data.tokens = cached_tokens

Axc EventType.COST_UPDATE
  -> no user-visible event unless existing frontend expects it; write trace/log only.

Axc EventType.ERROR
  -> Raven "error"

Axc EventType.DONE
  -> do not send "chat_complete" here; ChatOrchestrator post_process sends it.
```

Raven 特有事件仍由 bridge 直接发：

```text
model_fallback
tool_approval_request
human_input_request
sub_agent_start
sub_agent_step
sub_agent_complete
thinking_redacted
llm_prompt_snapshot
```

节流：

- 复用 `DeltaThrottle`。
- `stream_delta` 和 `thinking_delta` 可以合并。
- `tool_args_preview` 可以丢弃高频中间帧。
- `stream_end` 和 `thinking_end` 前必须 flush。

Trace 根节点：

- run 开始时创建 `ExecLogService.create_log(...)`。
- run 开始时创建 `TraceService.start_trace(..., type="agent_run")`。
- run 结束时 `TraceService.end_trace(...)`。
- run 失败时 trace status 为 `failed`，并写入 error。

### 9. 实现 human approval bridge

新增 `approvals.py`。

保留 Raven 旧行为：

- dangerous 工具在流式模式下发送 `tool_approval_request`。
- 前端回复通过现有 WebSocket 控制消息进入 active executor。
- `ask_human` 工具发送 `human_input_request`，等待用户回复后继续。

实现方式：

- `RavenEngineAdapter` 为每个 conversation 建立 `asyncio.Queue`。
- `ChatOrchestrator.active_executors[conv_id]` 保存 adapter，不保存旧 `AgentExecutor`。
- `adapter.cancel()` 取消 AxcAgentEngine run。
- `adapter.submit_approval(...)` 或 `adapter.submit_human_input(...)` 往 queue 写入用户回复。

当前 `RavenSystem/microservices/agent/routers/chat_router.py` 的 `tool_approval_response` 分支直接访问旧执行器内部字段，必须从：

```python
executor = active_executors[conv_id]
if executor.ctx.approval_queue is not None:
    await executor.ctx.approval_queue.put(msg)
```

改为：

```python
executor = active_executors[conv_id]
if hasattr(executor, "submit_approval"):
    await executor.submit_approval(msg)
```

如果同一 WebSocket 消息类型也承载 `ask_human` 回复，`submit_approval(msg)` 内部必须根据 payload 字段同时兼容 approval 和 human input；如果前端另有 `human_input_response` 类型，也要在 router 中调用 `submit_human_input(msg)`。

### 10. 实现 RavenEngineAdapter

新增 `adapter.py`。

对外接口：

```python
class RavenEngineAdapter:
    def __init__(self, request: RavenEngineRunRequest, db_session_factory, send_ws): ...
    async def run(self) -> RavenEngineRunResult: ...
    def cancel(self) -> None: ...
    async def submit_approval(self, payload: dict) -> None: ...
    async def submit_human_input(self, payload: dict) -> None: ...
```

`run()` 必须按顺序执行：

1. 用 `config_translator` 构造 AxcAgentEngine runtime config 和 Raven metadata。
2. 构造 `RavenLLMHubProvider`，主模型用 `request.model_ref_id`，备用模型用 `agent["fallback_model_ref_id"]`。
3. 构造 persistence adapters。
4. 构造 `RavenToolPlugin`，传入 `tools_config` 和 Raven runtime metadata。
5. 构造 `PluginRegistry`，注册 `RavenToolPlugin` 工厂。插件名固定为 `raven_tools`，临时 `AgentConfig.plugins` 必须包含 `{"raven_tools": {"enabled": True}}`，否则 Engine 不会加载这个插件。
6. 构造 `AxcAgentEngine.Engine(...)`，Engine 只接收基础设施和插件注册表，不接收模型。
7. 用临时 YAML 文件调用 `Engine.load_agent_template(yaml_path).instantiate(models=AgentModels(default=provider))`。临时文件必须放在系统临时目录，run 结束删除。临时 YAML 只包含 AxcAgentEngine 需要的最小配置：

```yaml
name: "<agent name>"
description: "<agent description>"
system_prompt: "<translated system prompt>"
runtime:
  max_rounds: <agent.max_rounds>
  thinking: "<agent.thinking>"
  parallel_tool_calls: <agent.parallel_tool_calls>
  human_in_the_loop: <agent.human_in_the_loop>
  workspace: "<workspace>"
  routing:
    mode: "<auto|react_only|por_first>"
plugins:
  raven_tools:
    enabled: true
    required: true
```

8. load agent。
9. 构造 messages：
   - 使用 `request.messages` 作为历史消息。
   - 当前用户消息由 `media.py` 构造。
   - 有多模态时调用 `agent.stream_with_messages(messages)`。
   - 无多模态时也建议调用 `agent.stream_with_messages(messages)`，保证历史一致。
   - 传给 `stream_with_messages` 的最后一条 user message 允许是 `str` 或 OpenAI-compatible `list[dict]`。如果当前用户消息是多模态 list，POR 规划的 `user_message` 文本目标取 list 中第一段 `{"type": "text"}` 的 `text`，但 LLM messages 必须保留完整多模态内容。
10. 遍历 Axc events，交给 `RavenEventBridge.handle(event)`。
11. 累积最终 content、usage、rounds、run_id。
12. close Engine。
13. 返回 `RavenEngineRunResult`。

禁止事项：

- 不要在每个 stream delta 重新创建 Engine。
- 不要把 Engine 做成全局单例后跨 agent 复用插件状态，除非确认插件无状态且 registry 不冻结冲突。
- 不要让 AxcAgentEngine 直接 import Raven services；Raven 依赖只能存在 `engine_adapter` 层。

当前 AxcAgentEngine `Agent._execute_stream(...)` 已通过 `inject_messages` 把完整 `processed.messages` 注入 `Executor.message_store`，并设置 `executor.skip_user_init = True`。迁移时必须保留这个实现，不要重写成只传最后一条用户消息。对应当前实现为：

```text
Agent.stream_with_messages(messages, ...)
  -> Agent._execute_stream(user_message, inject_messages=messages)

Agent._execute_stream(..., inject_messages)
  -> executor.message_store.extend(processed.messages)，executor.skip_user_init = True
```

验收标准：Raven `MessageService.build_messages_for_llm(...)` 产出的 system/user/assistant 历史必须原样进入 RavenLLMHubProvider，不允许只传最后一条用户消息。

### 11. 修改 ChatOrchestrator 执行入口

修改 `RavenSystem/microservices/agent/services/chat_orchestrator.py` 的 `_execute(...)`。

替换旧逻辑：

```python
executor = AgentExecutor(self.ctx, on_event=on_event)
self.active_executors[conv_id] = executor
executor.set_messages(messages)
executor.set_tools(tools_config)
result = await executor.run(user_content)
```

为：

```python
from microservices.agent.engine_adapter.adapter import RavenEngineAdapter
from microservices.agent.engine_adapter.runtime_models import RavenEngineRunRequest

run_request = RavenEngineRunRequest(
    agent_config_id=self.agent_config_id,
    conversation_id=conv_id,
    user_id=self.user_id,
    username=self.username,
    content=self.content,
    attachments=self.attachments,
    agent=self.agent,
    messages=messages,
    tools_config=tools_config,
    model_ref_id=self.actual_model_ref_id,
    request_id=self.request_id,
    stream=self.ctx.stream,
    execution_context=self.ctx,
    on_event=on_event,
)
executor = RavenEngineAdapter(run_request, SessionLocal, self._send_ws)
self.active_executors[conv_id] = executor
result_model = await executor.run()
result = {
    "success": result_model.success,
    "content": result_model.content,
    "error": result_model.error,
    "total_input_tokens": result_model.total_input_tokens,
    "total_output_tokens": result_model.total_output_tokens,
    "rounds": result_model.rounds,
}
```

保留 `_validate_input()`、`_load_agent_template()`、SafeGuard 输入检查、`_resolve_conversation()`、`_save_user_message()`、`_resolve_model()`、`_build_context()`、`_build_messages()`、SafeGuard 输出检查、OutputFormat 输出校验、`_post_process()`、`_cleanup()`。

### 12. 处理旧 AgentExecutor

`RavenSystem/microservices/agent/executor/agent_executor.py` 不要立刻删除。

改成兼容壳：

```python
class AgentExecutor:
    """Deprecated wrapper. Execution is handled by engine_adapter.RavenEngineAdapter."""
```

保留：

- `cancel()`
- `set_messages()`
- `set_tools()`
- `run()`

`run()` 内部直接抛明确错误：

```python
raise RuntimeError("AgentExecutor has been replaced by RavenEngineAdapter; use ChatOrchestrator._execute")
```

这样可以快速发现遗漏调用点。等全仓 `rg "AgentExecutor"` 只剩文档和这个壳后，再删除。

### 13. 统一取消和 active_executors 协议

检查以下文件里对 `active_executors` 的访问：

```bash
rg "active_executors|cancel\\(|approval_queue|tool_approval" /Users/zhaoxin/Desktop/AxcRavenSystem/RavenSystem/microservices/agent
```

所有调用点必须只依赖这些方法：

```python
cancel()
submit_approval(payload)
submit_human_input(payload)
```

不要访问 adapter 私有字段。

### 14. 保留并桥接 active systems

`ExecutionContextBuilder.build(...)` 当前会：

- 解析 `tools_config`
- 解析 `knowledge_base_ids`
- 解析 `graph_ids`
- 构造 Raven `ExecutionContext`
- 调用 `get_active_systems(agent)`
- 执行 `system.inject_context(...)`
- 执行 `system.get_tools(...)`
- 注入 `ask_human`

这部分第一阶段必须保留。AxcAgentEngine 只接收最终 `messages` 和最终 `tools_config`。

不要在第一阶段改写这些系统：

```text
RavenSystem/microservices/agent/systems/memory
RavenSystem/microservices/agent/systems/knowledge
RavenSystem/microservices/agent/systems/graph
RavenSystem/microservices/agent/systems/skill
RavenSystem/microservices/agent/systems/collaboration
RavenSystem/microservices/agent/systems/swarm
RavenSystem/microservices/agent/systems/mcp
RavenSystem/microservices/agent/systems/context_compress
RavenSystem/microservices/agent/systems/safeguard
RavenSystem/microservices/agent/systems/output_format
RavenSystem/microservices/agent/systems/cost_control
```

### 15. 补齐循环保护

旧内核有三类工具循环保护：

```text
max_tool_repeat
max_tool_name_repeat
max_tool_total_calls
```

当前 AxcAgentEngine 没有 Raven 旧内核的三类工具循环保护。必须在 `RavenToolPlugin` 中实现，不要改 AxcAgentEngine 通用 `tools/orchestrator.py`，避免把 Raven 特定错误文案和阈值语义写进通用引擎。

实现方式：

- 在 `RavenToolRuntime` 中维护 `last_tool_signature`、`same_args_repeat_count`、`last_tool_name`、`same_name_repeat_count`、`tool_total_counter`。
- signature 计算沿用旧内核：`f"{tool_name}:{md5(json.dumps(arguments, sort_keys=True, ensure_ascii=False).encode()).hexdigest()}"`。
- 检查发生在工具真正执行前；命中限制时返回 `ToolOutput.error(...)`，并通过 `RavenEventBridge` 发送最终错误内容，让主循环结束。

行为必须一致：

- 同一工具同一参数连续调用达到 `max_tool_repeat`，返回错误并终止。
- 同一工具不同参数连续调用达到 `max_tool_name_repeat`，返回错误并终止。
- 单个工具累计调用达到 `max_tool_total_calls`，返回错误并终止。

错误文案沿用旧内核，避免前端或日志分析规则变化：

```text
执行异常：工具 {name} 以相同参数陷入循环调用，已强制停止。请检查参数或换个方式提问。
执行异常：工具 {name} 连续调用{count}次，已强制停止。请换个方式提问。
执行异常：工具 {name} 累计调用{count}次，已强制停止。请换个方式提问。
```

### 16. 补齐 prompt snapshot 和 token 统计

旧内核会发送内部事件 `llm_prompt_snapshot` 给 trace，不发 WebSocket。

替换后必须在 `RavenLLMHubProvider.chat/stream` 调用前把实际 messages 交给 `RavenEventBridge`：

```python
await bridge.emit_internal("llm_prompt_snapshot", {
    "messages": messages,
    "message_count": len(messages),
})
```

usage 映射必须累计到最终 result：

```text
result.total_input_tokens
result.total_output_tokens
result.cached_tokens
```

并写入：

- `request.execution_context.total_input_tokens`
- `request.execution_context.total_output_tokens`
- `TraceService.end_trace(... input_tokens=..., output_tokens=...)`
- `chat_complete` 事件里的 `total_input_tokens` 和 `total_output_tokens`

### 17. 恢复 resume/checkpoint 语义

Raven 现有恢复入口：

- `ExecutionStateService.get_resume_info(...)`
- `ExecutionStateService.mark_running(...)`
- `ExecutionStateService.mark_completed(...)`
- `ExecutionStateService.update_status(...)`
- `TodoRecoveryService.get_resume_info(...)`

替换后必须保持：

- 打开已有 conversation 时仍发送 `execution_interrupted`。
- 打开已有 conversation 时仍发送 `todo_resume`。
- 执行开始时仍 mark running。
- 成功后仍 mark completed。
- 失败后仍 update status error。

Axc checkpoint 的 `run_id` 必须写入 ExecutionStateService，以便后续 `agent.resume_stream(run_id)` 能找到。

如果现有恢复按钮只传 conversation_id 而不传 run_id，必须在 adapter 中通过 conversation_id 查询 latest run_id。

### 18. 调整测试

新增测试目录：

```text
RavenSystem/microservices/agent/tests/test_engine_adapter_llm_provider.py
RavenSystem/microservices/agent/tests/test_engine_adapter_tool_plugin.py
RavenSystem/microservices/agent/tests/test_engine_adapter_event_bridge.py
RavenSystem/microservices/agent/tests/test_engine_adapter_config_translator.py
RavenSystem/microservices/agent/tests/test_chat_orchestrator_engine_adapter.py
```

测试必须覆盖：

- 普通文本对话。
- 带历史消息的对话。
- 图片附件触发 vision model。
- LLM thinking stream。
- tool args preview。
- tool call + tool result 持久化。
- dangerous 工具 approval。
- ask_human。
- fallback model。
- POR plan_created、step_started、step_completed。
- max_tool_repeat。
- checkpoint save/latest。
- ChatOrchestrator 输出 result 字段保持旧结构。

### 19. 全仓扫描验收

完成代码后执行：

```bash
cd /Users/zhaoxin/Desktop/AxcRavenSystem
rg "AgentExecutor\\(" RavenSystem/microservices/agent
rg "langgraph|ToolNode|astream_events|RichGenerationChunk" RavenSystem/microservices/agent/executor RavenSystem/microservices/agent/services
rg "LLMHubChatModel" RavenSystem/microservices/agent
```

期望：

- `AgentExecutor(` 不应出现在 `ChatOrchestrator` 主路径。
- `langgraph`、`ToolNode`、`astream_events` 不应出现在新的执行主路径。
- `RichGenerationChunk` 只允许留在旧 `llm_adapter.py` 或被新 `llm_provider.py` 明确复用。
- `LLMHubChatModel` 不应被新主路径使用。

### 20. 运行测试

执行：

```bash
cd /Users/zhaoxin/Desktop/AxcRavenSystem/AxcAgentEngine
python3 -m pytest

cd /Users/zhaoxin/Desktop/AxcRavenSystem/RavenSystem
python3 -m pytest microservices/agent/tests
```

如果 RavenSystem 当前没有完整 pytest 环境，至少执行新增 adapter 测试和 ChatOrchestrator 单测。

### 21. 手工联调清单

按顺序手工验证：

1. 新建普通文本会话，确认流式输出、DB assistant message、chat_complete。
2. 继续同一会话，确认历史消息被带入。
3. 上传图片，确认使用 `vision_model_ref_id`，无视觉模型时仍报旧错误。
4. 调用 `file_read`，确认 `tool_call`、`tool_result`、trace 都存在。
5. 调用业务工具，例如 `bapi:*` 或 `hologres:*`。
6. 调用 dangerous shell/python/powershell 工具，确认 approval 流程。
7. 调用 `ask_human`，确认等待用户回复后继续。
8. 触发 POR，确认计划和步骤事件。
9. 模拟 LLM 主模型失败，确认 fallback 事件和最终成功。
10. 中断执行，刷新会话，确认 `execution_interrupted` 和 resume 信息。
11. 触发输出格式错误，确认 ChatOrchestrator 仍执行 output_format 修复或 strict error。
12. 触发 SafeGuard 输入/输出，确认仍在 ChatOrchestrator 层生效。

## 完成标准

迁移完成必须同时满足：

- Raven 前端 WebSocket 协议无变化。
- Raven Agent YAML 无需改写。
- Raven 现有工具 Provider 无需改写。
- Raven 管理 API 无需改写。
- `ChatOrchestrator._execute` 主路径不再实例化旧 `AgentExecutor`。
- AxcAgentEngine 负责 ReAct/POR 主循环、LLM 调用抽象、工具调度和 checkpoint。
- 所有执行事件都能被 `RavenEventBridge` 转成旧事件。
- ExecLog、Trace、Message、ExecutionState 仍完整写入。
- 普通文本、工具调用、多模态、approval、resume、fallback、POR 都通过测试和手工联调。

## 回滚点

保留旧 `AgentExecutor` 文件作为短期回滚点。

如联调发现阻塞问题，只需在 `ChatOrchestrator._execute` 中把 `RavenEngineAdapter` 调用切回旧代码块，即可恢复旧内核。回滚不能删除新增 adapter 文件，避免丢失迁移进度。

## 后续清理

确认新内核稳定后再做这些清理：

- 删除旧 `RavenSystem/microservices/agent/executor/agent_executor.py`。
- 删除旧 `RavenSystem/microservices/agent/executor/por/*`。
- 删除旧 `RavenSystem/microservices/agent/executor/llm_adapter.py`。
- 把 Raven active systems 逐个迁移成 AxcAgentEngine 插件。
- 把 Raven memory/knowledge/graph/skill/swarm 的工具从桥接执行改成原生 `ToolDefinition`。
- 把 checkpoint/artifact 临时表整理成正式模型和管理 API。

## 附录 A：逐文件施工清单

没有上下文的执行者必须按下面清单改，不要自行调整文件边界。

### 新增文件

```text
RavenSystem/microservices/agent/engine_adapter/__init__.py
  - 注入 /Users/zhaoxin/Desktop/AxcRavenSystem/AxcAgentEngine 到 sys.path。
  - 不放业务逻辑。

RavenSystem/microservices/agent/engine_adapter/runtime_models.py
  - 定义 RavenEngineRunRequest、RavenEngineRunResult、RavenRuntimeMetadata、RavenToolRuntime。

RavenSystem/microservices/agent/engine_adapter/config_translator.py
  - Raven agent dict -> Axc AgentConfig 最小 YAML dict。
  - Raven agent dict -> RavenRuntimeMetadata。

RavenSystem/microservices/agent/engine_adapter/media.py
  - 调用 MessageService.build_user_content(...)。
  - 输出当前 user message content。

RavenSystem/microservices/agent/engine_adapter/llm_provider.py
  - 实现 RavenLLMHubProvider。
  - 内部调用 microservices.llm_hub.client.LLMInternalClient。

RavenSystem/microservices/agent/engine_adapter/tool_bridge.py
  - 实现 execute_raven_tool(...)。
  - 调用 microservices.agent.systems.tools.executor.execute_tool。
  - Raven ToolCallResult -> Axc ToolOutput。

RavenSystem/microservices/agent/engine_adapter/tool_plugin.py
  - 实现 RavenToolPlugin(BasePlugin)。
  - 注册 Raven tools、tool_search、循环保护、deferred 激活。

RavenSystem/microservices/agent/engine_adapter/event_bridge.py
  - Axc Event -> Raven WebSocket event。
  - 写 MessageService、TraceService、ExecLogService。

RavenSystem/microservices/agent/engine_adapter/approvals.py
  - 管理 approval_queue。
  - 提供 submit_approval / submit_human_input。

RavenSystem/microservices/agent/engine_adapter/persistence.py
  - 实现 MessagePersistence、CheckpointStore、AuditSink、SpanStore、ResultStore。

RavenSystem/microservices/agent/tests/test_engine_adapter_llm_provider.py
RavenSystem/microservices/agent/tests/test_engine_adapter_tool_plugin.py
RavenSystem/microservices/agent/tests/test_engine_adapter_event_bridge.py
RavenSystem/microservices/agent/tests/test_engine_adapter_config_translator.py
RavenSystem/microservices/agent/tests/test_chat_orchestrator_engine_adapter.py
```

### 修改文件

```text
RavenSystem/microservices/agent/services/chat_orchestrator.py
  - _execute(...) 从旧 AgentExecutor 改为 RavenEngineAdapter。
  - 保留 ExecLog/Trace 创建时机；如果迁移进 EventBridge，删除本函数里的重复 Trace 写入，避免重复。
  - run_request 必须传 execution_context=self.ctx。

RavenSystem/microservices/agent/routers/chat_router.py
  - tool_approval_response 分支改为 await executor.submit_approval(msg)。
  - 如新增 human_input_response 分支，调用 await executor.submit_human_input(msg)。
  - stop 分支继续调用 executor.cancel()。

RavenSystem/microservices/agent/executor/agent_executor.py
  - 改为 deprecated wrapper。
  - run() 抛 RuntimeError。
  - 保留 cancel/set_messages/set_tools，防止旧引用 import 崩。

RavenSystem/microservices/agent/executor/context.py
  - 不删除字段。
  - 允许 adapter 继续写 total_input_tokens、total_output_tokens、current_round、error、approval_queue。

RavenSystem/microservices/agent/executor/context_builder.py
  - 保持 active_systems 注入逻辑。
  - 不迁移 memory/knowledge/graph/skill 等系统。

RavenSystem/microservices/agent/models.py
  - 新增 AgentEngineCheckpoint、AgentEngineArtifact 两张表，见附录 F。

RavenSystem/microservices/agent/services/execution_state_service.py
  - 新增 run_id 读写方法，见附录 F。

RavenSystem/microservices/agent/services/message_service.py
  - 不改公开协议。
  - 如 build_user_content 不能独立处理 adapter 调用，补单测后最小修复。

RavenSystem/microservices/agent/services/trace_service.py
RavenSystem/microservices/agent/services/log_service.py
  - 优先不改；EventBridge 直接调用现有方法。
  - 只有方法签名无法满足时才加小方法。
```

## 附录 B：RavenEngineAdapter.run 完整伪代码

执行者按此结构实现，不要拆成新的框架。

```python
class RavenEngineAdapter:
    def __init__(self, request, db_session_factory, send_ws):
        self.request = request
        self.db_session_factory = db_session_factory
        self.send_ws = send_ws
        self._cancelled = False
        self._engine = None
        self._approval_queue = asyncio.Queue()
        if request.execution_context is not None:
            request.execution_context.approval_queue = self._approval_queue

    def cancel(self):
        self._cancelled = True
        if self.request.execution_context is not None:
            self.request.execution_context.cancelled = True

    async def submit_approval(self, payload):
        await self._approval_queue.put(payload)

    async def submit_human_input(self, payload):
        await self._approval_queue.put(payload)

    async def run(self):
        db = self.db_session_factory()
        tmp_path = None
        bridge = None
        try:
            metadata = build_raven_runtime_metadata(self.request)
            config_dict = build_axc_agent_yaml_dict(self.request, metadata)
            tmp_path = write_temp_yaml(config_dict)

            bridge = RavenEventBridge(
                request=self.request,
                db_session_factory=self.db_session_factory,
                send_ws=self.send_ws,
            )
            await bridge.start_run()

            provider = RavenLLMHubProvider(
                model_ref_id=self.request.model_ref_id,
                fallback_model_ref_id=self.request.agent.get("fallback_model_ref_id", 0) or 0,
                utility_model_ref_id=self.request.agent.get("utility_model_ref_id", 0) or 0,
                timeout=settings.LLM_TIMEOUT,
                thinking=self.request.agent.get("thinking", "auto"),
                emit_internal=bridge.emit_internal,
            )

            persistence = RavenPersistenceBundle(
                db_session_factory=self.db_session_factory,
                conversation_id=self.request.conversation_id,
                agent_config_id=self.request.agent_config_id,
            )

            tool_runtime = RavenToolRuntime(
                metadata=metadata,
                event_bridge=bridge,
                approval_queue=self._approval_queue,
                execution_context=self.request.execution_context,
                tools_config=self.request.tools_config,
            )
            plugin_registry = PluginRegistry()
            plugin_registry.register_factory("raven_tools", lambda: RavenToolPlugin(tool_runtime))

            self._engine = Engine(
                message_persistence=persistence.message_persistence,
                span_store=persistence.span_store,
                result_store=persistence.result_store,
                audit_sink=persistence.audit_sink,
                checkpoint_store=persistence.checkpoint_store,
                plugin_registry=plugin_registry,
            )
            agent = self._engine.load_agent_template(str(tmp_path)).instantiate(
                models=AgentModels(default=provider),
            )

            user_content = build_current_user_content(
                self.request.content,
                self.request.attachments,
                self.request.agent_config_id,
            )
            messages = list(self.request.messages)
            messages.append({"role": "user", "content": user_content})

            final_content = ""
            error = ""
            async for event in agent.stream_with_messages(
                messages,
                session_id=str(self.request.conversation_id),
                llm_options={"stream": self.request.stream},
            ):
                if self._cancelled:
                    break
                await bridge.handle(event)
                if event.type.value == "done":
                    final_content = event.content
                elif event.type.value == "error":
                    error = event.content

            ctx = self.request.execution_context
            result = RavenEngineRunResult(
                success=not bool(error) and not self._cancelled,
                content=final_content,
                error="已停止" if self._cancelled else error,
                total_input_tokens=getattr(ctx, "total_input_tokens", 0),
                total_output_tokens=getattr(ctx, "total_output_tokens", 0),
                rounds=getattr(ctx, "current_round", 0),
                run_id=bridge.run_id,
            )
            await bridge.end_run(result)
            return result
        except Exception as e:
            if bridge:
                await bridge.fail_run(e)
            return RavenEngineRunResult(success=False, error=str(e))
        finally:
            if self._engine:
                await self._engine.close()
            if tmp_path:
                Path(tmp_path).unlink(missing_ok=True)
            db.close()
```

## 附录 C：RavenLLMHubProvider 伪代码

`RavenLLMHubProvider` 不使用旧 `LLMHubChatModel`，但可复制其中的解析逻辑。

```python
class RavenLLMHubProvider:
    @property
    def model(self):
        return f"raven-model-ref:{self.model_ref_id}"

    @property
    def tool_name_mapping(self):
        return None

    async def chat(self, messages, tools=None, **kwargs):
        await self._emit_prompt_snapshot(messages)
        data = await asyncio.to_thread(self._call_sync, messages, tools, kwargs, self.model_ref_id)
        return self._to_llm_response(data)

    async def stream(self, messages, tools=None, **kwargs):
        await self._emit_prompt_snapshot(messages)
        try:
            async for chunk in self._stream_with_model(self.model_ref_id, messages, tools, kwargs):
                yield chunk
        except Exception as e:
            if not self._should_fallback(e):
                raise
            await self.emit_internal("model_fallback", {
                "original_model_ref_id": self.model_ref_id,
                "fallback_model_ref_id": self.fallback_model_ref_id,
                "error": str(e)[:200],
            })
            async for chunk in self._stream_with_model(self.fallback_model_ref_id, messages, tools, kwargs):
                yield chunk

    async def ask(self, prompt, **kwargs):
        resp = await self.chat([{"role": "user", "content": prompt}], tools=None, **kwargs)
        return resp.message.content

    async def close(self):
        return None
```

同步调用：

```python
def _call_sync(self, messages, tools, kwargs, model_ref_id):
    client = LLMInternalClient(service_name="agent")
    call_kwargs = {
        "model_ref_id": model_ref_id,
        "temperature": kwargs.get("temperature", self.temperature),
        "timeout": self.timeout,
    }
    if tools:
        call_kwargs["tools"] = tools
    if "parallel_tool_calls" in kwargs:
        call_kwargs["parallel_tool_calls"] = kwargs["parallel_tool_calls"]
    return client.chat("", messages, **call_kwargs)
```

`_to_llm_response(data)` 映射：

```text
data["choices"][0]["message"]["content"] -> LLMMessage.content
data["choices"][0]["message"]["tool_calls"] -> LLMMessage.tool_calls
data["usage"]["prompt_tokens"] or input_tokens -> LLMUsage.input_tokens
data["usage"]["completion_tokens"] or output_tokens -> LLMUsage.output_tokens
data["usage"]["cached_tokens"] -> LLMUsage.cached_tokens
```

流式调用必须优先复用旧 `LLMHubChatModel._astream_impl(...)` 里的 LLMInternalClient/Claude native 事件解析代码，但输出类型改成 `LLMStreamChunk`：

```text
文本 delta -> LLMStreamChunk(content_delta=...)
thinking delta -> LLMStreamChunk(thinking_delta=...)
tool args delta -> LLMStreamChunk(tool_call_delta={"index": i, "id": id, "function": {"name": name, "arguments": delta}})
usage -> LLMStreamChunk(usage=LLMUsage(...))
结束 -> StopAsyncIteration，不额外 yield done
```

## 附录 D：RavenToolPlugin 和 deferred 伪代码

```python
class RavenToolPlugin(BasePlugin):
    name = "raven_tools"

    def __init__(self, runtime):
        self.runtime = runtime
        self._tools_by_name = {}
        self._deferred_by_name = {}
        self._active_deferred = set()

    def initialize(self, config, plugin_ctx):
        super().initialize(config, plugin_ctx)

    def get_tools(self):
        result = []
        for tc in self.runtime.tools_config:
            if tc.get("type") == "unknown":
                continue
            name = (tc.get("name") or "").replace(":", "_")
            if not name or name in self._tools_by_name:
                continue
            tc = dict(tc)
            tc["name"] = name
            tool_def = ToolDefinition(
                name=name,
                description=tc.get("description", ""),
                parameters=tc.get("parameters") or {"type": "object", "properties": {}},
                execute=self._make_execute(tc),
                is_read_only=bool(tc.get("is_read_only", False)),
                timeout=int(tc.get("timeout", 120) or 120),
                deferred=bool(tc.get("defer", False)),
                risk_level=tc.get("risk_level", "safe"),
            )
            self._tools_by_name[name] = tool_def
            if tool_def.deferred:
                self._deferred_by_name[name] = tool_def
            result.append(tool_def)
        result.append(self._make_tool_search())
        return result

    def pre_llm_call(self, exec_ctx, messages, tools):
        tools = list(tools or [])
        for name in sorted(self._active_deferred):
            tool_def = self._deferred_by_name.get(name)
            if tool_def:
                schema = tool_def.to_openai_schema()
                if schema not in tools:
                    tools.append(schema)
        return messages, tools

    async def post_tool_call(self, exec_ctx, tool_name, arguments, result, duration_ms):
        self._active_deferred.discard(tool_name)
        return result
```

`tool_search`：

```python
def _make_tool_search(self):
    async def _execute(args, context):
        from microservices.agent.systems.tools.providers.builtin.tool_search import execute_tool_search
        matches = execute_tool_search(args, {"deferred_tools": list(self.runtime.deferred_tools)})
        for item in matches.get("matched_tools", []):
            name = item.get("name", "").replace(":", "_")
            if name in self._deferred_by_name:
                self._active_deferred.add(name)
        return ToolOutput.json_output(matches)
    return ToolDefinition(
        name="tool_search",
        description="搜索可用工具。当你需要某个功能但当前工具列表中没有时，用此工具搜索。搜索到工具后可直接按名称调用",
        parameters={"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
        execute=_execute,
        is_read_only=True,
        risk_level="safe",
    )
```

循环保护：

```python
def _check_loop_guard(self, tool_name, arguments):
    rt = self.runtime
    sig = f"{tool_name}:{md5(json.dumps(arguments, sort_keys=True, ensure_ascii=False).encode()).hexdigest()}"
    rt.tool_total_counter[tool_name] = rt.tool_total_counter.get(tool_name, 0) + 1
    if rt.tool_total_counter[tool_name] >= rt.max_tool_total_calls:
        return f"执行异常：工具 {tool_name} 累计调用{rt.tool_total_counter[tool_name]}次，已强制停止。请换个方式提问。"
    if sig == rt.last_tool_signature:
        rt.same_args_repeat_count += 1
        if rt.same_args_repeat_count >= rt.max_tool_repeat:
            return f"执行异常：工具 {tool_name} 以相同参数陷入循环调用，已强制停止。请检查参数或换个方式提问。"
    else:
        rt.last_tool_signature = sig
        rt.same_args_repeat_count = 1
    if tool_name == rt.last_tool_name:
        rt.same_name_repeat_count += 1
        if rt.same_name_repeat_count >= rt.max_tool_name_repeat:
            return f"执行异常：工具 {tool_name} 连续调用{rt.same_name_repeat_count}次，已强制停止。请换个方式提问。"
    else:
        rt.last_tool_name = tool_name
        rt.same_name_repeat_count = 1
    return ""
```

## 附录 E：approval / ask_human payload 协议

`RavenEngineAdapter` 必须只维护一个 `asyncio.Queue`，同时承载工具审批和人工输入。

前端工具审批回复：

```json
{
  "type": "tool_approval_response",
  "conversation_id": 123,
  "request_id": "approval-request-id",
  "approved": true
}
```

前端人工输入回复，如果没有现成类型，就复用：

```json
{
  "type": "tool_approval_response",
  "conversation_id": 123,
  "request_id": "human-request-id",
  "content": "用户回复内容"
}
```

adapter 规则：

```python
async def submit_approval(self, payload):
    await self._approval_queue.put(payload)

async def submit_human_input(self, payload):
    await self._approval_queue.put(payload)
```

dangerous 工具读取：

```python
payload = await asyncio.wait_for(queue.get(), timeout=settings.TOOL_APPROVAL_TIMEOUT)
approved = bool(payload.get("approved", False))
```

`ask_human` 读取：

```python
payload = await asyncio.wait_for(queue.get(), timeout=300)
user_response = payload.get("content") or payload.get("response") or ""
```

## 附录 F：DB 和持久化落点

当前 `RavenSystem/microservices/agent/models.py` 没有 engine checkpoint/artifact 表。必须在该文件新增：

```python
class AgentEngineCheckpoint(Base, FastApiBaseModelMixin):
    __tablename__ = "agent_engine_checkpoints"
    conversation_id = Column(Integer, nullable=False, index=True)
    run_id = Column(String(64), nullable=False, index=True)
    sequence = Column(Integer, default=0)
    status = Column(String(20), default="running", index=True)
    kind = Column(String(30), default="round", index=True)
    state_json = Column(Text, default="{}")
    metadata_json = Column(Text, default="{}")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class AgentEngineArtifact(Base, FastApiBaseModelMixin):
    __tablename__ = "agent_engine_artifacts"
    conversation_id = Column(Integer, nullable=False, index=True)
    artifact_id = Column(String(64), nullable=False, unique=True, index=True)
    content_type = Column(String(30), default="text")
    content = Column(Text, default="")
    metadata_json = Column(Text, default="{}")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
```

`ExecutionStateService` 必须新增：

```python
@classmethod
def set_run_id(cls, db, conversation_id: int, run_id: str):
    state = cls.get_or_create(db, conversation_id)
    current = json.loads(state.current_step or "{}")
    current["run_id"] = run_id
    state.current_step = json.dumps(current, ensure_ascii=False)
    db.commit()

@classmethod
def get_latest_run_id(cls, db, conversation_id: int) -> str:
    state = cls.get_state(db, conversation_id) or {}
    current = state.get("current_step", {}) or {}
    return str(current.get("run_id") or "")
```

`CheckpointStore.save(checkpoint)` 写入 `AgentEngineCheckpoint`：

```text
conversation_id = request.conversation_id
run_id = checkpoint.run_id
sequence = checkpoint.sequence
status = str(checkpoint.status)
kind = checkpoint.kind
state_json = json.dumps(checkpoint.state, ensure_ascii=False, default=str)
metadata_json = json.dumps(checkpoint.metadata, ensure_ascii=False, default=str)
```

`CheckpointStore.latest(run_id)` 按 `run_id` 和最大 `sequence` 读取，并还原为 `axc_agent_engine.runtime.checkpoint.Checkpoint`。

`ResultStore.put(content, metadata)` 写入 `AgentEngineArtifact`，返回 `ArtifactRef(id=artifact_id, kind=content_type, size=len(content), metadata=metadata)`。

## 附录 G：EventBridge 伪代码

```python
class RavenEventBridge:
    async def start_run(self):
        self.exec_log = ExecLogService.create_log(...)
        self.root_trace_id = TraceService.start_trace(...)

    async def handle(self, event):
        data = {"conversation_id": self.request.conversation_id}
        if event.type == EventType.STREAM_DELTA:
            data["content"] = event.content
            await self._send("stream_delta", data)
        elif event.type == EventType.TOOL_CALL:
            await self._send("tool_call", {...})
            MessageService.add_message(... role="tool_call" ...)
            self.pending_tool_traces[event.tool_call_id] = TraceService.start_trace(...)
        elif event.type == EventType.TOOL_RESULT:
            await self._send("tool_result", {...})
            MessageService.add_message(... role="tool_result" ...)
            TraceService.end_trace(...)
        elif event.type == EventType.ERROR:
            await self._send("error", {"message": event.content, "conversation_id": ...})
        elif event.type == EventType.DONE:
            self.final_content = event.content
```

`end_run(result)` 必须：

- `TraceService.end_trace(root_trace_id, status="completed" | "failed", input_tokens=..., output_tokens=...)`
- `ExecLogService.update_result(...)`
- `ExecutionStateService.set_run_id(db, conversation_id, result.run_id)`
- 不发送 `chat_complete`，仍由 `ChatOrchestrator._post_process(...)` 发送。

## 附录 H：无上下文执行验收断言

执行者完成后必须逐条确认：

```bash
cd /Users/zhaoxin/Desktop/AxcRavenSystem

rg "executor\\.ctx\\.approval_queue" RavenSystem/microservices/agent/routers
# 期望无结果

rg "AgentExecutor\\(" RavenSystem/microservices/agent/services RavenSystem/microservices/agent/routers
# 期望无结果

rg "LLMHubChatModel" RavenSystem/microservices/agent/services RavenSystem/microservices/agent/engine_adapter
# 期望无结果

rg "class AgentEngineCheckpoint|class AgentEngineArtifact" RavenSystem/microservices/agent/models.py
# 期望两者都存在

rg "set_run_id|get_latest_run_id" RavenSystem/microservices/agent/services/execution_state_service.py
# 期望两者都存在

rg "class RavenEngineAdapter|class RavenLLMHubProvider|class RavenToolPlugin|class RavenEventBridge" RavenSystem/microservices/agent/engine_adapter
# 期望四者都存在
```
