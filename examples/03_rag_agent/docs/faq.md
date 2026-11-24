# 常见问题

## AxcAgentEngine 是什么？

AxcAgentEngine 是一个纯 Agent 执行引擎框架，随包提供 ReAct 和 POR（Plan-Observe-Replan）策略插件。宿主也可以注册自定义循环，由模板的 runtime.strategy 选择。

## 支持哪些 LLM？

支持任何 OpenAI Chat Completions 兼容的 API，包括 OpenAI、Anthropic、DeepSeek、Ollama 等。

## 如何安装？

```bash
pip install axc-agent-engine
```

## 插件系统怎么用？

宿主在 PluginRegistry 中显式注册预置或自定义插件类，再在 Agent YAML 的 plugins 字段中启用并配置插件。
