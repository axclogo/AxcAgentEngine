# Changelog

All notable changes to this project will be documented in this file.

The format follows Keep a Changelog, and this project uses semantic versioning
once the public API reaches 1.0.

## [0.2.0] - 2026-05-18

### Added

- OpenAI Chat Completions compatible API subset.
- `/v1/capabilities` for machine-readable API capability discovery.
- POR checkpoint resume support.
- Structured audit events and `ErrorEnvelope`.
- Tool registry schema version and dynamic registration audit log.
- Context forking for isolated POR parallel steps.

### Changed

- LLM fallback is limited to retryable provider failures.
- Tool hook dispatch is centralized through `PluginManager`.
- Plan detection validates step IDs and dependency DAGs before execution.

### Notes

- In-memory stores are intended for development and tests.
- Request-level OpenAI `tools` and `tool_choice` are intentionally unsupported;
  tools are governed by Agent YAML and plugins.
