# Architecture

## Data Flow

```
Engine.__init__(default_llm, message_bus, audit_sink, checkpoint_store, ...)
  → create AgentMessageDispatcher(message_bus) if bus provided
  → create ProviderRegistry
  → receive explicit PluginRegistry (empty by default)
  → create ExecutionServices(result_store, message_bus, dispatcher,
      audit_sink, checkpoint_store, command_executor, policy_evaluator)

Engine.load_agent(yaml, llm="coder", fallback_llm="fast")
  → parse YAML → AgentConfig
  → resolve per-agent LLM via ProviderRegistry or direct config
  → create PluginContext (agent-level LLM, stores, dispatcher)
  → load_plugins() from Engine PluginRegistry → topological sort → initialize()
  → create ToolRegistry → freeze()
  → create Agent
  → dispatcher.run_agent_consumer(agent)  ← auto-start consumer
  → return Agent

Agent.chat/stream(message, session_id, llm_options)
  → _create_executor(config, routing_mode, llm_options)
      → clone Engine-provided ExecutionServices into ExecutionContext
      → write agent_name/session_id metadata
  → restore session context
  → Executor.run_stream(user_message)
      → _init_messages (system prompt + plugin context + user)
      → PluginManager.on_execution_start()
      → _react_loop:
          ├─ transform_messages (plugins)
          ├─ check_should_stop (plugins)
          ├─ LLMCaller.call (stream or sync)
          │   ├─ pre_llm_call hooks
          │   ├─ primary LLM (retry + fallback)
          │   ├─ StreamAggregator (real-time on_delta → queue)
          │   └─ post_llm_call hooks
          ├─ TransactionRouter.route(message)
          │   └─ if action=por_plan → PORRunner
          ├─ if tool_calls:
          │   └─ execute_tool_calls (orchestrator)
          │       ├─ partition (read=concurrent, write=serial)
          │       ├─ pre_tool_call hooks (fail_closed enforced)
          │       ├─ capability policy (explicit allowed_capabilities)
          │       ├─ validate_arguments (enum/range/type)
          │       ├─ execute with timeout + retry (read-only only)
          │       └─ post_tool_call hooks (ToolOutput)
          └─ on_round_end hooks
      → on_execution_complete (modifies result before DONE)
      → on_execution_end
  → save session (auto-persist)
```

## POR (Plan-Observe-Replan)

POR is selected by TransactionRouter based on runtime routing policy:
- `routing.mode: auto` — detect structured plan JSON in LLM response content
- `routing.mode: por_first` — always attempt plan detection
- `routing.mode: react_only` — never enter POR, pure ReAct

`create_plan` is an internal function in `planner.py` that constructs a Plan
dataclass from parsed JSON. It is NOT a tool in ToolRegistry.

PORRunner receives a Plan object directly from TransactionRouter:
- Steps execute sequentially (parallel for isolated contexts)
- Each step: sub-ReAct loop with dynamic round budget
- Observer evaluates step results (LLM or heuristic fallback)
- Replanner: topological step dependencies, max 3 replans

## Multi-Agent Collaboration

All cross-agent communication goes through MessageBus + AgentMessageDispatcher:

```
Engine creates dispatcher → load_agent starts consumer per agent

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

All tools return ToolOutput. Non-ToolOutput returns are rejected.
Tools with a non-empty capability are rejected unless the capability is listed
in `runtime.allowed_capabilities` or a custom policy evaluator allows them.

```
orchestrator.execute_tool_calls
  → partition_tool_calls (read-only=concurrent batch, write=serial)
  → _execute_single:
      1. pre_tool_call hooks (fail_closed → reject)
      2. resolve ToolDefinition from registry
      3. evaluate capability policy
      4. build ToolContext (workspace + ExecutionServices)
      5. executor.execute_tool (validate → retry → timeout → enforce ToolOutput)
      6. post_tool_call hooks (receive/return ToolOutput)
  → message_store.append_tool_results uses ToolOutput.compact_view()
```

Large results stored via ResultStore, retrievable via result_read/result_search/result_page tools.
HTTP tools block localhost/private/link-local/reserved destinations before
request execution. Command tools use the configured CommandExecutor, falling
back to LocalSubprocessExecutor for local development.

## Storage Protocols

Zero external dependencies. Protocol interfaces with in-memory defaults:
- KVStore: key-value with TTL
- MessagePersistence: session message storage
- SpanStore: tracing span persistence
- VectorStore: embedding search (linear scan, ~1000 entries)
- MessageBus: pub/sub with max_idle auto-exit
- ResultStore: large tool output storage with paged retrieval
- AuditSink: structured tool audit events
- CheckpointStore: durable execution checkpoints

Engine injects stores, message bus, audit sink, checkpoint store, custom policy,
and command executor services into every Agent-created ExecutionContext.
