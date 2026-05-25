# API Compatibility

AxcAgentEngine exposes an OpenAI Chat Completions compatible subset for hosts
that want to call an Agent over HTTP.

## Routes

| Route | Status | Notes |
| --- | --- | --- |
| `POST /v1/chat/completions` | Supported subset | Agent-backed chat completion. |
| `GET /v1/agents` | Supported | Lists loaded and discoverable Agent YAML files. |
| `GET /v1/capabilities` | Supported | Machine-readable compatibility declaration. |
| `/v1/responses` | Not supported | Use the Python SDK for richer agent workflows. |

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

When enabled, a final usage chunk is emitted before `data: [DONE]`.

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
