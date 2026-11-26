# Architecture

## Data Flow

```
Engine.__init__(message_bus, audit_sink, checkpoint_store, plugin_registry, ...)
  → create AgentMessageDispatcher(message_bus) if bus provided
  → receive explicit PluginRegistry (empty by default)
  → create ExecutionServices(artifact_store, message_bus, dispatcher,
      audit_sink, checkpoint_store, command_executor, policy_evaluator)

Engine.load_agent_template(yaml)
  → parse YAML → AgentConfig
  → return AgentTemplate

AgentTemplate.instantiate(models=AgentModels(...), mounts=..., metadata=..., overrides=...)
  → materialize AgentConfig with overrides
  → bind Agent-level model objects
  → create PluginContext (agent-level LLM, stores, dispatcher)
  → validate plugin schemas and dependency graph → initialize dependencies first
  → create Agent with an internally scoped SessionManager
  → select the strategy, register plugin tools, then freeze the tool registry
  → dispatcher.run_agent_consumer(agent)  ← auto-start consumer
  → return Agent

Agent.chat/stream(message, session_id, llm_options)
  → _execution_context(request)
      → clone Engine-provided ExecutionServices into ExecutionContext
      → write agent_name/session_id metadata
  → _create_executor(ctx) binds the selected strategy and its run-scoped runtime
  → restore session context
  → Executor.run_stream(user_message)
      → initialize input and system messages for a new run
      → run start hooks and insert the collected plugin context
      → create and run the selected loop (official ReAct/POR or host plugin)
      → official ReAct path:
          ├─ transform_messages (plugins)
          ├─ check_should_stop (plugins)
          ├─ LLMCaller.call (stream or sync)
          │   ├─ pre_llm_call hooks
          │   ├─ primary LLM (retry + fallback)
          │   ├─ StreamAggregator (real-time on_delta → queue)
          │   └─ post_llm_call hooks
          ├─ POR auto mode only: TransactionRouter.route(message)
          │   └─ if action=por_plan → PORRunner
          ├─ if tool_calls:
          │   └─ execute_tool_calls (orchestrator)
          │       ├─ partition (read=concurrent, write=serial)
          │       ├─ pre_tool_call hooks (errors propagate)
          │       ├─ capability policy (explicit allowed_capabilities)
          │       ├─ validate_arguments (enum/range/type)
          │       ├─ execute with timeout + retry (read-only only)
          │       └─ post_tool_call hooks (ToolOutput)
          └─ on_round_end hooks
      → on_execution_complete (modifies result before DONE)
      → on_execution_end
  → save session (auto-persist)
```

## Builtin Plugin Boundary

Official builtin plugins keep agent behavior in the plugin and external resource
ownership in the host:

- Plugin logic may choose retrieval, memory, graph, tracing, or skill strategies.
- YAML config only describes stable behavior and policy.
- Runtime objects are bound through `mounts` at `AgentTemplate.instantiate()`.
- `overrides` only patches YAML-serializable config fields. It cannot inject
  client/store/provider objects or change official resource slots.
- Builtin plugins must not create external network clients from plugin YAML
  endpoint/API-key fields.
- Missing required resources and resource call failures are explicit errors, not
  silent fallback paths.

Knowledge uses plugin-owned retrieval strategy over local `sources` plus mounted
resources such as `knowledge.embedding`, `knowledge.vector_store`,
`knowledge.documents`, `knowledge.index`, and `knowledge.reranker`. The plugin
decides how to combine BM25, vector search, fusion, filtering, tracing, and
reranking; the host owns any external client lifecycle.

## POR (Plan-Observe-Replan)

The official POR plugin supports `plugins.por.mode` values `por_first` (default) and `auto`.
New hosts register a strategy plugin and enable it in YAML. Only templates without a named or enabled strategy use the routing policy `runtime.routing.mode` to select one of those same official implementations:
- `runtime.routing.mode: auto` — detect structured plan JSON in LLM response content
- `runtime.routing.mode: por_first` — generate a plan before the first ReAct round
- `runtime.routing.mode: react_only` — never enter POR, pure ReAct

`create_plan` is an internal function in `planner.py` that constructs a Plan
dataclass from parsed JSON. It is NOT a tool in ToolRegistry.

In auto mode the router hands a detected plan to PORRunner. In por_first mode the strategy asks PlanningService for the initial plan.

- POR dispatches at most `plugins.por.max_parallel_steps` ready steps per batch (default 4). Each step receives its declared prerequisite results.
- Serial and isolated step loops charge the same run round counter. The step deadline covers model and tool waits. The executor applies the total timeout while waiting for strategy events and closes the producer on expiry.
- Observer evaluates step results (LLM or heuristic fallback)
- Replanner: topological step dependencies, max 3 replans

