# Plugin Development

插件是可选行为的扩展边界。插件可以注入上下文、转换消息、观察 LLM 调用、定义工具、执行策略或后处理最终结果。
Plugins are the extension boundary for optional behavior. A plugin may inject context, transform messages, observe LLM calls, define tools, enforce policy, or post-process final results.

## Registration

插件注册必须由宿主代码显式完成。`Engine` 默认的 `plugin_registry` 为空，Agent YAML 只能启用已经注册过的插件。
Plugin registration must be explicit host code. The default `Engine` `plugin_registry` is empty, and Agent YAML can only enable plugins that were already registered.

```python
from axc_agent_engine import AgentModels, Engine, PluginRegistry
from axc_agent_engine.plugins.builtin import BuiltinToolsPlugin
from my_project.plugins import MyPlugin

registry = PluginRegistry()
registry.register(BuiltinToolsPlugin)
registry.register(MyPlugin)

engine = Engine(plugin_registry=registry)
template = engine.load_agent_template("./agents/my_agent.yaml")
agent = template.instantiate(models=AgentModels(default=llm))
```

YAML 只负责选择和配置插件，不负责 import 代码。
YAML only selects and configures plugins; it does not import code.

```yaml
plugins:
  my_plugin:
    enabled: true
    api_url: "http://localhost:5000"
```

## Lifecycle

宿主先校验所有已配置插件的 schema 和启用插件的依赖图。依赖先初始化，同批可运行插件按 phase、priority 排序。插件默认关闭，需要显式声明 `enabled: true`。构造函数只创建本地状态，资源在 `initialize` 中取得，`close` 应能处理部分初始化。

The loader validates configuration, missing dependencies, and cycles before the first initializer runs. A failure during initialization, strategy selection, or tool binding closes started plugins in reverse order, including the plugin whose initializer failed. Normal shutdown uses the same order. For immediate retries inside an event loop, use `await template.instantiate_async(models=...)` to wait for rollback. The synchronous entry point schedules rollback on the current loop when one is running. `await engine.close()` joins outstanding cleanup.

```text
initialize(config, plugin_ctx)
on_execution_start(exec_ctx)
inject_context(exec_ctx)
transform_messages(messages, exec_ctx, current_message)
pre_llm_call(exec_ctx, messages, tools)
post_llm_call(exec_ctx, messages, response, duration_ms)
pre_tool_call(exec_ctx, tool_name, arguments)
post_tool_call(exec_ctx, tool_name, arguments, output, duration_ms)
on_tool_call_failed(exec_ctx, tool_name, arguments, error, duration_ms)
on_round_end(exec_ctx, user_message, assistant_message, tool_calls)
should_stop(exec_ctx)
on_execution_complete(exec_ctx, result, trace)
on_execution_end(exec_ctx, result, error)
close()
```

同步 hook 必须保持轻量，不能做阻塞 I/O。
Synchronous hooks must stay lightweight and must not perform blocking I/O.

工具 hook 可以返回 `(allowed, arguments)`，也可以从 `axc_agent_engine.plugins.base` 导入 `PreToolCallDecision`，随本次拒绝返回原因、错误码和详情。`allowed` 必须是布尔值。消息转换 hook 返回消息列表，停止 hook 返回布尔标志和原因。
Tool hooks return a boolean and an argument dictionary, or a `PreToolCallDecision` carrying the reason, code and details for that call. Message transforms return a list. Stop hooks return a boolean flag and a reason.

禁用的插件由加载器排除。预置插件的 hook 只处理已启用的运行实例。跟踪插件从本次工具调用读取 ID，审批响应的 `approved` 必须是布尔值。
The loader excludes disabled plugins. Tool tracing uses the ID assigned by the executor. Hosts must return a boolean `approved` in approval responses.

## Configuration Schema

可选字段省略时使用默认值。显式值按声明的类型校验，例如 `scope_keys: [tenant_id, user_id]`。逗号分隔字符串和显式 null 都不能充当数组。指定 `compress.boundary.resource` 时，宿主必须挂载符合 `CompressionBoundaryStore` 接口的资源。
Omitting an optional field selects its default. Explicit values must match the declared type. An explicit compression boundary resource must implement `CompressionBoundaryStore`.

