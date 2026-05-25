# Sidecar 边界说明 / Sidecar Boundary

`sidecar` 是宿主侧主动调用的旁路能力集合，不属于 Agent 核心执行链路。
`sidecar` is a host-invoked set of auxiliary capabilities, not part of the Agent core execution path.

## 边界 / Boundary

- 不从 Agent YAML 自动加载。
  It is not loaded automatically from Agent YAML.
- 不参与默认 ReAct/POR 执行循环。
  It does not participate in the default ReAct/POR execution loop.
- 不改变核心 `Agent`、`Executor`、`PluginManager` 的运行语义。
  It must not change the runtime semantics of `Agent`, `Executor`, or `PluginManager`.
- 只能由宿主应用显式 import 和调用。
  Host applications must import and call these capabilities explicitly.
- 可以复用核心模型、存储协议、dispatcher、LLM client，但不能反向污染核心层。
  It may reuse core models, storage protocols, dispatcher, and LLM clients, but must not push sidecar concerns back into the core layer.

## 目录职责 / Directory Responsibilities

- `agent_selector/`：宿主侧 Agent 选择，根据任务、能力、标签、成本、延迟和风险做确定性排序。
  Host-side Agent selection that ranks candidates by task, capability, tag, cost, latency, and risk.
- `cost_optimizer/`：成本样本聚合、花费估算和优化建议。
  Cost sample aggregation, spend estimation, and optimization findings.
- `distiller/`：从执行轨迹中蒸馏规则、工具偏好、反模式和 skill 候选。
  Distills rules, tool preferences, anti-patterns, and skill candidates from execution traces.
- `failure_miner/`：聚类失败记录，识别失败类别并给出处理建议。
  Clusters failure records, identifies categories, and suggests follow-up actions.
- `orchestration/`：旁路多 Agent 任务生命周期，创建、运行、查询和取消后台编排任务。
  Sidecar multi-agent task lifecycle for creating, running, querying, and cancelling orchestration tasks.
- `eval/`：评测执行、LLM-as-judge、人工标注匹配和报告生成。
  Evaluation execution, LLM-as-judge, annotation matching, and report generation.
- `multi_agent/`：多 Agent 会话、调度器、停止条件、persona 和共享上下文。
  Multi-agent sessions, schedulers, stop conditions, persona, and shared context.
- `simulation/`：结构化仿真内核、场景、动作解析、环境、评估器和报告。
  Structured simulation kernel, scenarios, action parsing, environments, evaluators, and reports.

## 设计原则 / Design Principles

- 一种旁路能力一个目录。
  One sidecar capability owns one directory.
- 根目录只保留聚合导出，不放具体实现。
  The root package only aggregates exports and must not hold feature implementation.
- 旁路能力优先保持纯数据输入输出，避免依赖运行中 Agent 内部状态。
  Sidecar capabilities should prefer explicit data input/output and avoid depending on live Agent internals.
- 代码是负债，功能才是价值；新增旁路前必须确认它不能放在已有目录内。
  Code is liability and behavior is value; add a new sidecar only when it cannot fit an existing directory.
- 注释和 docstring 必须中英双语，便于中文维护和英文生态工具同时理解。
  Comments and docstrings must be bilingual so Chinese maintainers and English ecosystem tooling can both read them.
