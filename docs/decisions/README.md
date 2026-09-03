# Architectural Decision Records

This directory contains the Architectural Decision Records (ADRs) for strata,
using the [MADR](https://adr.github.io/madr/) format.

An ADR captures a significant design choice — what was decided, what alternatives
were considered, and why. They exist so the rationale survives beyond the author.

## Index

There is no hand-maintained index table here — it always drifted out of sync with
the actual files (missing entries, stale statuses) and nobody caught it until it was
audited directly against the files themselves. The files in this directory **are**
the index:

- Browse `docs/decisions/*.md` directly, sorted by number (`000N-title.md`).
- Each file's own `- Status:` line (near the top) is the source of truth for that
  ADR's status — not a copy of it maintained elsewhere.
- To find ADRs by topic, search file names/titles or grep for keywords across the
  directory — there are too few readers of this list for a table to pay for its own
  upkeep.

## Adding a new ADR

1. Copy the template below into `docs/decisions/NNNN-title-with-dashes.md`, where
   `NNNN` is the next unused number (check the directory listing, not a table).
2. Fill in the sections. Remove optional sections you don't need.
3. That's it — no index table to update. `decisions/` is intentionally excluded
   from the Sphinx `docs/index.rst` toctree and from the docs-index-coverage check
   in `scripts/Check.ps1` (see its `$excludeTopDirs`), so there is no second place
   to register a new ADR.

## Introducing a new convention

ADR-0073 exists because several embedded-string conventions (`@repo_name/...`,
`./`/`../`, `field op value`, bare YAML rule strings) were each added independently,
without anyone checking whether something equivalent already existed — resulting in
duplicated detection/parsing logic scattered across multiple files that quietly
drifted out of sync with each other. Before introducing a new syntax, field shape,
naming scheme, or other repeated pattern, apply these three checks:

1. **Write an ADR.** Any new convention — a string syntax, a naming rule, a YAML
   field shape, a resolution algorithm — needs an ADR recording what was decided and
   why, even if it's short. This is what makes the next person's "does something
   like this already exist?" search possible at all.
2. **Prefer reuse over invention.** Before designing something new, check whether an
   existing strata mechanism already does the job (grep `docs/decisions/` and the
   relevant `models`/`utils` modules first) or whether an industry-standard format
   already fits (e.g. JMESPath instead of a bespoke query mini-language, standard
   regex instead of a hand-rolled glob variant). Only invent a new convention when
   both come up empty, and say so explicitly in the ADR's "Considered Options".
3. **One implementation, not copies.** If the same parsing/detection/evaluation
   logic is needed in more than one place, it lives in exactly one shared
   function/class that every call site imports — never copy-pasted or
   independently reimplemented per call site, even with slightly different
   variable names. Reviewers should treat a second hand-rolled copy of existing
   logic as a bug, not a style nitpick.

## Status values

The `- Status:` line (always the first line under the title) must use exactly one
of these values, so it stays greppable instead of drifting into free-text phrasing
like "phase 1 implemented" or "completed (mostly)":

| Value                   | Meaning                                                                |
| ----------------------- | ---------------------------------------------------------------------- |
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
have a `## Remaining Work` section** listing what's left, so an agent or reviewer can
find open work with a single grep for that heading instead of reading the whole
document. `implemented`, `deferred`, `superseded`, and `rejected` ADRs don't need one
— there's nothing pending to enumerate (superseded/rejected point at whatever
replaced them instead).

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
