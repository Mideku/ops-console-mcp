# AGENTS.md — cross-tool agent guide for ops-console-mcp

This is the single source of truth for any coding agent (Claude Code, Cursor, or any other
tool, or a human) working in this repo. Tool-specific files (e.g. `CLAUDE.md`) add process on
top of this; they never override the invariants below.

## What this project is

A read-only MCP server (C#) plus a systemd-timer collector (Python) that exposes governed,
non-interactive infrastructure status — Docker service state, backup/deploy/job outcomes,
CI run status — to an MCP client, with no write path anywhere in the codebase.

## Build, test, smoke — exact commands

```bash
# C# server + tests (from repo root)
dotnet restore OpsConsole.Mcp.sln
dotnet build OpsConsole.Mcp.sln --configuration Release --no-restore
dotnet test OpsConsole.Mcp.sln --configuration Release --no-build

# Python collector unit tests (from collector/)
cd collector
python -m unittest discover -s tests -t .

# stdio protocol smoke test (from repo root; needs a built server)
python tools/smoke_stdio.py
# pass --dotnet <path> if `dotnet` isn't on PATH (e.g. a repo-local .dotnet/dotnet.exe)
```

`tools/smoke_stdio.py` starts the server with an unreachable snapshot URL and asserts: the
JSON-RPC handshake completes, `tools/list` returns exactly the 8 declared tool names, and a
`tools/call` against an unreachable upstream fails gracefully with `UPSTREAM_UNAVAILABLE`
instead of crashing the process. This is the fastest way to prove the server still boots and
still exposes only the intended tool surface after a change — run it after touching
`Tools/`, `Program.cs`, or the snapshot client.

Full pre-commit sequence: see `.claude/skills/release-gate/SKILL.md`.

## Non-negotiable invariants

1. **Absolute read-only.** No tool, code path, or configuration flag may perform a write,
   restart, exec, or mutation against Docker, systemd, GitHub, or the host. If a change adds
   a write capability of any kind, it does not belong in this repo — stop and raise it as an
   owner decision (`.claude/skills/record-owner-decision/SKILL.md`), do not implement it.
2. **Every externally-sourced string passes through the redactor.** Anything that originated
   outside this process — container logs, journal output, GitHub API text, error messages —
   must flow through `Security/Redactor.cs` (server side) or `collector/redactor.py`
   (collector side) before it reaches a tool result, an error message, or the audit log. No
   exceptions for "it's probably fine" fields.
3. **Tool names use underscores**, not dots or camelCase (`infra_list_services`, not
   `infra.listServices`), for client-pattern compatibility. New tools follow the existing
   `<domain>_<verb>_<noun>` shape and take no free-form string input — every parameter is a
   closed enum, a bounded integer, or a conservative regex (see the tool catalog in
   `README.md`).
4. **No environment-specific values are ever committed.** Real hostnames, IPs, Compose
   project names, repo owners, unit names, or paths only ever live in the gitignored
   `collector/collector.config.json` or in environment variables. Config templates
   (`collector/collector.config.example.json`) carry placeholders only. If you find yourself
   typing a real value into a tracked file, stop.
5. **The audit chain is never weakened.** Every tool call still appends exactly one
   hash-chained JSONL record; the hash-chain verification at startup still fails closed. Do
   not add a code path that skips logging a call, and do not change the log format without
   also updating the chain-verification logic and its tests in the same change.

## Style conventions

**C# (`src/`, `tests/`):** nullable-enabled, file-scoped namespaces, `sealed` classes unless
built for extension, `[JsonPropertyName]` snake_case on every wire-facing property (the
collector is a separate, non-.NET process — the wire format is the contract, not the CLR
name). Prefer `GeneratedRegex` over runtime-constructed `Regex`. Tests are MSTest-style under
`tests/OpsConsole.Mcp.Tests/`, one test class per source file under test.

**Python (`collector/`):** stdlib only, no third-party dependency (the target host has no
guaranteed pip). Every subprocess/network call happens inside a function body, never at
import time, so the module stays import-safe for local testing on any platform. One
try/except per external call site (one bad container/job must never abort the whole
collection) with a corresponding `errors[]` entry on failure. Tests are `unittest`-style
under `collector/tests/`.

## Repo structure

```
src/OpsConsole.Mcp/          C# MCP server (stdio transport)
  Tools/                     infra_*/ci_* tool implementations
  Security/Redactor.cs       centralized output sanitization
  Audit/AuditLogger.cs       hash-chained audit log
  RateLimiting/              per-session + global sliding-window limits
  Snapshot/                  snapshot client + wire-format models
tests/OpsConsole.Mcp.Tests/  C# test suite
collector/
  collector.py               reads Docker/systemd/GitHub state, writes snapshot.json
  redactor.py                collector-side redaction (mirrors Security/Redactor.cs)
  snapshot_server.py          minimal static file server for the snapshot
  collector.config.example.json   placeholder-only config template
  tests/                     Python unit tests
  systemd/                  service/timer unit templates ({{PLACEHOLDER}} tokens)
tools/smoke_stdio.py         end-to-end stdio protocol smoke test
.github/workflows/ci.yml     dotnet test + python unittest + gitleaks + mcp-scan
```

## Skills

Operational playbooks live in `.claude/skills/`; read the relevant one before doing the
matching work rather than improvising:

- `release-gate` — the exact pre-commit/pre-push gate for this repo.
- `adversarial-review` — multi-lens review for diffs touching security- or contract-critical
  paths.
- `record-owner-decision` — how and where to record an owner-level decision.
- `big-file-surgery` — bounded reads/edits for large files.
- `subagent-structured-output` — schema-first contracts for any subagent fan-out.
