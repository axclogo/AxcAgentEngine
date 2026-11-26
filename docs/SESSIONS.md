# Session Operations

`plugins.sessions` lets an Agent read, message, stop, and create host-owned
sessions. It is a plugin over a host service; the engine core keeps no session
storage, user table, or authorization state.

## Session operations are not `agent_call`

| | `collaboration.agent_call` | `plugins.sessions` |
| --- | --- | --- |
| Object | an Agent, for one call | a durable, user-visible Session |
| Lifetime | caller waits for the reply | caller gets an accepted receipt; completion arrives as a notice |
| History | hidden implementation detail | the Session's own durable history |
| User control | none | user can open, steer, or stop the same Session |
| Authority | Agent allowlist plus capability | per-target host authorization plus capability |
| Result | returned inline | observed through `session_read` or a completion notice |

`agent_call` is a synchronous request/reply over the dispatcher. Session
operations always address a host Session id and never call another Agent
directly.

## Host service

Mount an object implementing `SessionService` at `sessions.service`:

```python
template.instantiate(
    models=models,
    mounts={"sessions.service": my_session_service},
)
```

Every method is asynchronous and receives the trusted caller dictionary:

```python
{
    "session_id": "sess-a",
    "agent_name": "weekly-report",
    "run_id": "run-1",
    "title": "写周报",
    "user_id": "u-1",
    "tenant_id": "t-1",
    "turn_origin": "user",
    "provenance": {"depth": 0, "chain": []},
}
```

The plugin builds this dictionary from run metadata. It never accepts caller
identity from tool arguments. The host must re-check its own user and business
permissions in every method; a grant can only narrow that authority.

### Methods

| Method | Contract |
| --- | --- |
| `list_sessions(caller, filters)` | Return `SessionInfo` rows the caller may see. Filters: `agent_name`, `status`, `title`, `updated_after`, `limit`. |
| `read(caller, session_id, since, limit)` | Return committed `SessionMessage` rows after `since` (or the latest `limit`). Exclude thinking and raw tool output. |
| `send(caller, session_id, text, max_active)` | Append the message and start or steer a run. Return `{"accepted": bool, "message_id": str, "reason": str}`. |
| `create(caller, agent_name, text, title, max_active)` | Create a Session and start its first run. Return `{"accepted": bool, "session_id": str, "reason": str}`. |
| `stop(caller, session_id)` | Stop the current run only. Return `{"stopped": bool, "status": str}`; an idle Session is a successful no-op. |
| `watch(caller, session_id, enabled)` | Persist or remove a completion subscription. |
| `authorize(caller, session_id, level, cross_agent)` | Return `"granted"`, `"denied"`, or `"unavailable"`. |

`SessionInfo` carries `session_id`, `agent_name`, `title`, `status`,
`updated_at`, `access`, `last_message_id`, and `scope`. `SessionMessage` carries
`message_id`, `role`, `text`, `created_at`, and `origin_session_id`. `scope` is
opaque host data (WebUI sends `local` or `topic:<id>`); the plugin only shows
it in the list text and JSON result.

### Authorization

`authorize` is the only user-consent entry point. The host:

1. checks its persisted grant for this caller Session and target;
2. on the first visit, shows its own confirmation UI;
3. persists the user's choice, including whether it covers one Session or the
   target Agent's whole group;
4. returns `"unavailable"` when it cannot ask.

The plugin caches `granted` and `denied` per run and never prompts twice for the
same target and level. A `denied` read also blocks operate. The host may grant a
Session scope or an Agent scope; the plugin does not model that distinction.

`cross_agent=False` is passed to `authorize`; the host must reject a target
belonging to another Agent. The plugin also forces its own `agent_name` filter on
`session_list` when the flag is off.

## Dispatch depth and capacity

The plugin sends `provenance.depth` and `provenance.chain` with every dispatch.
The host must persist them with the message and run, then inject
`metadata["session_provenance"]` into the child run:

```json
{"depth": 1, "chain": ["sess-a"]}
```

