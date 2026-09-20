# Architectural Decision Records

This directory contains the Architectural Decision Records (ADRs) for strata-v2,
using the [MADR](https://adr.github.io/madr/) format.

An ADR captures a significant design choice — what was decided, what alternatives
were considered, and why. They exist so the rationale survives beyond the author.

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