插件必须声明 `config_schema`。未声明 schema 的插件不能注册，也不能通过 `AgentTemplate.instantiate()` 加载。
Plugins must declare `config_schema`. Plugins without a schema cannot be registered or loaded through `AgentTemplate.instantiate()`.

```python
from axc_agent_engine import BasePlugin
from axc_agent_engine.plugins.config_schema import config_field, config_schema

class MyPlugin(BasePlugin):
    name = "my_plugin"
    display_name = "我的插件"
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
        display_name_en="My plugin",
    )
```

schema 字段支持 `string`、`integer`、`number`、`boolean`、`object` 和 `array`，并可声明默认值、枚举、必填、嵌套对象、数组元素和 advanced 元数据。
Schema fields support `string`, `integer`, `number`, `boolean`, `object`, and `array`, plus defaults, enums, required flags, nested objects, array items, and advanced metadata.

宿主可以从注册表读取 schema，用于前端详情页、模板生成、默认值展示和可选校验。
Hosts can read schemas from the registry for detail pages, template generation, default displays, and optional validation.

```python
schemas = registry.list_plugin_config_schemas()
compress_schema = registry.get_plugin_config_schema("compress")
```

注册表会自动补充通用字段 `enabled`。启用的插件必须能注册、初始化并满足依赖，否则 Agent 加载直接失败；加载器会在初始化之前拒绝未声明的 YAML 字段。
The registry automatically adds the shared `enabled` field. Enabled plugins must be registered, initialize successfully, and have all dependencies loaded; otherwise Agent loading fails. The loader rejects undeclared YAML fields before initialization.

## Tool Definitions

工具必须返回 `ToolOutput`。非 `ToolOutput` 返回会被视为契约错误。
Tools must return `ToolOutput`. Non-`ToolOutput` returns are treated as contract errors.

每个工具应定义 `name`、`description`、JSON-schema-like `parameters`、`is_read_only`、`capability`、`risk_level` 和 `timeout`。
Every tool should define `name`, `description`, JSON-schema-like `parameters`, `is_read_only`, `capability`, `risk_level`, and `timeout`.

高风险工具必须使用非空 capability，让宿主可以显式允许或拒绝。
High-risk tools must use a non-empty capability so hosts can explicitly allow or deny them.

`ToolOutput.llm_view` 是给 LLM 理解工具结果的完整文本视图，不是 token 节省层。列表类工具必须输出完整条目标识，例如路径、名称或 id；内容类工具默认输出完整内容，片段读取必须由显式参数表达。工具实现禁止默认字符级截断、省略、只返回计数，或把 `summary` 当成唯一 LLM 内容。超大结果必须完整写入 `ArtifactStore` artifact，并在 `llm_view` 中给出 `artifact_id` 和后续读取方式。
`ToolOutput.llm_view` is the complete text view for LLM reasoning, not a token-saving layer. List tools must include full item identifiers such as paths, names, or ids; content tools return full content by default, and partial reads must be explicit parameters. Tool implementations must not default to character truncation, omission, count-only views, or `summary` as the only LLM content. Oversized results must be written in full to an `ArtifactStore` artifact, with the `artifact_id` and `artifact_read`/`artifact_page` follow-up path in `llm_view`.

每个官方工具的 `llm_view` 生成逻辑应有对应单测，避免结构化 `content` 已包含信息但 LLM 视图丢失关键信息。
Every official tool `llm_view` generator should have matching tests so structured `content` cannot contain information that the LLM view drops.

## Failure Policy

插件加载、依赖和 hook 异常都会直接抛出。不要在插件层吞掉配置错误或致命运行错误；需要可选能力时，应让宿主显式禁用插件或不配置对应能力。
Plugin load, dependency, and hook errors are propagated directly. Do not swallow configuration errors or fatal runtime errors in plugin infrastructure; hosts should explicitly disable optional plugins or omit optional capabilities.

## State

插件实例是 Agent 级作用域。单次运行状态应放在 `exec_ctx.get_plugin_state(plugin_name)`，或放在按 tenant/user/agent/session 分区的后端存储中。
Plugin instances are Agent-scoped. Per-run state should be stored in `exec_ctx.get_plugin_state(plugin_name)` or in a backing store keyed by tenant, user, agent, and session.

