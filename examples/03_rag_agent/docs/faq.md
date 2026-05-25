# 常见问题

## AxcAgentEngine 是什么？

AxcAgentEngine 是一个纯 Agent 执行引擎框架，支持 ReAct 和 POR（Plan-Observe-Replan）两种执行模式。

## 支持哪些 LLM？

支持任何 OpenAI Chat Completions 兼容的 API，包括 OpenAI、Anthropic、DeepSeek、Ollama 等。

## 如何安装？

```bash
pip install axc-agent-engine
```

## 插件系统怎么用？

在 Agent YAML 的 plugins 字段中声明要启用的插件及其配置。引擎内置 18 个插件，用户也可以开发自定义插件。
