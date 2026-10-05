# Architectural Decision Records

This directory contains the Architectural Decision Records (ADRs) for strata-v2,
using the [MADR](https://adr.github.io/madr/) format.

An ADR captures **one** significant, point-in-time decision — the problem that
forced it, the alternatives considered, and why one was chosen. It exists so
the rationale survives beyond the author. An ADR is a historical record, not a
progress tracker and not a design doc: once written, its Context/Decision/
Consequences don't change, and it never describes implementation status,
phase progress, or day-to-day "how this currently works." That content lives
in [`docs/design/`](../design/README.md) (once finished) or
[`docs/work/`](../work/README.md) (while in progress) — never in the ADR
itself. Don't add a `- Revised:` line patching an old decision's narrative,
and don't add a `## Remaining Work` tracker — see "Decisions don't get
revised in place" and "No build-progress tracking" below.

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
what a new ADR is for. If you're just narrating how a design evolved day to
day (not a distinct new decision), that narrative belongs in a
[`docs/work/`](../work/README.md) doc instead, not in the ADR at all.

## No build-progress tracking in an ADR

An ADR records that a decision was made, not whether it's been built yet.
Never add a `## Remaining Work` section, a phase checklist, or an
implementation-status narrative to an ADR. Track that in:

- [`docs/design/`](../design/README.md), once the feature is fully built and
  nothing is pending — a living doc describing how it works today.
- [`docs/work/`](../work/README.md), while it's still being built, designed,
  or debated — phases, open questions, in-progress narrative.

An ADR with real follow-on work should link out via `Related:` to whichever
of those two has it, instead of listing it itself.

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
   design will keep evolving, create/update a matching doc in
   [`docs/work/`](../work/README.md) (while it's in progress) and link it
   from this ADR's `Related:` line. Once that build-out finishes with
   nothing pending, the doc graduates to [`docs/design/`](../design/README.md)
   instead. Keep the ADR itself focused on the decision, never the build
   progress.

## Status values

The `- Status:` line (always the first line under the title) must use exactly one
of these decision-lifecycle values — never a build-progress value like
"implemented" or "partially-implemented"; whether something has been built
is tracked in `docs/design/`/`docs/work/`, not on the ADR:

| Value        | Meaning                                                                 |
| ------------ | ----------------------------------------------------------------------- |
| `proposed`   | Decided in principle, open for discussion; not yet final                |
| `accepted`   | Decision finalized                                                      |
| `rejected`   | Considered and declined                                                 |
| `deprecated` | No longer in effect, but not replaced by a specific other ADR           |
| `superseded` | Replaced by another ADR — see its `- Status: superseded — see ADR-NNNN` |

A short clarifying note may follow after an em-dash, e.g.
`- Status: accepted — see ADR-0031 for the follow-up extension`.

An ADR never has a `## Remaining Work` section. If there's follow-on work,
link to the `docs/work/` (or, once finished, `docs/design/`) doc that tracks
it via `Related:` instead.

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
```
