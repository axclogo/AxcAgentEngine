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

## Configuration Schema

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

注册表会自动补充通用字段 `enabled`。启用的插件必须能注册、初始化并满足依赖，否则 Agent 加载直接失败；插件仍会收到 YAML 中的额外自定义 key。
The registry automatically adds the shared `enabled` field. Enabled plugins must be registered, initialize successfully, and have all dependencies loaded; otherwise Agent loading fails. Plugins still receive extra custom YAML keys.

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
