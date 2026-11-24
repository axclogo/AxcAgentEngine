# Examples

These examples are small starting points for common host integrations.

| Example | Purpose |
| --- | --- |
| `01_basic_chat` | Minimal Agent YAML and Python SDK usage. |
| `02_tool_agent` | Built-in tools and capability gates. |
| `03_rag_agent` | Knowledge plugin with local documents. |
| `04_streaming` | Streaming events from `agent.stream()`. |
| `05_custom_plugin` | Writing and loading a custom plugin. |
| `06_multi_agent` | Sidecar multi-agent orchestration. |
| `07_simulation` | Debate/red-blue simulation sidecar examples. |
| `08_custom_loop` | Select a host-defined single-reply loop or the bundled POR strategy in the same template. |
| [09_live_input](09_live_input/run.py) | Offline POR progress, live input, cancellation and checkpoint forks. |

Examples 01–07 explicitly register POR and select its auto mode. Example 08 registers both implementations. Set `runtime.strategy` to `single_reply` or `por` and run `python examples/08_custom_loop/run.py`.

示例 01–07 显式注册并选择官方 POR 的自动路由模式。示例 08 在同一模板中启用两个循环，由 `runtime.strategy` 选择。

Inspect example 01 offline with `axc inspect --agent examples/01_basic_chat/agent.yaml`. Example 08 uses a host registry. Call `agent.inspect()` on that host to see the selected strategy and run `python -m pytest -q tests/engine/test_example_strategies.py` to check both selections offline.

Before running examples, install the package in editable mode:

```bash
pip install -e ".[dev,api]"
```

Set the LLM endpoint expected by each example in environment variables or in
the example host code. Do not commit API keys.

Run `python -m examples.09_live_input.run` to revise an active plan. Add `--cancel` to see an accepted message reported as unprocessed. Both modes run without an API key.

离线运行 `python -m examples.09_live_input.run --fork`，从原 POR 计划的历史检查点创建独立分支，并在子分支提出新指令。

Example 02 enables read-only `file_grep` through the builtin tools plugin. Install ripgrep in the command environment to use it. 示例 02 通过预置工具插件启用只读代码搜索，调用时需要命令环境提供 `rg`。
