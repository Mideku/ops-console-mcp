---
name: subagent-structured-output
description: Author every subagent dispatch (reviewer, fixer, verifier) with a schema-first output contract so the call validates on the first try. Use whenever spawning a subagent expected to return anything other than free prose — a review, a build-fix report, a contract-check result.
---

# Schema-safe subagent dispatch

## Why this matters

A subagent given only a prose description of the expected output ("report what you found,
with a severity") will improvise the shape: it omits a key, nests where a flat list was
expected, or invents an enum value close to but not equal to the one the caller checks for.
Every failed structured-output call has to be regenerated from scratch — the model re-does
the full reasoning pass and pays for it twice, once for the failed attempt and once for the
retry. Treat schema mismatches as a cost bug, not a formatting nitpick.

## Rule 1 — end every dispatch prompt with an explicit output contract

State the exact keys, their types, and any enum values, then give one filled example. This
repo's schema shape for a review finding:

```
OUTPUT CONTRACT: respond with EXACTLY this JSON shape (all keys required, no extra keys):
{"file": "<path>", "line": <int>, "severity": "blocker"|"major"|"minor"|"nit",
 "summary": "<one sentence>", "evidence": "<file:line or command output backing the claim>"}
If there are no findings, return an empty array, not an omitted field.
Example: {"file": "src/OpsConsole.Mcp/Security/Redactor.cs", "line": 132,
 "severity": "major", "summary": "New CIDR range not covered by PrivateIpRegex.",
 "evidence": "tests/OpsConsole.Mcp.Tests/RedactorTests.cs has no case for it"}
```

An array that's allowed to be empty must say so explicitly — otherwise models either invent
a placeholder finding to have something to report, or drop the key entirely.

## Rule 2 — design the schema for first-try success

- `required` lists only the keys downstream code actually reads. Don't require fields you
  won't consume; every required-but-unused field is a chance to fail validation for no
  payoff.
- Prefer `additionalProperties: true` unless a downstream parser does an exact key-set match
  — if you do set it to `false`, say so in the same OUTPUT CONTRACT block ("no extra keys").
- Keep arrays flat and objects shallow (two levels, not four) — nesting depth multiplies the
  ways a model can misplace a value.
- Use the identical enum strings in the schema and in the prompt text. A prompt that says
  "high/med/low" against a schema enum of `high|medium|low` is a guaranteed first-try miss.

## Rule 3 — mechanics specific to this repo

- Give every subagent **absolute paths** — a subagent's working directory is not guaranteed
  to match the caller's, so a relative path like `collector/redactor.py` can resolve to
  nothing. Use `<repo-root>/collector/redactor.py`.
- Reviewer/verifier subagents should run read-only (no write tools) — see
  `.claude/agents/security-reviewer.md` and `.claude/agents/contract-reviewer.md` for the
  pattern: `tools: Read, Grep, Glob, Bash` (Bash for running tests/greps, never for editing).
- Fixer subagents (e.g. `.claude/agents/build-fixer.md`) get a narrower contract: what file
  they changed, what error they fixed, and a one-line diff summary — not a full narrative.
