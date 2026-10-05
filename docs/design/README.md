# Design Docs

This directory holds **finished** design docs for strata-v2 components and
features — how something works, fully built, with nothing pending — as
distinct from [`docs/decisions/`](../decisions/README.md), which records
point-in-time architectural decisions that don't change once written, and
[`docs/work/`](../work/README.md), which holds everything still in progress
(phase checklists, open questions, drafts).

**Start here:** [v2-schema-overview.md](v2-schema-overview.md) — a one-page
catalog of every v2 kind and its current status, linking out to the ADR and
(where one exists) the deeper design or work doc for each.

## Design doc vs. ADR vs. work doc

|                                | ADR (`docs/decisions/`)       | Design doc (`docs/design/`)                                      | Work doc (`docs/work/`)                       |
| ------------------------------ | ----------------------------- | ---------------------------------------------------------------- | --------------------------------------------- |
| Answers                        | "Why did we choose X over Y?" | "How does X work, now that it's finished?"                       | "Where are we on X, and what's still open?"   |
| Lifespan                       | Immutable once accepted       | Edited in place, but only while still accurate — nothing pending | Living — edited constantly as work progresses |
| Scope                          | One decision                  | One finished component/feature                                   | One in-progress component/feature             |
| Contains phases/open questions | No                            | **No — move it out once it's done**                              | Yes, this is the right place                  |

A design doc never has unfinished phases, open design questions, or a
"Remaining Work" list in its main body. If a doc has any of those, it
belongs in [`docs/work/`](../work/README.md) instead — once the remaining
items are actually done, move the whole doc here.

A design doc *may* note a deliberately-**out-of-scope** item in a short line
(e.g. "X is not supported; revisit if a real consumer needs it") — that's a
decided scope boundary, not unfinished work, and doesn't disqualify the doc
from being "finished."

## The trailing `## History` section

Graduating a work doc almost always means it carried real, useful reasoning —
rejected alternatives, the evidence that pinned down a non-obvious choice,
dead ends that are worth not re-treading. Deleting all of that and pointing
people at `git log` is technically true but nobody actually does that — so it
doesn't belong there. Instead, keep one final section, always last in the
file:

```markdown
## History

- {One durable bullet per real pivot/rejection: what was tried or considered,
  and the evidence or reasoning that settled it. No dates, no "Phase 1/2/3",
  no day-by-day narrative.}
```

This is a **cleanup, not a carry-over** — collapse the work doc's often large
`## Remaining Work / Open Questions` and `## Changelog` sections into this one
section, written as durable findings rather than a procedural log:

- Drop anything that's just "done"/"implemented" noise with no reasoning
  attached — the Current Design section above already shows what's built.
- Drop dates, phase numbers, and test-count/check-suite trivia.
- Merge near-duplicate entries (an open question and its later resolution)
  into one bullet stating the outcome and why, not the back-and-forth.
- Keep only what would actually help someone from re-litigating a settled
  question or redoing an investigation that's already been done.

A short `## History` (5-15 bullets) is normal. If after cleanup it's still
sprawling, that's a sign the doc needed splitting, not a sign to keep it all.

## Adding a new design doc

1. Promote it from `docs/work/topic-name.md` once the feature is fully built
   and nothing about it is still open — or create
   `docs/design/topic-name.md` directly (no numbering) if it was always
   simple enough to skip a work-in-progress phase.
2. Use the template below.
3. Link it from any ADR(s) that motivated it (`Related:` line), and link
   back to those ADRs from the doc's own `## Related Decisions` section.
4. Collapse the work doc's `## Remaining Work / Open Questions` and
   `## Changelog` sections into one cleaned-up `## History` section (see
   below) — keep only what's true today in the main body.

### Template

```markdown
# {Component or feature name} — Design

- Status: current
- Last updated: YYYY-MM-DD

## Overview

{One or two paragraphs: what this component/feature is and does.}

## Current Design

{How it works today. Diagrams, module layout, key types/flows — whatever
helps a reader understand the current shape without reading the source.}

## Related Decisions

- [ADR-NNNN](../decisions/NNNN-title.md) — {one-line: what it decided}

## History

- {Durable rationale worth keeping — see "The trailing ## History section" above.}
```

Unlike ADRs, design docs are expected to be edited in place as the
implementation legitimately changes — but only ever to keep describing a
*finished* state. The moment a change is in flight, move the doc to
`docs/work/` for the duration. `## History` stays put and keeps growing by
the same cleanup rule each time the doc graduates again.
