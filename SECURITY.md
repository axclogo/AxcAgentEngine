# Security Policy

## Supported Versions

Security fixes are applied to the latest released minor version unless a release
note says otherwise. Pre-1.0 APIs may still change, but security fixes are
treated as high priority.

## Reporting a Vulnerability

Do not open a public issue for a suspected vulnerability.

Report security issues by emailing the maintainers listed in the project
repository, or by using GitHub private vulnerability reporting when it is
enabled for the repository.

Please include:

- Affected version or commit.
- Minimal reproduction steps.
- Impact assessment.
- Whether the issue requires a malicious prompt, malicious tool output, or
  untrusted configuration.
- Any logs with secrets removed.

## Security Model

AxcAgentEngine is an execution engine. It can run tools, call models, read and
write files, call HTTP endpoints, execute commands, and load plugins depending
on Agent YAML and host configuration. Treat Agent YAML, plugins, skills, MCP
servers, and tool configuration as trusted code/configuration unless your host
application adds its own review and sandboxing layer.

High-risk capabilities include:

- `shell`
- `python_exec`
- `pip_install`
- `file_write`
- `http_request`
- `memory_write`
- `memory_delete`
- `agent_call`

Tools with a non-empty capability are denied unless that capability is listed in
`runtime.allowed_capabilities` or a custom `PolicyEvaluator` allows it.

## Production Guidance

- Use a dedicated workspace directory per tenant/user/session when tools can
  access files.
- Inject a production `CommandExecutor` backed by Docker, nsjail, Firecracker,
  or another strong sandbox for command/Python execution.
- Do not expose Agent YAML editing to untrusted users without review.
- Restrict outbound HTTP with allowlists when possible.
- Treat MCP servers as trusted executables.
- Store secrets outside Agent YAML and expand them in host code.
- Use persistent audit sinks and checkpoint stores for regulated environments.

## Dependency Security

CI runs dependency auditing. Hosts should still audit optional dependencies used
in their deployment, especially `api`, `knowledge`, MCP, and sandbox-related
extras.
