# OpsConsole.Mcp

Read-only MCP server exposing `infra_*` and `ci_*` tools over stdio. See the spec pack
(`00-README-spec.md`, `01-architettura.md`, `02-contratto-tool.md`, `03-sicurezza-threat-model.md`)
at the repository root for the full design and threat model. This server never talks to
Docker, systemd or GitHub directly: it only reads a pre-generated, already-redacted JSON
snapshot published by a separate collector process (see `01-architettura.md`).

## Configuration (environment variables)

| Variable | Required | Default | Meaning |
|---|---|---|---|
| `OPS_CONSOLE_SNAPSHOT_URL` | yes | — | Absolute URL of the read-only snapshot endpoint (collector output). |
| `OPS_CONSOLE_SNAPSHOT_TOKEN` | no | — | Optional bearer-style token sent as `X-Snapshot-Token` when fetching the snapshot. |
| `OPS_CONSOLE_AUDIT_PATH` | no | `./audit.jsonl` | Path to the append-only, hash-chained audit log. |
| `OPS_CONSOLE_RATE_PER_MINUTE` | no | `30` | Per-session call budget per minute (a global, process-wide budget scales with CPU count on top of this). |

## Startup behavior

On startup the server verifies the last 100 records of the audit log chain. If the chain is
broken, it prints a diagnostic to stderr and exits with a non-zero code (fail-closed): it will
never start serving tool calls on top of a log whose integrity cannot be established.

## Notes on the `stale` field

Every successful tool response includes a `stale: bool` field, computed from the snapshot's
`generated_at` and `stale_after_seconds`. This is intentionally additive to the payload shapes
documented in `02-contratto-tool.md` (which predate the stale-detection requirement): the
contract's `additionalProperties: false` schemas describe the tool's own domain fields, and
`stale` is treated as transport-level metadata about freshness of the underlying data, always
present so a stale value is never silently presented as fresh.

## Running

```bash
export OPS_CONSOLE_SNAPSHOT_URL="http://127.0.0.1:8080/snapshot.json" # example only, replace with your tailnet URL
dotnet run --project src/OpsConsole.Mcp
```

Configure your MCP client (e.g. Claude Code) to launch this as a stdio server command.
