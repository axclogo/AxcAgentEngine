# AxcAgentEngine Core Refactor Plan

本文档只描述 `AxcAgentEngine` 内部核心重构，不包含 RavenSystem 适配层、DB、router、WebSocket 协议或业务系统迁移。

## 核心目标

```text
ReAct：继续保留自研
POR：使用 pydantic-graph
Workflow / 暂停恢复：使用 Apache Burr
不要引入 LlamaIndex / LangGraph / CrewAI 这种大框架进核心
```

目标不是堆功能，而是降低长期维护负债：

- ReAct 是引擎最核心的可控循环，必须保持轻量、可读、可测试。
- POR 是有状态图执行问题，交给小型、显式、类型友好的 `pydantic-graph`。
- 暂停恢复和长 workflow 是 durable execution 问题，交给 Apache Burr。
- 任何依赖都必须服务于明确的执行模型，不允许为了集成而引入大框架。

## 非目标

- 不改 RavenSystem。
- 不引入 LangGraph、LlamaIndex、CrewAI。
- 不把业务协议、数据库模型、用户鉴权、私有工具发现写进引擎 core。
- 不重写 LLMProvider、ToolDefinition、ToolOutput 的公共契约，除非能显著减少核心复杂度。
- 不让 POR 或 Burr 反向污染 ReAct 主循环。

## 当前问题

当前仓库已经有：

- 自研 ReAct：`axc_agent_engine/core/executor.py`、`react_loop.py`
- 自研 POR：`axc_agent_engine/planning/*`
- 自研 checkpoint/resume：`runtime/checkpoint.py`、`planning/checkpointing.py`、`Agent.resume_stream`

问题在于三类职责仍然耦合：

- `Agent` 入口同时处理输入、session、executor 创建、resume。
- `Executor` 同时处理 ReAct、POR 路由、checkpoint 生命周期。
- `planning` 目录承担计划建模、执行调度、观察、重规划、checkpoint 多种职责。

重构方向是把三件事拆清楚：

```text
Agent API
  -> ReActKernel              # 自研，短循环，工具调用
  -> PORGraphRuntime          # pydantic-graph，计划图执行
  -> WorkflowRuntime          # Burr，durable run / pause / resume
```

## 架构边界

### ReAct Kernel

保留自研实现，边界收窄为：

```text
messages + tools + execution context
  -> LLM call
  -> tool call flow
  -> assistant final answer or next round
```

允许依赖：

- `LLMCaller`
- `MessageStore`
- `ToolRegistry`
- `ToolExecutionPipeline`
- `PluginManager`
- `ExecutionContext`

禁止依赖：

- `pydantic-graph`
- `burr`
- Raven 业务代码
- LangGraph / LlamaIndex / CrewAI

目标文件形态：

```text
axc_agent_engine/core/react_kernel.py
axc_agent_engine/core/react_turn.py
axc_agent_engine/core/executor.py        # 只做 orchestration boundary
```

### POR Graph Runtime

POR 使用 `pydantic-graph` 表达计划图和状态流转。

边界：

```text
PlanRequest
  -> PlanGraph
  -> StepNode / ObserveNode / ReplanNode
  -> PlanResult
```

`pydantic-graph` 只允许出现在：

```text
axc_agent_engine/planning/graph_runtime.py
axc_agent_engine/planning/graph_nodes.py
axc_agent_engine/planning/graph_state.py
```

不允许出现在：

```text
axc_agent_engine/core/*
axc_agent_engine/tools/*
axc_agent_engine/llm/*
```

POR 节点内执行单步任务时调用自研 ReAct kernel，不复制 ReAct 逻辑。

```text
StepNode
  -> ReActKernel.run_step(...)
```

### Workflow / Pause / Resume Runtime

暂停恢复使用 Apache Burr，定位为 durable workflow runtime，不是 Agent 框架。

边界：

```text
WorkflowRuntime
  start(run_request) -> run_id
  pause(run_id, reason)
  resume(run_id, input)
  status(run_id)
```

Burr 只允许出现在：

```text
axc_agent_engine/workflow/burr_runtime.py
axc_agent_engine/workflow/state.py
axc_agent_engine/workflow/adapters.py
```

不允许直接出现在：

```text
axc_agent_engine/core/*
axc_agent_engine/tools/*
axc_agent_engine/planning/graph_nodes.py
```

`Agent.resume_stream(...)` 最终应委托给 `WorkflowRuntime`，不直接拼 checkpoint 状态。

## 目录调整

新增：

```text
axc_agent_engine/workflow/
  __init__.py
  protocols.py
  state.py
  burr_runtime.py
  memory_runtime.py

axc_agent_engine/planning/
  graph_state.py
  graph_nodes.py
  graph_runtime.py
```

保留但收窄：

```text
axc_agent_engine/core/react_loop.py
axc_agent_engine/core/executor.py
axc_agent_engine/planning/planner.py
axc_agent_engine/planning/observer.py
axc_agent_engine/planning/replanner.py
```

迁移完成后评估删除或降级：

```text
axc_agent_engine/planning/por_runner.py
axc_agent_engine/planning/checkpointing.py
axc_agent_engine/runtime/recovery.py
```

## 依赖策略

`pyproject.toml` 调整：