## Multi-Agent Collaboration

All cross-agent communication goes through MessageBus + AgentMessageDispatcher:

```
Engine creates dispatcher → instantiate starts consumer per agent

Caller (collaboration/swarm/session)
  → dispatcher.request(envelope)
  → publish to agent:{recipient} channel
  → consumer receives envelope
  → consumer calls agent.chat() (agent's own LLM/plugins)
  → consumer publishes reply to _reply:{correlation_id}
  → caller receives reply via correlation_id matching
```

Key constraints:
- No direct agent.chat() outside dispatcher consumer
- collaboration/swarm plugins use plugin_ctx.dispatcher (shared instance)
- MultiAgentSession receives dispatcher in constructor
- Engine manages consumer lifecycle (start on load, stop on unload/close)

## Plugin Hook Lifecycle

```
initialize(config, plugin_ctx)     # Engine-level, once per Agent load
├─ on_execution_start(exec_ctx)    # Per-request, reset run state here
│   ├─ inject_context() → str      # Sync: inject into system prompt
│   ├─ transform_messages()        # Sync: modify message list
│   ├─ pre_llm_call()              # Sync: modify messages/tools before LLM
│   ├─ [LLM call]
│   ├─ post_llm_call()             # Async: observe LLM response
│   ├─ pre_tool_call()             # Async: allow/reject/modify tool args
│   ├─ [tool execution]
│   ├─ post_tool_call(ToolOutput)  # Async: modify ToolOutput
│   ├─ on_round_end()              # Async: observe round completion
│   └─ should_stop()               # Sync: signal early termination
├─ on_execution_complete()         # Async: modify final result before DONE
├─ on_execution_end()              # Async: cleanup, always called
└─ close()                         # Engine shutdown, parallel with timeout
```

## Tool Execution Pipeline

`runtime.parallel_tool_calls: false` serializes tool execution even when a model or custom loop submits multiple calls. With parallel execution enabled, `runtime.max_parallel_tool_calls` sets a positive per-run limit (default 4), shared by POR steps. Adjacent read-only calls can overlap. Writes retain their position between read batches, and results follow request order.

All tools return ToolOutput. Non-ToolOutput returns are rejected.
Tools with a non-empty capability are rejected unless the capability is listed
in `runtime.allowed_capabilities` or a custom policy evaluator allows them.

```
orchestrator.execute_tool_calls
  → partition_tool_calls (read-only=concurrent batch, write=serial)
  → _execute_single:
      1. pre_tool_call hooks (errors propagate; explicit rejection returns false)
      2. resolve ToolDefinition from registry
      3. evaluate capability policy
      4. build ToolContext (workspace + ExecutionServices)
      5. executor.execute_tool (validate → retry → timeout → enforce ToolOutput)
      6. post_tool_call hooks (receive/return ToolOutput)
  → message_store.append_tool_results uses ToolOutput.context_view()
```

Large results stored via ArtifactStore, retrievable via artifact_read/artifact_search/artifact_page tools.
HTTP tools block localhost/private/link-local/reserved destinations before
request execution. Command tools use the configured CommandExecutor, falling
back to LocalSubprocessExecutor for local development. Local output is captured on disk with at most 1 MiB per
stream returned inline. A larger stream is retained as a complete file under the command working directory’s
`.axc/logs/`; command tools register that path with the host artifact store. Retain the file for the lifetime of
its artifact reference. `CommandResult.stdout_path` and `stderr_path` identify streams stored on disk. A host executor returning these fields supplies paths its artifact store can read. POSIX commands run in a dedicated process group, reclaimed on exit, timeout or cancellation.
On Windows, timeout and cancellation request tree termination with `taskkill /T /F`. A host executor owns the
lifecycle of remote jobs and any processes that detach from its control.

## Storage Protocols

Zero external dependencies. Protocol interfaces with in-memory defaults:
- KVStore: key-value with TTL
- MessagePersistence: message storage keyed by opaque scope ID and session ID
- SpanStore: tracing span persistence
- VectorStore: embedding search (linear scan, ~1000 entries)
- MessageBus: pub/sub with max_idle auto-exit
- ArtifactStore: large tool output storage with paged retrieval
- AuditSink: structured tool audit events
- CheckpointStore: durable execution checkpoints

`MessagePersistence` receives an opaque, deterministic `scope_id` from the
engine for `save`, `load`, `delete`, and `clear`. Persistence implementations
store the scope as part of the key and do not derive it from the Agent's public
name at call time.

