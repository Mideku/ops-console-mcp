# Owner decisions

One entry per decision that required an explicit owner call (new tool, contract change,
credential/permission change, publication). See
`.claude/skills/record-owner-decision/SKILL.md` for the format and process. Entries are
appended in order and never renumbered or deleted; a later change to an earlier decision is
a new entry that references the old one.

## D-001: Use underscores in tool names, not dots or camelCase

- **Date:** 2026-07-18
- **Context:** The MCP tool catalog needed a consistent naming convention across the 8
  `infra_*`/`ci_*` tools before the server shipped its first version. Different MCP clients
  and prior art in the ecosystem use inconsistent separators (`infra.listServices`,
  `infraListServices`, `infra_list_services`).
- **Options considered:**
  - Dot-separated namespacing (`infra.list_services`) — reads well but not universally
    accepted by client-side tool-name validation patterns.
  - camelCase (`infraListServices`) — common in the broader API-naming world but inconsistent
    with the snake_case wire format already used for the snapshot JSON.
  - Underscore-separated, flat (`infra_list_services`) — matches the snapshot wire format's
    snake_case convention and is accepted by the widest set of client tool-name patterns.
- **Decision:** All tool names use underscores in a flat `<domain>_<verb>_<noun>` shape
  (`infra_list_services`, `ci_get_latest_run`, etc.), matching the snake_case already used on
  the collector-to-server wire format.
- **Consequences:** Every new tool follows this convention. The 8 tools listed in
  `tools/smoke_stdio.py`'s `EXPECTED_TOOLS` set and in the README's tool catalog table are
  the enforced ground truth for the current tool set — a mismatch between any of these three
  places is a bug, not an intentional variance.

## D-002: Optional `runner_repo` config key for self-hosted runner status

- **Date:** 2026-07-18
- **Context:** `ci_get_runner_status` returned an empty (but correct) runner list: the
  monitored repository runs its workflows on hosted runners, while the actual self-hosted
  runner on the monitored host is registered to a *different* repository under the same
  owner. Showing the real runner requires querying that other repository.
- **Options considered:**
  - Leave as-is — honest data, but the tool never shows the self-hosted runner that runs on
    the very host this server monitors.
  - Point the whole `github` config at the runner's repository — loses the primary
    repository's workflow-run history.
  - Add an optional `github.runner_repo` key used only for the runners endpoint, falling
    back to `github.repo` when absent — run history and runner status each come from the
    repository where they actually live.
- **Decision (owner):** Add the optional `runner_repo` key. The fine-grained PAT's
  repository access is widened by the owner to include the runner's repository, with
  permissions unchanged (Actions, Contents, Administration — all read-only).
- **Consequences:** One PAT covers both repositories at the same read-only permission
  level; the snapshot's `ci.runners` reflects the host's real runner while `recent_runs`
  stays scoped to the primary repository. Config example and collector README document the
  key as optional.