```toml
dependencies = [
  "pydantic>=2.0,<3.0",
  "httpx>=0.27.0,<0.29",
  "pyyaml>=6.0,<7.0",
  "pydantic-graph>=...",
]

[project.optional-dependencies]
workflow = [
  "burr>=...",
]
```

原则：

- `pydantic-graph` 是 POR 核心依赖，可以进主依赖。
- `burr` 先放 optional dependency，避免所有轻量用户被迫安装 durable workflow 栈。
- 禁止 `langgraph`、`llama-index`、`crewai` 出现在主依赖、optional 依赖和 core imports。

版本号实施前必须查询并固定到合理上限。

## 执行阶段

### 阶段 1：加架构护栏

新增测试：

```text
tests/test_core_dependency_boundaries.py
```

覆盖：

- `langgraph` / `llama-index` / `crewai` 不在任何依赖中。
- `pydantic_graph` 不被 `core/*`、`tools/*` import。
- `burr` 不被 `core/*`、`tools/*`、`planning/*` 直接 import。
- `react_loop` 不依赖 POR graph runtime。

验收：

```bash
python3 -m pytest tests/test_core_dependency_boundaries.py
```

### 阶段 2：收窄自研 ReAct

目标：

- 从 `Executor` 中抽出 `ReActKernel`。
- `Executor` 只负责选择 `react_only`、`por_first`、`auto`，不直接实现每轮逻辑。
- 工具执行仍使用现有 `ToolExecutionPipeline`。

验收：

- 现有 ReAct 工具调用测试全部通过。
- `core/react_kernel.py` 不 import `planning`。
- `core/executor.py` 行数下降。

### 阶段 3：引入 pydantic-graph POR

目标：

- 用 `pydantic-graph` 表达 plan -> step -> observe -> replan。
- planner/observer/replanner 的业务逻辑由 graph node 服务调用。
- `PORRunner` 作为 POR 执行入口，内部委托 `PORGraphRuntime`。

事件保持：

```text
plan_created
step_start
step_completed
error
done
```

验收：

- `tests/test_planning.py`
- `tests/test_por_checkpointing.py`
- 新增 `tests/test_por_graph_runtime.py`

### 阶段 4：引入 WorkflowRuntime Protocol

目标：

```python
class WorkflowRuntime(Protocol):
    async def start(self, request: WorkflowRequest) -> AsyncIterator[Event]: ...
    async def resume(self, run_id: str, message: str = "") -> AsyncIterator[Event]: ...
    async def pause(self, run_id: str, reason: str = "") -> None: ...
```

先实现：

- `MemoryWorkflowRuntime`：包装现有 checkpoint store，保证现有测试不大面积破坏。
- `BurrWorkflowRuntime`：使用 Apache Burr 承载 durable 状态机。

验收：

- `Agent.resume_stream` 不直接读 checkpoint store。
- checkpoint store 只作为 workflow runtime 的持久层。
- 原 resume 测试迁移到 workflow runtime 测试。

### 阶段 5：Burr 接管暂停恢复

目标：

- ReAct run、POR run 都注册为 Burr action graph。
- pause/resume 不直接恢复 `Executor` 内部字段，只消费 `WorkflowRuntime` 提供的 snapshot。
- 对外保持 `agent.resume_stream(run_id, ...)`。

验收：

- 中断后可以恢复 ReAct。
- 中断后可以恢复 POR 当前 step。
- 恢复后不重复已完成工具调用。
- `run_id` 稳定。

### 阶段 6：删除冗余恢复路径

删除或降级：

- 删除 `Executor` 的直接 checkpoint 恢复入口。
- 删除 `Agent.resume_stream` 内部 checkpoint 拼装逻辑。
- 删除 `planning/checkpointing.py` 中与 Burr 重叠的状态恢复逻辑。

当前保留：

- `CheckpointStore` protocol，作为 workflow 持久层。
- `runtime/checkpoint.py` 的通用 checkpoint 数据结构，如 Burr adapter 仍需要。

## 测试矩阵

必须覆盖：

- 纯 ReAct 文本回答。
- ReAct 工具调用。
- ReAct 多轮工具调用。
- POR plan_created / step_start / step_completed。
- POR step 失败后 replan。
- POR resume 当前 step。
- ReAct pause/resume。
- human approval pause/resume。
- stream delta 不丢失。
- checkpoint/run_id 可查询。
- 禁止大框架依赖。

## 代码质量门槛

每阶段完成必须满足：

```bash
python3 -m pytest
```

并检查：

```bash
rg "langgraph|llama_index|llama-index|crewai" pyproject.toml axc_agent_engine tests
rg "pydantic_graph|pydantic-graph" axc_agent_engine/core axc_agent_engine/tools
rg "burr" axc_agent_engine/core axc_agent_engine/tools axc_agent_engine/planning
```

期望：

- 第一条只允许出现在边界测试或文档。
- 第二条无输出。
- 第三条无输出。

## 完成定义

重构完成必须同时满足：

- ReAct 自研路径仍可独立运行，不依赖 pydantic-graph 或 Burr。
- POR 主执行路径由 pydantic-graph 承载。
- pause/resume 主路径由 WorkflowRuntime 承载，Burr adapter 可用。
- `Agent` 不直接拼装 checkpoint 恢复细节。
- `Executor` 不直接实现 POR 状态机。
- 全量测试通过。