避免把“当前请求”状态保存在插件实例字段上。
Avoid keeping "current request" state on the plugin instance itself.

## Example

参考 `examples/05_custom_plugin`。
See `examples/05_custom_plugin`.

## Execution strategies

官方执行策略 `react` 与 `por` 随引擎分发。显式使用时，宿主注册插件类，Agent YAML 启用插件。
仅启用一个策略时自动选中；启用多个时通过 `runtime.strategy` 指定入口。POR 的子步骤复用 ReAct 实现，无需另启顶层 ReAct 插件。
显式选择策略后，路由由该插件决定；只有未选择任何策略的旧模板才按 `runtime.routing.mode` 装配兼容的官方循环。

宿主从 `axc_agent_engine.plugins.builtin` 导入官方策略。模板实例化后可以用 `agent.inspect()["strategy"]` 查看实际选中的策略、选择原因和配置。

自定义策略继承 `ExecutionStrategyPlugin`，声明配置 schema，实现 `create_loop(runtime)`。
每次请求创建一个循环实例。其 `run(message, state)` 是异步生成器，必须产出终止事件。
恢复时引擎先核对策略名，再还原消息和用量，并把上次保存的策略状态传给新实例。

An execution strategy supplies the loop for one Agent run. Hosts register
the official strategies through the same registry used for hook plugins. Import the bundled drivers from
`axc_agent_engine.plugins.builtin`. The host selects a driver at agent binding time.
Use `agent.inspect()["strategy"]` to see the selected name, selection reason, and effective settings.

```python
from axc_agent_engine import Engine, PluginRegistry
from axc_agent_engine.plugins.builtin import PORPlugin, ReActPlugin

registry = PluginRegistry()
registry.register_many([ReActPlugin, PORPlugin])
engine = Engine(plugin_registry=registry)
```

```yaml
name: planner
runtime:
  strategy: por
plugins:
  react: {enabled: true}
  por: {enabled: true, mode: auto}
```

`react` runs the tool loop. `por` defaults to `por_first` and uses bounded
ReAct steps internally. Its `auto` mode starts with ReAct and hands a
structured plan to POR. Only one top-level loop runs. A single enabled
strategy is selected automatically. With several enabled, set
`runtime.strategy` to an enabled name. Existing templates with no strategy
plugin use the official implementations selected by `runtime.routing.mode`.

Subclass `ExecutionStrategyPlugin` from `axc_agent_engine.plugins.strategy`
and declare a name and config schema. Implement `create_loop(runtime)` to
return a fresh object for every request. Its `run(message, state)` async
generator yields engine events and finishes with DONE, ERROR, or CANCELLED.
The executor closes the generator at its first terminal event and owns the
execution hooks, usage accounting in DONE, and the final checkpoint.
`plugins.por.max_parallel_steps` 默认为 4，设为 1 可串行执行。每批只派发依赖已满足的步骤。各步骤共用本次运行的轮次预算。需要依次修改同一文件的步骤应声明 `depends_on`。已派发的步骤结束后，POR 会先处理待投递的指令，再派发下一批。

### StrategyRuntime

| Public operation | Behavior |
| --- | --- |
| `begin_round()` | Check cancellation, round count and elapsed time, then increment the round number. Call this once per round. |
| `check_cancelled()` | Check for cancellation between steps of a long-running round. |
| `history()`, `append_message(message)` | Read an independent history snapshot and append a message to the run. |
| `call_model(user_message, tools=None)` | Apply message transforms and model hooks. Forward each `Event`, then consume the final `ModelReply.message`. Use `async with aclosing(...)` when forwarding events. |
| `tool_schemas()`, `await execute_tools(calls)` | Obtain visible schemas and execute normalized calls through the host policy and audit sink. Each call supplies `id`, `name`, and an `arguments` dictionary. |
| `append_tool_results(results)`, `await end_round(user_message, reply, calls)` | Record tool messages and dispatch round-end hooks. |
| `await save_state(kind, state)` | Save a serializable dictionary of plugin progress alongside engine-owned messages, cursor and usage. |

每个请求独享一个循环实例。模型输出、工具权限与审计均经过上述接口。循环转发事件时应在 `finally` 或 `aclosing` 中释放自己持有的异步资源。调用方在收到首个终止事件时即可读取已写回的会话。

