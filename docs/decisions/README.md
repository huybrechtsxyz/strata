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

### Minimal template

```markdown
# {Short title — what was decided}

- Status: accepted
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
