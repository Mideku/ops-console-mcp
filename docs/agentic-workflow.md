# How this repo was built

This is a factual account of the process used to build ops-console-mcp with agent
assistance, kept here so the approach is reproducible rather than folklore. It intentionally
names no specific model or vendor beyond generic tiers ("smaller/larger models") and contains
no invented metrics — where a number would normally go, this describes what was checked
instead.

## Spec first, code second

Before any server or collector code was written, the shape of the system was decided up
front rather than negotiated after the fact: the stdio/HTTPS/collector architecture now
described in this README, the tool contract (parameter enums, caps, error codes for every
`infra_*`/`ci_*` tool), and the threat-model framing behind the "Contrast case" in
`README.md` (what a compromised or misled agent could still not do, and why). Deciding the
contract before the code meant the C# tool signatures and the Python collector's output shape
could be derived from the same intent instead of negotiated after the fact — the classic
failure mode in a two-language project is each side inventing its own version of "the
snapshot shape," and a settled contract closes that gap before it opens.

## Parallel writer subagents, schema-first

Independent pieces (a single tool's implementation, the redactor's pattern set, one collector
section, one test file) were dispatched to writer subagents running on smaller, cheaper
models, each given the relevant contract section and a strict output contract for what it
should return when done (files touched, a one-line summary, open questions). This is the
same discipline documented in `.claude/skills/subagent-structured-output/SKILL.md`: a subagent
without an explicit schema for its own report tends to produce prose that has to be re-read
and re-interpreted by whoever dispatched it, which defeats the point of parallelizing in the
first place.

## Adversarial cross-review before merge

Every writer subagent's output went through a review pass by a different, larger model before
being accepted — not the same model reviewing its own work. This caught real defects before
they reached the codebase, not hypothetical ones:

- A contract mismatch between the collector and the server, found not by static reading but
  by an actual integration run: the collector wrote a field the server's model didn't expect
  in the shape it arrived in, surfaced only when the two sides were run together rather than
  reviewed as text side by side. This is why `tools/smoke_stdio.py` exists as a standing
  check now — a reviewer reading both files can still miss a runtime-only mismatch that an
  end-to-end run catches immediately.
- A BOM (byte-order mark) silently present at the start of the audit log file, found by an
  external verifier reprocessing the file independently of this codebase rather than by a
  reviewer reading the C# writer code — the writer code looked correct in isolation; the
  defect only showed up when something outside the codebase tried to consume the file the way
  a real auditor would.

Each of these was fixed and re-verified before merge, not filed as a follow-up — a review that
finds a real defect and ships anyway is not a review.

## Governor pass

After cross-review, a final pass checked the diff against the original contract and
threat-model intent specifically (not general code quality) — did the implementation still
match what was decided up front, and did the threat model's claims ("no tool accepts a
free-form path," "every string is redacted") still hold against the code as merged, not as
originally designed. Corrections from this pass were narrow and targeted at closing that
specific gap, not a second general review pass.

## Token discipline

Model tiering by role was deliberate, not incidental: mechanical, narrow tasks (a single
tool's boilerplate, a build-error fix) ran on smaller models with reasoning effort capped low;
judgment-heavy tasks (security review, contract consistency, the governor pass) ran on larger
models. Every subagent dispatch used a schema-first output contract (see
`.claude/skills/subagent-structured-output/SKILL.md`) specifically because a failed structured
output forces the whole reasoning pass to be redone — avoiding that retry cost, not just
"using cheaper models," is what kept the overall process affordable at the depth of review
described above.