### Checkpoint migration

`state_version` is a positive integer on the plugin class, initially 1. Unversioned snapshots are read as v1. A change of version invokes `migrate_state` with a detached copy before creating the loop, running execution hooks, or calling tools. The default implementation rejects other versions. A custom migration returns a dictionary or `None`. A successful resume writes subsequent checkpoints with the current version.

```python
# Methods on a strategy whose v1 payload used "position" for the next step.
state_version = 2

def migrate_state(self, state, from_version):
    if from_version == 1:
        return None if state is None else {"next_step": state["position"]}
    return super().migrate_state(state, from_version)
```

插件名标识检查点的归属，`state_version` 标识该插件保存的数据格式。迁移函数只转换自己的状态。宿主可用同一持久化存储的旧快照验证升级，旧版无策略名的轮次快照只能由官方循环恢复，旧版 POR 快照归属 POR。

### Testing a host strategy

Import the following checks from `axc_agent_engine.plugins.testing` in your own async tests. Use a deterministic provider and expected replies.

```python
await assert_isolated_runs(agent, {"first": "first", "second": "second"})
await assert_stream_contract(agent.resume_stream(saved_run_id), content="restored")
await assert_cancellable_run(engine, agent, "block", ready)
await assert_stream_closes(agent.stream("partial"), released)
assert_tool_denied(await audit_sink.list_events(), "restricted-call")
```

The cancellation fixture sets an `asyncio.Event` called `ready` while blocked. The close fixture produces a nonterminal event and sets `released` in its cleanup. For the permission check, configure a tool with a capability outside the allowlist, invoke it through `execute_tools` and also assert its implementation was never entered. `assert_stream_contract` accepts `terminal=EventType.ERROR` or `EventType.CANCELLED` for other expected outcomes.

从仓库根目录运行离线示例验收 `python -m pytest -q tests/engine/test_example_strategies.py tests/engine/test_strategy_plugins.py tests/engine/test_strategy_state_versions.py`。用例覆盖同一 YAML 切换循环、并发隔离、取消、关闭、工具拒绝和跨进程恢复。`chat()` 和 `resume()` 在取消时抛出 `CancelledError`，流式入口产出 `CANCELLED`。

可运行的第三方循环见 [自定义循环示例](../examples/08_custom_loop)。在其 YAML 中切换 `runtime.strategy`，即可用同一宿主运行官方 POR 或自定义单轮策略。

The [custom loop example](../examples/08_custom_loop) registers both strategies and selects the active loop in YAML.

## Active input and durable execution

Call `await agent.send_message(run_id, text, message_id=key, session_id=session)` from the host. Use a fresh run ID for each new run and the original ID for resume. Use an empty session ID for anonymous runs. The host supplies authentication and routes requests to the engine instance that owns the active run.

| Receipt | Meaning and host action |
| --- | --- |
| `accepted` | Queued for this run. Retrying the same ID and text preserves its place. |
| `processed` | Already appended to user history. The model or a downstream tool may still be working. |
| `full` | 128 messages are pending. Retry the same ID later. |
| `unprocessed` | The saved run still contains this ID as pending. Resume an interrupted run to deliver it. |
| `closed` | No matching active recipient or recognized stored receipt. A new instruction requires a new run. |

IDs and text must be nonblank. Reusing an ID with different text raises `ValueError` when the receipt is available. Terminal events carry ordered `processed_message_ids` and `unprocessed_message_ids` when nonempty. Without a checkpoint store, receipts expire when the run leaves memory. Cancellation closes admission immediately. Retrying a known ID still reports its receipt during cleanup. Installing an in-memory store extends lookup within that process. Cross-process recovery requires a host store that atomically saves each checkpoint and returns the latest committed sequence.

Official ReAct and `StrategyRuntime.call_model()` admit queued input before assembling the next request. Custom loops deciding work without that helper can `await runtime.receive_messages()` to consume the pending text once. A final answer and a concurrent send have an order. If input wins, the engine continues the same history with a fresh loop state. If completion wins, admission closes before the completion hook. POR keeps completed steps and admits new instructions with the revised plan in one checkpoint. Steps already dispatched can finish.

