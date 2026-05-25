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

Before running examples, install the package in editable mode:

```bash
pip install -e ".[dev,api,knowledge]"
```

Set the LLM endpoint expected by each example in environment variables or in
the example host code. Do not commit API keys.
