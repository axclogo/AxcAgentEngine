# Security Model

AxcAgentEngine is safe by default only when hosts keep high-risk capabilities
disabled. Enabling tools gives the model access to host-defined actions.

## Capability Gates

Tools with a non-empty `capability` are denied unless the capability is listed
in Agent YAML:

```yaml
runtime:
  allowed_capabilities:
    - file_read
    - file_write
```

Hosts can also inject a custom `PolicyEvaluator`.

## Workspace Boundary

File and command tools should run with a configured `runtime.workspace`.
Workspace validation prevents basic path traversal outside that directory.

## Command Execution

`LocalSubprocessExecutor` is a development baseline, not a strong sandbox. For
production, inject a stronger `CommandExecutor` using Docker, nsjail,
Firecracker, or a platform sandbox.

## HTTP Requests

Built-in HTTP tools reject local, private, link-local, reserved, and metadata
addresses. Hosts should still add explicit network allowlists for production.

## MCP

MCP servers are external programs or remote services. Treat them as trusted
configuration. Do not allow untrusted users to configure MCP commands or URLs.

## Memory and Knowledge

Memory and knowledge plugins can persist or retrieve sensitive data. Use
tenant/user/session scoping and production-grade stores when deploying beyond a
single-user local process.
