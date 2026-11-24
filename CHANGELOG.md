# Changelog

All notable changes to AxcAgentEngine are documented in this file.

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