Checkpoints persist only `run_id`, `session_id`, and `agent_name`, so the host
must resupply `session_provenance` on resume and fork.

The plugin rejects a target equal to the caller, and any target in the
caller's ancestor chain for `read`, `watch`, `send`, and `stop`. This check runs
before authorization, so a forbidden target never opens a consent prompt.
`session_list` also removes the caller and every ancestor from the host result.
`max_active` is passed to `send` and `create`; the host must enforce it
atomically and reject without queueing.

## Completion notices

When a Session dispatched by the caller settles, the host delivers a user-role
message to the caller Session:

```text
后台会话「客户访谈」（情报分析助手）已完成。
<last reply>
除非你继续给它发消息，否则它不会再做别的事。
```

- Idle caller: the notice starts a new turn.
- Running caller: deliver through live input (`agent.send_message`); the caller
  sees it on its next step.
- Archived or deleted caller: drop the notice.

`session_watch` installs the same subscription for a Session the caller did not
dispatch. `session_send` and `session_create` always subscribe by default.

`session_wait` does not poll a target. It waits for the caller's own next input
(user steering or a completion notice) and returns only `{"timed_out": bool}`.

### Notice module

Use `axc_agent_engine.plugins.builtin.sessions` for the shared decisions:

```python
from axc_agent_engine.plugins.builtin.sessions import (
    CALLER_STATES, NOTICE_STATUSES, decide, format_notice, notice_id,
)

message_id = notice_id(target_session_id, target_run_id)
text = format_notice(title=..., agent_name=..., status="done", last_reply=...)
action = decide(caller_state="idle", auto_wakeups=3, max_wakeups=100)
# action: "start" | "inject" | "hold" | "drop"
```

`format_notice` renders the three-line notice; `max_chars` defaults to 4000 and
appends `…（已截断）` when needed. `decide` returns:

| caller_state | condition | action |
| --- | --- | --- |
| `archived` / `deleted` | any | `drop` |
| `running` | `max_wakeups == 0` or `auto_wakeups < max_wakeups` | `inject` |
| `running` | `auto_wakeups >= max_wakeups` and `max_wakeups > 0` | `hold` |
| `idle` | `max_wakeups == 0` or `auto_wakeups < max_wakeups` | `start` |
| `idle` | `auto_wakeups >= max_wakeups` and `max_wakeups > 0` | `hold` |

### Auto-wakeup counting

The host persists one `auto_wakeups` counter per caller Session:

- Every notice that makes the caller execute another step increments the
  counter: both `start` (new turn) and `inject` (delivered into the running turn).
- At the limit (`hold`): write the notice into the caller's history, but do not
  deliver it into the current run and do not start a new turn. The next
  user-started turn must include the backlog.
- User sends input in the caller Session: reset to zero.

Set `metadata["turn_origin"] = "notice"` for a turn started by a notice, and
`"user"` (or omit it) for every other turn. The plugin rejects any other value.

## Configuration

```yaml
plugins:
  sessions:
    enabled: true
    allow_create: true
    allow_operate: true
    cross_agent: true
    max_depth: 3
    max_active: 16
    wait_timeout: 30
    read_limit: 50
    read_max_chars: 20000
    max_wakeups: 100
```

`max_wakeups` accepts integers from 0 to 100000; 0 disables the limit.
`max_active` accepts 1 to 256; `max_depth` accepts 1 to 20. Invalid values fail
Agent loading with `ConfigValueError`. `runtime.max_rounds` defaults to 500 and
accepts 1 to 100000.

`allow_operate: false` removes `session_send` and `session_stop`.
`allow_create: false` removes `session_create`.

Grant `session_read` and `session_operate` through `runtime.allowed_capabilities`
before the tools can run:

```yaml
runtime:
  allowed_capabilities: ["session_read", "session_operate"]
```

## Fork and rewind

