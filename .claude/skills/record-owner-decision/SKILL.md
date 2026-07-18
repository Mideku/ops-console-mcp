---
name: record-owner-decision
description: Record an owner-level decision (new tool, contract change, credential/permission change, publication) into docs/decisions.md. Use when the owner states a decision in chat, or when a design question genuinely can't be resolved without one — never invent a decision on the owner's behalf.
---

# Record owner decision

## What needs an owner decision (not an implementation choice)

- Adding a new tool, or widening what an existing tool returns/accepts.
- Any change to the wire contract (`SnapshotModels.cs` ↔ `collector.py` field shapes) that
  isn't a pure rename kept in sync across both sides.
- Anything touching credential scope: the GitHub PAT's permissions, the snapshot bearer
  token, the collector service account's capabilities.
- Publishing or changing what's public about the project (README claims, security posture
  statements, the threat-model contrast case).

Ordinary implementation choices (which regex engine, how to structure a helper function,
test naming) are not owner decisions — don't file those here.

## Rule: verbatim text only, never invented

If the owner hasn't actually stated a decision, do not write one. Record the open question
in `docs/decisions.md` with a status of `pending` and stop — recording a pending entry
authorizes nothing. Never infer a decision from silence, from "seems reasonable," or from
what a previous entry decided in an unrelated case.

## Format — one entry per decision in `docs/decisions.md`

```markdown
## D-<NNN>: <short title>

- **Date:** YYYY-MM-DD
- **Context:** <why this came up — 1-3 sentences>
- **Options considered:** <bullet list, brief>
- **Decision:** <verbatim owner choice, or "pending" if not yet decided>
- **Consequences:** <what this rules in/out going forward>
```

Number `D-NNN` sequentially; never renumber or delete a past entry (append corrections as a
new entry that references the old one by id instead).

## Steps

1. Read `docs/decisions.md` (it's short — a normal `Read` is fine, no big-file-surgery
   needed) to find the next free `D-NNN` and confirm you're not duplicating an existing
   entry.
2. Append the new entry using the format above at the end of the file.
3. If the decision changes something already documented (README tool catalog, `AGENTS.md`
   invariants), update that document in the same commit — a decision recorded but not
   reflected in the artifact it governs is worse than not recording it, since it creates two
   contradictory sources.
4. Report back: decision id, one-line summary, and which other files (if any) were updated
   to reflect it.
