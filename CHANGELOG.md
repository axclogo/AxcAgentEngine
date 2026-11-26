# Changelog

All notable changes to AxcAgentEngine are documented in this file.

## 3.3.2

### Fixed

- Settle write tools whenever they return a `ToolOutput`, including
  `ToolOutput.error`, so a known failure no longer blocks later resume or fork.
  Exceptions, timeouts, cancellation, and interrupted writes without a returned
  outcome still require reconciliation.
- Document that write tools must return failure results for rejected arguments
  or missing targets detected before writing, rather than raising exceptions.

## 3.3.1

### Changed

- Completion notices now count every delivered notice as one wakeup, including
  `inject` into a running caller. A running caller at `max_wakeups` returns
  `hold` instead of `inject`; the host writes the notice but does not deliver it
  or start a turn.
- Raise long-task defaults and bounds: `runtime.max_rounds` 500 / 1–100000,
  `sessions.max_wakeups` 100 / 0–100000, `sessions.max_active` 16 / 1–256,
  and `sessions.max_depth` 3 / 1–20.

## 3.3.0

### Added

- `Agent.fork_run` writes the selected cut's messages into the new session
  history, including system, `tool_calls`, and `tool` messages.
- `Agent.rewind_session` and `Agent.can_rewind` restore a session to the history
  loaded before a run; start checkpoints retain that history across restarts.
  Add the `on_session_rewind` plugin hook.
- Add `plugins.builtin.sessions.notices` with `notice_id`, `format_notice`,
  `decide`, `NOTICE_STATUSES`, and `CALLER_STATES`.
- Add `max_wakeups` to the sessions plugin and `turn_origin` to `SessionCaller`.
- Add `SessionInfo.scope` and show it in the session list text.

### Changed

- Session tools now reject any ancestor target for read, watch, send, and stop
  before authorization, and remove self/ancestors from `session_list`.
- Fork and resume distinguish a missing checkpoint owner from an owner mismatch.
  Checkpoints without an owner record require an identity-less caller; no owner
  migration is provided. Branch resume reports a missing owner record separately
  from a different owner.
- Harden fork rollback on cancellation and session-write failure, validate
  assistant `tool_calls` and tool `tool_call_id` structure before history restore, and propagate
  `ConfigValueError` from plugin configuration through Agent loading. Failed
  cleanup exposes `ForkFailure` and `Agent.discard_fork` for idempotent recovery.
- Require explicit `turn_origin` values to be `user` or `notice`; only an absent
  field defaults to `user`.

## 3.2.0

### Added

- Add the `sessions` plugin: `session_list`, `session_read`, `session_send`,
  `session_stop`, `session_create`, `session_watch`, and `session_wait` over a
  host-mounted `sessions.service`.
- Add `session_read` and `session_operate` capabilities.
- Add `docs/SESSIONS.md` with the host service contract, provenance, completion
  notices, and authorization rules.

## 3.1.0rc1

- Add opt-in ripgrep workspace search and content-version checks for file changes. Exact edits preserve unmatched bytes.
- Reclaim local foreground commands on timeout or cancellation. Large command output can be paged through file-backed artifacts.

- Fork a retained strategy checkpoint into an independent run and session. POR exposes a cut after every parallel result has been merged.

- Isolate compression and recall by agent and session. Anonymous runs use their own state.
- Add ordered active-run input, durable receipts, live POR progress and revision of pending steps.
- Reserve run IDs before reading checkpoints and reject stale queued resumes. A resume instruction joins the original conversation.
- Persist write-tool intent after argument validation and before dispatch. Recovery reports unresolved call IDs for host reconciliation.
- Delegate MCP transports to the optional official SDK. Remove the obsolete knowledge extra and choose Burr explicitly.
- Keep repetition and reflection state local to a run, and load scoped memory before constructing model context. Resetting a session clears its compression boundary.
- Honor the requested API listen address and resolve templates through the configured agent directory. HTTP clients receive the final POR text, token counts and cancellation outcome.
- Reject malformed model plans before replacing pending steps. The in-memory message bus releases idle listeners and pending requests on shutdown.
- Hosts can register ReAct, POR, or custom execution strategies and select them per agent.
- Examples select the bundled POR plugin in auto mode. A host-defined single-turn loop demonstrates switching implementations in YAML.
- Strategy checkpoints record the plugin name and state version. Plugins can migrate older snapshots before the loop starts.
- Plugin binding validates dependencies before initialization and rolls back partial failures in reverse order.
- Added a public strategy runtime, black-box conformance checks and `axc inspect` for resolved drivers, tools and capability policies.
- Fixed semaphore release on queue timeout and stream cleanup on early disconnect. Memory and Burr now use the same workflow lifecycle.

## [3.0.0] - 2026-10-26

### Fixed

- Isolated session history by an internal per-agent scope so identical session
  IDs cannot leak messages between agents.
- Kept an agent's persistence scope stable when its public `name` is changed at
  runtime.
- Scoped session reset and removal operations to the owning agent.

### Changed

- `SessionManager` now requires an identity seed when constructed.
- `MessagePersistence.save`, `load`, and `delete` now receive `scope_id` before
  `session_id`.
- `MessagePersistence` now includes `clear(scope_id)`.
- `Engine` now creates one session manager per agent instead of sharing a
  manager across all agents.

### Compatibility

- This release intentionally changes the `MessagePersistence` protocol and
  does not read data written with the unscoped 2.x persistence contract.

[3.0.0]: https://github.com/axclogo/AxcAgentEngine/releases/tag/v3.0.0