Starting or resuming a run reserves its ID before waiting for a session slot or reading a checkpoint. A second attempt with the same ID raises `ValueError`. A persisted checkpoint replaced while the request waits for a slot also requires a fresh resume request. Competing engine processes still require coordination by the host.

宿主通过 `run_options` 指定本次运行的 `run_id`、布尔值 `stream`、正整数 `stream_idle_timeout` 和交互队列 `approval_queue`、`response_queue`。轮次上限在 Agent 的 `runtime.max_rounds` 配置中设置。恢复时沿用快照中的已用轮次；存储中的轮次和 token 计数必须是非负整数。
Hosts may pass `run_id`, `stream`, `stream_idle_timeout`, `approval_queue` and `response_queue` as per-run options. Configure the round budget through `runtime.max_rounds`. Resume uses the stored round and token counts, which must be nonnegative integers.

`resume(run_id, message=text)` queues one new instruction behind inputs already saved for that run. It receives an engine-generated message ID in the terminal receipts. A full inbox must first be resumed without extra text. The default loop receives the restored batch as its `message` argument with each instruction already present once in history. A strategy that consumes restored input at its own decision boundary can set `defer_resume_input = True`. With saved strategy state, the input then stays queued for that strategy's decision boundary. The bundled POR loop uses this mode to revise the remaining plan before dispatching its next step.

恢复时的补充输入排在原有待处理消息之后。第三方循环默认从 `message` 和历史中读取；同一运行 ID 被占用期间，重复启动或恢复会立即收到错误。

After a custom loop incorporates completed tool results, `save_state(kind, state)` marks that progress durable. Resume must continue from the supplied state without repeating work that it represents.

工具作者应准确声明 `is_read_only`。启用检查点后，引擎在调用写工具前保存调用 ID，随后在保存对应执行进度时记录已知结果。进程丢失且结果未确认时，恢复在调用工具前报错。宿主根据错误中的 ID 核对外部系统，决定后续处理。工具可以把 `context["tool_call_id"]` 用作外部服务的幂等键。取消不会撤回已经产生的外部副作用。插件状态版本与旧快照迁移规则见上文。

已完成的运行在没有待处理输入时，重复调用 `resume(run_id)` 直接返回已保存的答复，`resume_stream` 只发出一次携带用量的 DONE。提供补充输入会从已保存的历史开始新一轮循环。中断快照若只有工具请求、缺少对应结果，恢复会列出缺失的调用 ID。宿主按调用 ID 核对结果，补回缺失记录后可以重试。已有工具结果保持原顺序。POR 检查点内的非法步骤或未知状态会使恢复报错。

POR 在派发前检查已接收的插话。生成修订计划时又收到指令，会先处理新指令，再启动步骤。失败步骤的快照从重规划继续，已保存的重规划直接用于调度。已保存最终摘要时，恢复直接交付摘要。旧快照可从已保存的最后一条助手答复还原摘要；缺少可用摘要时恢复报错。

### Dependencies and migration

- Install `axc-agent-engine[mcp]` for standard MCP stdio, SSE or Streamable HTTP. Remove `use_sdk: false` and update simplified JSON-RPC servers to implement the MCP initialization handshake. The official Python SDK owns transport and protocol handling.
- Install `axc-agent-engine[workflow]` and pass `workflow_runtime_factory=lambda: create_workflow_runtime(prefer_burr=True)` to `Engine` to select Burr. Import the factory from `axc_agent_engine.workflow.factory`. Memory is the default in both installations.
- The knowledge plugin can load local documents or use a host-provided search resource. Remove the former `knowledge` extra from installation commands. Hosts using a separate FAISS index manage its dependency.
- Tool argument validation covers top-level required properties, property types, enums, numeric bounds and string length. Unknown properties pass through. Hosts that require recursive schemas, formats, combinators or additional-property enforcement must validate those constraints in their tools.
- Named compression boundaries belong to an agent and session. Anonymous calls have run-local compression state. External recall requires matching scope metadata. Existing unscoped recall entries need an owner before they can be reused.

### Resetting session state

