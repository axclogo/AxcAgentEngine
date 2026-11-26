# API Compatibility

AxcAgentEngine exposes an OpenAI Chat Completions compatible subset for hosts
that want to call an Agent over HTTP.

The host selects each Agent’s execution strategy when it loads the template. Both HTTP streaming and non-streaming requests use that Agent’s configured loop. See the [execution strategy contract](PLUGIN_DEVELOPMENT.md#execution-strategies).

## Routes

| Route | Status | Notes |
| --- | --- | --- |
| `POST /v1/chat/completions` | Supported subset | Agent-backed chat completion. |
| `GET /v1/agents` | Supported | Lists loaded and discoverable Agent YAML files. |
| `GET /v1/capabilities` | Supported | Machine-readable compatibility declaration. |
| `/v1/responses` | Not supported | Use the Python SDK for richer agent workflows. |

## Choosing an agent

Pass a name from `GET /v1/agents` as `agent` or `model`. Loaded agents are addressed by their configured names (`agent.registered_name` in the SDK). Changing a display name preserves the lookup key. The host may also allow discovery of top-level `.yaml` and `.yml` templates through `agents_dir`. Request a template by its filename stem, such as `support` for `support.yaml`. The server reuses the engine instance registered under that template’s configured name. Filesystem paths and symbolic links leading outside the configured directory are excluded from discovery.

`axc serve --agent ./agents/support.yaml --host 127.0.0.1 --port 8000` binds the service to loopback. The CLI uses the preloaded template’s parent directory for discovery unless `--agents-dir` selects another directory.

## Request Parameters

Supported `POST /v1/chat/completions` parameters:

- `model`
- `agent`
- `messages`
- `stream`
- `session_id`
- `temperature`
- `max_tokens`
- `top_p`
- `stop`
- `presence_penalty`
- `frequency_penalty`
- `seed`
- `user`
- `response_format`
- `stream_options`

Unsupported parameters return `400` with `code=unsupported_parameter`.

Unsupported today:

- `n > 1`
- request-level `tools`
- request-level `tool_choice`
- unknown OpenAI parameters such as `logprobs`

## Tool Governance

Request-level tools are intentionally unsupported. Tools are loaded through
Agent YAML and plugins so the engine can apply:

- capability gates
- risk metadata
- plugin hooks
- audit events
- workspace policy
- command executor policy

This avoids letting an API caller bypass the Agent's configured tool governance
model.

## Streaming

When `stream=true`, the API returns `text/event-stream` chunks shaped like
`chat.completion.chunk`.

Supported stream option:

```json
{
  "stream": true,
  "stream_options": {"include_usage": true}
}
```

When enabled, a final usage chunk reports the cumulative counters from the completed run before `data: [DONE]`. A strategy can deliver its final answer in the terminal event. The API streams that text even if the strategy emitted no earlier text deltas.

A cancelled buffered run returns HTTP 499 with error code `run_cancelled`. When SSE headers have already been sent, cancellation emits a terminal error chunk with `axc_event.type="cancelled"` and the cancellation reason, followed by requested partial usage and the done marker.

## Error Shape

Errors use an OpenAI-style object:

```json
{
  "error": {
    "message": "Unsupported parameter: logprobs",
    "type": "invalid_request_error",
    "param": "logprobs",
    "code": "unsupported_parameter"
  }
}
```

## Capability Discovery

Use:

```bash
curl http://localhost:8000/v1/capabilities
```

Clients should inspect this route before assuming full OpenAI API parity.

## Runtime defaults

`runtime.max_rounds` defaults to **500** and accepts integers from 1 to
100000. The sessions plugin defaults are `max_depth=3`, `max_active=16`, and
`max_wakeups=100`; their accepted ranges are 1–20, 1–256, and 0–100000.
Out-of-range values fail Agent loading with `ConfigValueError`.

## SDK-only session lifecycle

The HTTP subset does not expose session fork or rewind. Python hosts use:

```python
branch = await agent.fork_run(run_id, checkpoint_id, metadata=identity)
await agent.rewind_session(session_id, before_run_id=run_id)
if await agent.can_rewind(session_id, run_id):
    ...
```

`fork_run` seeds the new session history from the selected cut. A failed fork
re-raises the original error with a `ForkFailure` attached; call
`fork_failure_of(error)` and then `await agent.discard_fork(failure)` after the
stores recover. Cleanup is idempotent and only removes the branch run/session.
`rewind_session` holds the session reset gate, calls `on_session_rewind` before
rewriting history, and does not delete checkpoints. Host metadata such as
`session_provenance` is not carried by the engine checkpoint; supply it on every
later `stream`, `resume_stream`, and `fork_run` call. A checkpoint without an
owner record can only be used by a caller without `tenant_id`/`user_id`; no
owner migration is provided. See [SESSIONS.md](SESSIONS.md) and
[ARCHITECTURE.md](ARCHITECTURE.md).