`Agent.fork_run(run_id, checkpoint_id)` now writes the checkpoint's messages
into the new Session history before returning. The new Session contains system,
assistant `tool_calls`, and `tool` messages from the cut; hosts no longer need to
reconstruct branch history from transcript text. `session_provenance` and other
host metadata still must be supplied on every later `stream`/`resume_stream`.

### Fork failure recovery

Fork uses compensating cleanup because `CheckpointStore` and
`MessagePersistence` are independent stores. If the checkpoint save fails, no
session history is written. If the session write fails or is cancelled, the
engine tries to remove both the branch checkpoint and the branch session. When
that cleanup fails, the original exception is re-raised with a `ForkFailure`
attached.

```python
from axc_agent_engine import fork_failure_of

try:
    branch = await agent.fork_run(run_id, checkpoint_id, metadata=identity)
except Exception as error:
    failure = fork_failure_of(error)
    if failure is not None and not failure.cleaned:
        # Retry after the checkpoint/message store recovers. Idempotent.
        result = await agent.discard_fork(failure)
        assert result.cleaned
```

`ForkFailure` carries `branch_run_id`, `branch_session_id`, `source_run_id`,
`source_checkpoint_id`, `cause`, `rollback_error`, `checkpoint_written`,
`session_written`, and `cleaned`. `discard_fork` returns `ForkCleanupResult` and
only deletes the branch IDs; it never touches the source run. Cancellation keeps
its `asyncio.CancelledError` type and carries the same `fork_failure` attribute.
A failed cleanup requires the host to retry `discard_fork` after recovery. A
process crash between the two writes cannot produce a `ForkFailure`; the host
must discover the orphan from its checkpoint and session stores. An uncertain
storage result is reported through the attached failure.

`Agent.can_rewind(session_id, before_run_id)` and
`Agent.rewind_session(session_id, before_run_id=...)` restore the history that
was loaded when that run started. The engine records that history position in
the run's start checkpoint; it survives restart when the host provides a durable
`CheckpointStore`. Rewind:

- holds the same `SessionExecutionGate.reset` guard as `reset_session`; an
  active or queued run fails immediately;
- calls plugin `on_session_rewind(agent_name, session_id, before_run_id)` before
  rewriting history;
- does not delete checkpoints. Cuts at or after `before_run_id` become
  semantically invalid and the host must stop offering them;
- rejects malformed recorded history. `can_rewind` returns `False`; `rewind_session`
  raises `ValueError` without changing the session.

### Checkpoint ownership

For a checkpoint without an owner record (`owner == {}`), only a caller that
supplies no `tenant_id`/`user_id` may fork or resume it. A caller with identity
receives `Checkpoint has no owner record; retry without tenant or user
identity`. A checkpoint with a different owner receives `Checkpoint belongs to
another tenant or user`. This is rule A: no migration is provided. A fork of an
ownerless cut also stays ownerless; hosts that need identity must start a new
conversation/lineage instead of attaching identity to the old cut.

## Host checklist

1. Mount `SessionService` at `sessions.service`.
2. Enforce user, tenant, Agent, and business-scope permissions inside every
   method.
3. Implement `authorize` with a real consent UI and persistent grants.
4. Persist provenance with each dispatch and reinject it on every run,
   resume, and fork.
5. Enforce `max_active` atomically in `send` and `create`.
6. Use `format_notice`/`notice_id` and `decide` for completion notices; persist
   `auto_wakeups`, increment it for both `start` and `inject`, reset it on user
   input, and pass `turn_origin`. On `hold`, write the notice but do not deliver
   it or start a turn.
7. Add `session_read` and `session_operate` to `runtime.allowed_capabilities`.
8. Render `session_*` tool calls as cards that link to the target Session.
9. Stop seeding branch history manually; `fork_run` now does it. Use
   `can_rewind`/`rewind_session` for edit-and-resend and stop offering cuts at or
   after the rewound run.
10. Make `CheckpointStore.delete_run` and `MessagePersistence.delete` idempotent
    for missing IDs; `discard_fork` retries cleanup and must not fail on an
    already-removed branch.
