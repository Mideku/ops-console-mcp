# CLAUDE.md

`AGENTS.md` is the source of truth for what this project is, build/test/smoke commands, the
non-negotiable invariants, and style conventions. Read it first. This file adds only the
Claude-Code-specific process on top.

## Subagent policy — token discipline

- **Writer/fixer subagents** (mechanical work: apply a diff, fix a build error, run a
  script) dispatch on an efficient model tier with capped reasoning effort. They do the
  work; they do not adjudicate whether it's correct.
- **Adversarial review runs on the main model**, not on a writer subagent. A subagent that
  wrote or fixed code is not the one that gets to clear it — cross-review by the primary
  model (or a dedicated reviewer agent) is mandatory before merge for anything touching
  `Tools/`, `Security/Redactor.cs`, `Audit/AuditLogger.cs`, `collector/collector.py`,
  `collector/snapshot_server.py`, or `.github/workflows/ci.yml`.
- **Every fan-out uses a structured-output schema**, no exceptions — see
  `.claude/skills/subagent-structured-output/SKILL.md`. An unschemad dispatch is output you'll
  have to re-parse by hand or re-run.
- Keep dispatches narrow: one subagent per file/concern, not one asked to "review the whole
  PR" — narrow prompts are cheaper to validate and cheaper to retry.

## Skills — when to use which

- `.claude/skills/release-gate/SKILL.md` — run before every commit/push: build, both test
  suites, smoke test, anti-leak grep, CI YAML sanity, staged-file check.
- `.claude/skills/adversarial-review/SKILL.md` — multi-lens review (security / contract /
  test) before committing a diff that touches a security- or contract-critical path.
- `.claude/skills/subagent-structured-output/SKILL.md` — required reading before writing any
  subagent dispatch prompt.
- `.claude/skills/big-file-surgery/SKILL.md` — reading/editing large generated or vendored
  files (e.g. `project.assets.json` under `obj/`) without a wasted whole-file read.
- `.claude/skills/record-owner-decision/SKILL.md` — recording an owner-level decision (new
  tool, contract change, credential/permission change, publication) in `docs/decisions.md`.

## Agents

- `.claude/agents/security-reviewer.md` — redaction, injection-via-tool-surface, read-only
  invariant, systemd hardening.
- `.claude/agents/contract-reviewer.md` — `SnapshotModels.cs` ↔ `collector.py` ↔ config
  example ↔ README field/type/enum consistency.
- `.claude/agents/build-fixer.md` — mechanical build-error fixes only, cheapest model tier.
