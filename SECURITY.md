# Security Policy

## Supported versions

Only the `main` branch is supported. There are no maintained release branches; fixes land on
`main` and older commits are not backported.

## Reporting a vulnerability

Please report suspected vulnerabilities using [GitHub's private vulnerability reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing/privately-reporting-a-security-vulnerability)
on this repository, rather than filing a public issue. This lets the report be triaged before
any details become public.

## Principles

- **No write tools, by construction.** There is no code path anywhere in this server that
  restarts a container, executes a command, triggers a CI run, or writes to any external system.
  This is not a runtime check applied to an otherwise general-purpose tool — the write paths
  were never implemented. Reports proposing to "harden" a write capability that could exist are
  out of scope; any actual write path found in the code is treated as a critical bug.
- **Redaction as defense-in-depth.** Secret redaction is applied independently at multiple
  points (collector output before it is ever written to disk, and again centrally on every
  string a tool returns), not as a single gate that a bypass could defeat entirely.
- **Audit trail.** Every tool invocation is recorded synchronously in a hash-chained,
  append-only log before the response reaches the client, so a compromise or a malfunctioning
  agent leaves durable, tamper-evident evidence.
- **Tool descriptions are treated as code.** The text exposed to an agent via `tools/list` is
  reviewed with the same rigor as the implementation, not as incidental documentation, because
  it is itself a prompt-injection surface for any agent (or downstream MCP client) that reads it
  before ever calling a tool.
