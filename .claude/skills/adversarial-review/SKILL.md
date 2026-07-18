---
name: adversarial-review
description: Multi-lens review (security / contract / test) for a diff before it's committed. Use for any change touching Tools/, Security/Redactor.cs, Audit/AuditLogger.cs, RateLimiting/, collector/collector.py, collector/snapshot_server.py, collector/redactor.py, or .github/workflows/ci.yml. Skip for docs-only or config-comment changes.
---

# Adversarial review — three lenses, one pass

This repo's entire value proposition is "an agent cannot make this server do anything but
read." A single reviewer pass tends to rubber-stamp the diff's own framing. Run all three
lenses below before proposing a commit; do not skip a lens because the diff "looks small."

## Lens 1 — security

- **Redaction.** Does every new externally-sourced string (a log line, a journal entry, a
  GitHub API field, an error message) pass through `Security/Redactor.cs` or
  `collector/redactor.py` before it can reach a tool result, an error payload, or the audit
  log? A new field that bypasses the redactor is a leak waiting for the right input.
- **Tool description as injection surface.** MCP tool/parameter descriptions are themselves
  read by the client and, in some clients, by the model driving the session. A new or edited
  tool description must describe behavior only — no embedded instructions, no "ignore
  previous constraints," no second-order phrasing that reads differently to a model than to
  a human. Treat tool descriptions with the same suspicion as untrusted input, because from
  the model's perspective that's what they are.
- **Read-only invariant.** Does the diff add a code path that shells out, calls a mutating
  Docker/GitHub API, writes a config file, or accepts a parameter that could be concatenated
  into a command or file path? If the diff adds *any* capability beyond returning data already
  computed by the collector, stop and treat it as a new-tool owner decision
  (`.claude/skills/record-owner-decision/SKILL.md`) rather than merging it.
- **Systemd hardening (collector-side changes only).** If `collector/systemd/*` changed,
  confirm the sandboxing directives (`NoNewPrivileges`, `ProtectSystem=strict`, `PrivateTmp`,
  `MemoryDenyWriteExecute`, the seccomp filter, minimal `CapabilityBoundingSet`) are still
  present and that no new directive silently widens the service account's reach (e.g.
  flipping `ProtectHome` from `read-only` to `false`).

## Lens 2 — contract

Check these four artifacts agree on every field touched by the diff: name, JSON wire name
(`snake_case`), type, and enum values.

- `src/OpsConsole.Mcp/Snapshot/SnapshotModels.cs` (`[JsonPropertyName]` attributes)
- `collector/collector.py` (the dict keys it writes into `snapshot.json`)
- `collector/collector.config.example.json` (config keys the collector reads)
- `README.md` (tool catalog table + environment variable table)

A field renamed in one and not the others is a silent runtime break: the C# side will either
throw, silently default, or (worse) deserialize into the wrong shape. This is the single most
common regression class in a two-language, wire-format-coupled repo — treat any mismatch as a
blocker, not a nit.

## Lens 3 — test

- Both suites green: `dotnet test OpsConsole.Mcp.sln` and (from `collector/`)
  `python -m unittest discover -s tests -t .`.
- `python tools/smoke_stdio.py` still passes — this is the only check that exercises the
  actual JSON-RPC handshake and the exact tool-name set, not just unit-level behavior.
- Any new tool, enum value, or redaction pattern has a corresponding test added in the same
  diff — a behavior change without a test is itself a finding, not something to wave through
  because "it's obviously right."

## Disposition

Findings from any lens that touch the read-only invariant or a redaction gap are blockers:
fix before commit, no exceptions. Contract mismatches are blockers if they'd break
deserialization, otherwise fix-before-commit as good hygiene. Report findings inline against
the diff (file:line, what's wrong, what lens found it) rather than as a separate report file.
