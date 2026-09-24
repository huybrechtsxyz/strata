# Architectural Decision Records

This directory contains the Architectural Decision Records (ADRs) for strata-v2,
using the [MADR](https://adr.github.io/madr/) format.

An ADR captures **one** significant, point-in-time decision — the problem that
forced it, the alternatives considered, and why one was chosen. It exists so
the rationale survives beyond the author. An ADR is a historical record, not a
progress tracker: once written, its Context/Decision/Consequences don't
change. If reality later diverges (implementation status, follow-on work,
"how this currently works day to day"), that lives in
[`docs/design/`](../design/README.md), not by editing the ADR.

**Existing files `0001`–`0023` predate this tightened convention** and mix
several decisions, phase trackers, and in-place `- Revised:` notes into single
files. They are grandfathered as-is — don't rewrite them retroactively. Follow
the rules below for new ADRs; if you're touching an old one substantially,
prefer splitting new decisions out into their own ADR rather than adding to it.

## One decision per ADR

If you catch yourself writing "Decision 1", "Decision 2", ... "Decision 6" in
a single file, or the file describes multiple independently-arguable choices,
split it into multiple ADRs and cross-link them with `Related:`. A reader
should be able to link to one ADR number and know exactly which decision that
refers to.

## Decisions don't get revised in place

An accepted ADR's content is immutable. If a later change alters or replaces
an earlier decision:

1. Write a **new** ADR (`NNNN-title.md`) describing the new decision, with a
   `Related:` line pointing back to the old one.
2. Update the old ADR's `- Status:` line to `superseded — see ADR-NNNN` (a
   one-line status edit is fine; do not rewrite its body).

Don't add `- Revised:` lines that patch an old decision's narrative — that's
what a new ADR is for.

## Index

There is no hand-maintained index table here. The files in this directory **are**
the index:

- Browse `docs/decisions/*.md` directly, sorted by number (`000N-title.md`).
- Each file's own `- Status:` line (near the top) is the source of truth for that
  ADR's status.
- To find ADRs by topic, search file names/titles or grep for keywords across the
  directory.

## Adding a new ADR

1. Copy the template below into `docs/decisions/NNNN-title-with-dashes.md`, where
   `NNNN` is the next unused number (check the directory listing).
2. Fill in the sections. Remove optional sections you don't need.
3. That's it — no index table to update.
4. If the decision involves ongoing build-out, phases, or a component whose
   design will keep evolving after this decision, also create/update a
   matching doc in `docs/design/` (see [docs/design/README.md](../design/README.md))
   and link it from this ADR's `Related:` line. Keep the ADR itself focused on
   the decision, not the build progress.

## Status values

The `- Status:` line (always the first line under the title) must use exactly one
of these values:

| Value                   | Meaning                                                                |
| ----------------------- | ------------------------------------------------------------------------ |
| `proposed`              | Decided in principle; no implementation started                        |
| `accepted`              | Decision finalized; may not require code (e.g. a policy/inventory ADR) |
| `in-progress`           | Actively being built, nothing usable shipped yet                       |
| `partially-implemented` | Some of the decision is built and in use; some is not                  |
| `implemented`           | Fully built — nothing pending                                          |
| `deferred`              | Intentionally not being worked on right now                            |
| `superseded`            | Replaced by another ADR — do not implement this one                    |
| `rejected`              | Considered and declined                                                |

A short clarifying note may follow after an em-dash, e.g.
`- Status: partially-implemented — Phase 1 done, Phase 2 not started`.

**Any ADR whose status is `proposed`, `in-progress`, or `partially-implemented` must
have a `## Remaining Work` section** listing what's left. `implemented`, `deferred`,
`superseded`, and `rejected` ADRs don't need one.

Keep `## Remaining Work` short (a handful of bullets). If the remaining work
is substantial enough to need phases, a checklist that gets updated over
several sessions, or its own status narrative, that's a sign it belongs in a
`docs/design/` doc instead — link to it here rather than growing this section
into a tracker.

### Minimal template

```markdown
# {Short title — what was decided}

- Status: proposed
- Date: YYYY-MM-DD

## Context and Problem Statement

{What forced this decision?}

## Considered Options

- Option A
- Option B

## Decision Outcome

Chosen: **Option A**, because {one-line justification}.

### Consequences

- Good: {positive effect}
- Bad: {trade-off or cost}

## Remaining Work

<!-- Required while Status is proposed / in-progress / partially-implemented.
     Remove this section once Status becomes implemented. -->

- Not started — nothing in this ADR has been implemented yet.
```