`await agent.reset_session(session_id)` removes the named session’s messages and calls each plugin’s `on_session_reset(agent_name, session_id)` hook before returning. An empty ID covers all named sessions of that agent. The compression plugin deletes the matching boundary, including its summary, file cache and tool observations. A mounted compression boundary store supplies `delete(agent_name, session_id)` for one session and `clear(agent_name)` for an agent-wide reset. A store error propagates to the caller; cleanup should support retry.

A reset rejects a scope with active or queued session executions. Finish or cancel the affected run and await its terminal event before retrying. New named runs on that agent wait for the local reset to finish. Hosts sharing persistent stores between engine processes coordinate resets across those processes.

Session reset covers conversation history and plugin-owned session state. Host recall indexes and long-term memory have their own retention policies. They may still supply matching entries after a session ID is reused. Hosts requiring erasure must also remove those records and any retained checkpoints or artifacts.

### Memory scope and execution state

The memory plugin shares long-term records according to its configured `namespace` and `scope_keys`. Enable `session_scope` to include the session ID. Each scope loads its records before the first model request and owns its statistics and retrieval index. Keys using `%`, `|`, `=` or `:` inside an identity component now use an escaped representation in a separate KV prefix. Existing ordinary keys remain readable. For older records containing these separators, verify the original owner before copying records to the new scope. The original key alone can represent multiple identities. Parallel POR steps and restored runs inherit the memory scope resolved for their parent run. When an old checkpoint lacks `plugin_scopes.memory`, recovery uses the original scope fields in its metadata. The host must supply any missing identity fields through `agent.resume_stream(run_id, metadata=original_identity)`, using its trusted record for that run. Blank fields or values conflicting with the saved identity stop recovery before hooks, model requests, tool calls or checkpoint writes. With the default memory configuration the external fields are `tenant_id` and `user_id`. A single-tenant installation using only `scope_keys: [agent_name]` needs no external identity. For historical records that intentionally omitted a configured identity dimension, verify the exact original scope before backfilling `plugin_scopes.memory` in storage. A stored scope identifies the memory to restore; the host still authorizes the caller.

### Multi-process ownership

A host sharing a checkpoint store across workers must grant one live executor exclusive ownership of each run, before starting it or reading its checkpoint for resume. Keep ownership until the run stops and its final writes finish. Route live input and cancellation to that worker. Before handing off, stop or fence the previous worker so it cannot dispatch tools or write checkpoints. The engine's run claim is local to one Engine. Two workers can both pass the stale-checkpoint check. For external writes, use the recorded tool call ID for idempotency or reconciliation.

Repetition limits and reflection prompts belong to one execution context. Concurrent sessions, subsequent runs and isolated child steps get independent state. Within a run a reflection produced after a tool round is available to the next model request.

### Forkable checkpoints

A custom loop can call `await runtime.save_state(kind, state, forkable=True)` once all dispatched work has joined,
its results are in history and the state identifies the next action. The keyword accepts a boolean. Other intermediate saves remain recovery-only.
The engine marks a completed execution automatically. ReAct marks completed tool rounds. POR marks a saved plan,
a revision, the last merged result of a batch, and the saved summary. A partial parallel batch stays recovery-only.
An unresolved write blocks forking even if a strategy declares a cut.

插件实例仍为 Agent 级。无需继承状态的插件沿用默认实现。需要继承的会话私有数据由插件实现
`capture_session(ctx)`，同步返回可序列化字典；`restore_session(ctx, state)` 在子分支首次续跑前安装该副本。
安装须可重复执行，以允许存储失败后重试。插件写入 `plugin_scopes` 时，实现
`fork_scope(scope, source, target, state)` 核对来源归属并返回目标作用域。返回值为非空白字符串或 `None`。两个身份字典都包含宿主提供的可信字段。
恢复已有子分支时，来源和目标是同一身份。此时该方法校验并返回已保存的作用域，作用域变更会终止恢复。
使用无法按历史切点重建的会话私有外部资源时，插件应声明 `supports_session_fork = False`，此配置下不发布切点。

The memory plugin snapshots session-private records and rebinds them to the child. Shared memory stays in the
configured scope and can change independently of the checkpoint. Compression captures its historical summary,
file cache and tool context. Its external recall index has no historical export contract, so configuring that index
disables forks. Run-local approval queues, tracing and other per-execution resources start fresh.
See the [offline POR example](../examples/09_live_input/run.py).