Engine injects stores, message bus, audit sink, checkpoint store, custom policy,
and command executor services into every Agent-created ExecutionContext.


## Resume and cleanup

A resume request goes through the workflow runtime to recover a snapshot. The executor checks the strategy name, validates its state version, and applies any migration before starting execution hooks or constructing the loop. A failed migration leaves the source checkpoint available for inspection or retry.

Memory and Burr share the transitions used for run, pause, resume and terminal events. Burr supplies its state representation and resume action. Both adapters track workflow status in process. Recovery in a fresh process requires a host checkpoint store that can read the saved snapshot. A closed, unfinished stream pauses the workflow. Agent session writes and release of concurrency slots finish before the caller receives a terminal event.

A completed execution checkpoint contains the final reply. Resuming it with no pending input returns that reply after ownership and strategy validation. The replay leaves the checkpoint and any newer session history intact. New input starts another loop from the saved conversation. An interrupted snapshot with missing tool results stops before a model request and reports the outstanding call IDs.

`Agent.rewind_session(session_id, before_run_id=...)` restores the history that was loaded when that run started. The start checkpoint records that history, so the record survives restart with a durable store. The call holds the same session reset gate as `reset_session`, calls `on_session_rewind` before rewriting history, and leaves checkpoints in place. `Agent.can_rewind` reports whether the record exists and is valid. Cuts at or after the rewound run become semantically invalid; the host stops offering them.

A checkpoint without an owner record may only be forked or resumed by a caller that supplies no `tenant_id` or `user_id`. Identity-bearing callers receive an explicit missing-owner error; a different owner receives an identity-mismatch error. The engine provides no migration for old ownerless cuts.

### Run-local control

Each executor owns one FIFO inbox, message history and checkpoint writer. Input admission, consumption, plan revision and tool-write intent share the writer lock. A running POR graph publishes plan and step progress to its consumer. Closing the stream joins the graph and its outstanding step tasks. A dispatched agent has a separate run and checkpoint ID, with cancellation linked to its active parent. This permits independent recovery and keeps child receipts out of the parent inbox.

The agent shares compression configuration across runs. It keeps buffers, observations and file caches in the execution context. A named run restores and saves its session boundary through the selected store. Anonymous runs start clean.

The in-memory message bus keeps agent inbox subscriptions open while idle. Hosts can pass `max_idle_rounds` to set an idle timeout in minutes. Closing the bus wakes subscribers and pending request callers immediately. Each short-lived reply channel is removed when its last subscriber leaves.

## Execution forks

A host selects a retained checkpoint with `cp.forkable` from its own `CheckpointStore.list(run_id)`. A rejected position exposes its reason in `cp.fork_blocker`.
`await agent.fork_run(run_id, cp.id, metadata=identity)` checks the exact cut, saves a single independent checkpoint, and writes the cut's messages into the new session history. The returned `ForkedRun` contains fresh run and session IDs. The source may still be running.

Fork publication uses compensating cleanup, not a cross-store transaction. If the checkpoint write fails, no session history is written. If the session write fails (including cancellation), the engine removes the new checkpoint and the branch session. The checkpoint store and message persistence have no shared transaction; when cleanup itself fails, the original exception is re-raised with an attached `ForkFailure`. The host then calls `Agent.discard_fork(failure)` after storage recovers; that call is idempotent, removes only the branch IDs, and never touches the source run. A process crash between the two writes can leave one side. The engine cannot return a `ForkFailure` after process loss; the host must discover the orphan from its checkpoint and session stores.
Resume the child through `agent.resume_stream(child.run_id, message, metadata=identity)`.
The source can be deleted after creation. Every later child checkpoint records the immediate source run and cut in
`metadata["forked_from"]`. A child can be forked by the same operation.

A cut includes admitted messages, recorded tool results, the strategy cursor and portable session data.
Queued input, receipts, current cancellation, and billing belong to the originating run. An unfinished branch
inherits the round cursor. A completed branch replays its saved result without model calls and starts a fresh
round budget when given new input. Its session history is written back when the child executes.
Old or expired cuts, an incompatible strategy or plugin set, and unresolved writes fail explicitly.

宿主负责调用授权、多进程单写、检查点保留期限，以及将切点与 Git 工作区、制品或外部系统版本关联。
引擎从切点继续时不会重放历史工具调用。切点之后的工作可能由两个分支分别执行，文件和外部副作用由宿主隔离。
分叉和分支恢复时传入原始租户与用户身份；同时提供记忆插件配置的其他作用域字段。
