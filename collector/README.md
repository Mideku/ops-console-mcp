# Collector

Read-only data collection for ops-console-mcp. Runs on the monitored Linux
host and produces a single redacted JSON snapshot consumed by the MCP
server (see `../01-architettura.md` and `../02-contratto-tool.md`).

Two independent scripts, both Python 3.12 stdlib only (no pip dependency):

- `collector.py` — one-shot: reads Docker Compose/container state, the
  backup systemd unit's journal, the log+exit files of a static whitelist
  of ops scripts (`deploy`, `restore-test`, `prepush-proof`), and
  optionally the GitHub Actions API, then writes `snapshot.json`
  atomically. Meant to run periodically via `systemd/ops-console-collector.timer`.
- `snapshot_server.py` — a minimal `http.server`-based file server that
  serves only `GET /snapshot.json`. Refuses to start if bound to
  `0.0.0.0` or an empty address; intended to be bound to a Tailscale IP
  only.

Neither script performs any mutating action against Docker, systemd, or
GitHub — this is enforced structurally, not by a runtime check (see
`../03-sicurezza-threat-model.md` section 4).

## Files

- `redactor.py` — shared secret-redaction function (`redact(text) -> str`)
  used by both scripts on every piece of text before it is written to the
  snapshot.
- `collector.config.example.json` — template config. Copy it to
  `collector.config.json` (gitignored) and fill in real values; never
  commit the real file.
- `systemd/ops-console-collector.service` + `.timer` — unit templates
  (placeholders `{{SERVICE_USER}}`, `{{INSTALL_DIR}}`, `{{CONFIG_PATH}}`,
  `{{SNAPSHOT_DIR}}`) with the hardening directives described in
  `../03-sicurezza-threat-model.md` section 8.4.
- `systemd/ops-console-snapshot.service` — unit template for the file
  server.
- `tests/test_collector.py` — unittest suite covering the redactor,
  job log/exit parsing, snapshot assembly (with mocked sources), and the
  snapshot server's bind-address and auth checks.

## Install (summary)

1. Create a dedicated, non-interactive service user on the host, member
   of the `docker` group (required to read container state — this is
   root-equivalent, see the threat model doc; no other privilege is
   granted).
2. Copy this directory to the host (e.g. `/opt/ops-console-mcp/collector`).
3. Copy `collector.config.example.json` to `collector.config.json` next
   to it, and fill in the real compose project names, backup unit name,
   job file paths, GitHub owner/repo/workflow file names, output path,
   and (for the snapshot server) the Tailscale bind address and port.
   This file is not committed to the repository.
4. Render the systemd unit templates (replace `{{SERVICE_USER}}`,
   `{{INSTALL_DIR}}`, `{{CONFIG_PATH}}`, `{{SNAPSHOT_DIR}}`, `{{OWNER}}`,
   `{{REPO}}`) and install them under `/etc/systemd/system/`.
5. `systemctl daemon-reload && systemctl enable --now ops-console-collector.timer ops-console-snapshot.service`.

## Environment variables

- `OPS_CONSOLE_GH_PAT` — GitHub fine-grained personal access token with
  `actions:read` + `contents:read` scope, limited to the single monitored
  repository. Read by `collector.py` only. If unset, the `ci` section of
  the snapshot stays `runner_status: "unknown"` and an entry is added to
  `errors[]`.
- `OPS_CONSOLE_SNAPSHOT_TOKEN` — optional static token for
  `snapshot_server.py`. If set, every request must include a matching
  `X-Snapshot-Token` header, otherwise the server responds `401`.

## Running the tests

```
cd collector
python -m unittest discover -s tests -t .
```
