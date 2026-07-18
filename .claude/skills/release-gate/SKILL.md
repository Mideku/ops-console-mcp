---
name: release-gate
description: The exact pre-commit/pre-push gate for this repo, in order. Use before every commit and before every push — no partial runs, no skipping a step because "it's just a doc change" unless the diff really is docs-only (see the docs-only shortcut below).
---

# Release gate — exact order, exact commands, exact fail criteria

Run every step below, in order, before proposing a commit that touches `src/`, `collector/`,
`tests/`, `tools/`, or `.github/workflows/`. For a genuinely docs-only diff (README, this
skill directory, `docs/`), steps 1-3 can be skipped, but step 4 (anti-leak grep) and step 6
(no local artifacts staged) still run — docs are exactly where a real hostname or IP most
often gets pasted by accident.

## 1. C# build + test

```bash
dotnet restore OpsConsole.Mcp.sln
dotnet build OpsConsole.Mcp.sln --configuration Release --no-restore
dotnet test OpsConsole.Mcp.sln --configuration Release --no-build
```

Fail criterion: any error on build, any failed/errored test. Skipped tests are not a pass —
investigate why before proceeding.

## 2. Python collector unit tests

```bash
cd collector
python -m unittest discover -s tests -t .
```

Fail criterion: anything other than `OK` on the summary line. `ResourceWarning` noise from
socket cleanup in the test process is not a failure; an `ERROR`/`FAIL` line is.

## 3. stdio smoke test

```bash
python tools/smoke_stdio.py
```

Fail criterion: any exit code other than 0. This is the only step that proves the server
still completes the JSON-RPC handshake, still reports exactly the 8 declared tool names, and
still degrades gracefully (an `UPSTREAM_UNAVAILABLE` tool error, not a crash) when the
snapshot endpoint is unreachable. Run this even when steps 1-2 are green — it has caught
regressions neither unit suite exercises (transport wiring, tool registration).

## 4. Anti-leak grep

Search the staged diff for patterns that must never appear in a public repo: private IPs,
real hostnames, real usernames, or private project/process names.

```bash
git diff --cached | grep -inE \
  '(10\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}|172\.(1[6-9]|2[0-9]|3[01])\.[0-9]{1,3}\.[0-9]{1,3}|192\.168\.[0-9]{1,3}\.[0-9]{1,3}|100\.(6[4-9]|[7-9][0-9]|1[01][0-9]|12[0-7])\.[0-9]{1,3}\.[0-9]{1,3})|\.(local|internal|lan|home\.arpa)\b'
```

This mirrors the categories the redactor itself checks (`private_ip`, `internal_hostname` in
`src/OpsConsole.Mcp/Security/Redactor.cs`) applied to the diff instead of to tool output.
Fail criterion: any match outside a test fixture file (`tests/**`, `collector/tests/**`) or
`.gitleaks.toml`'s narrow, documented allowlist. A match in the diff body of `README.md`,
`AGENTS.md`, or any doc is a hard stop — placeholder values only
(`<tailnet-host>`, `my-app-prod`, `my-github-owner`, etc.), never a real one.

## 5. CI workflow sanity (only if `.github/workflows/ci.yml` changed)

```bash
python -c "import yaml, sys; yaml.safe_load(open('.github/workflows/ci.yml')); print('yaml OK')"
```

Fail criterion: parse error. Also re-read the diff for pinned action SHAs (this repo pins
every `uses:` to a commit SHA with a version comment) — an unpinned `uses: actions/x@v4` is a
regression, not a style nit.

## 6. No local artifacts staged

```bash
git status --porcelain | grep -E '\.jsonl$|audit\.log$|snapshot\.json$|collector\.config\.json$|\.local\.json$|\.smoke_audit\.jsonl$'
```

Fail criterion: any output. These are exactly the files `.gitignore` excludes because they
carry real, environment-specific runtime state (audit logs, the real collector config, a real
snapshot) — if one shows up staged, `.gitignore` was bypassed with `git add -f` or a filename
drifted outside the ignore patterns; stop and find out which before committing.

## Order matters

Run 1→2→3 before 4: a build/test failure means the diff isn't done yet, and there's no point
grepping an unfinished diff for leaks. Run 4 before 6: a leak in a file that then also
shouldn't be staged is still a leak worth finding first. Only propose the commit after all
applicable steps pass.
