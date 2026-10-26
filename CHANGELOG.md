# Changelog

All notable changes to AxcAgentEngine are documented in this file.

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
