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
                required=True,
            ),
        ],
        display_name_en="My plugin",
    )
```

schema 字段支持 `string`、`integer`、`number`、`boolean`、`object` 和 `array`，并可声明默认值、枚举、必填、嵌套对象、数组元素、advanced、deprecated。  
Schema fields support `string`, `integer`, `number`, `boolean`, `object`, and `array`, plus defaults, enums, required flags, nested objects, array items, advanced, and deprecated metadata.

宿主可以从注册表读取 schema，用于前端详情页、模板生成、默认值展示和可选校验。  
Hosts can read schemas from the registry for detail pages, template generation, default displays, and optional validation.

```python
schemas = registry.list_plugin_config_schemas()
compress_schema = registry.get_plugin_config_schema("compress")
```

注册表会自动补充通用字段 `enabled` 和 `required`。schema 不改变 `initialize(config, plugin_ctx)` 的执行语义；插件仍会收到 YAML 中的额外自定义 key。  
The registry automatically adds the shared `enabled` and `required` fields. The schema does not change `initialize(config, plugin_ctx)` execution semantics; plugins still receive extra custom YAML keys.

## Tool Definitions

工具必须返回 `ToolOutput`。非 `ToolOutput` 返回会被视为契约错误。  
Tools must return `ToolOutput`. Non-`ToolOutput` returns are treated as contract errors.

每个工具应定义 `name`、`description`、JSON-schema-like `parameters`、`is_read_only`、`capability`、`risk_level` 和 `timeout`。  
Every tool should define `name`, `description`, JSON-schema-like `parameters`, `is_read_only`, `capability`, `risk_level`, and `timeout`.

高风险工具必须使用非空 capability，让宿主可以显式允许或拒绝。  
High-risk tools must use a non-empty capability so hosts can explicitly allow or deny them.

## Fail Open vs Fail Closed

`BasePlugin.fail_closed = False` 表示 hook 失败会记录日志并尽量继续执行。  
`BasePlugin.fail_closed = False` means hook failures are logged and execution continues where possible.

`fail_closed = True` 表示 hook 失败会中止或拒绝受影响的操作，适合安全和策略插件。  
`fail_closed = True` means hook failures abort or reject the affected operation. Use it for safety and policy plugins.

## State

插件实例是 Agent 级作用域。单次运行状态应放在 `exec_ctx.get_plugin_state(plugin_name)`，或放在按 tenant/user/agent/session 分区的后端存储中。  
Plugin instances are Agent-scoped. Per-run state should be stored in `exec_ctx.get_plugin_state(plugin_name)` or in a backing store keyed by tenant, user, agent, and session.

避免把“当前请求”状态保存在插件实例字段上。  
Avoid keeping "current request" state on the plugin instance itself.

## Example

参考 `examples/05_custom_plugin`。  
See `examples/05_custom_plugin`.
