# Design Docs

This directory holds **living design docs** for strata-v2 components and
features — how something currently works, its build-out progress, and open
questions — as distinct from [`docs/decisions/`](../decisions/README.md),
which records point-in-time architectural decisions that don't change once
written.

**Start here:** [v2-schema-overview.md](v2-schema-overview.md) — a one-page
catalog of every v2 kind and its current status, linking out to the ADR and
(where one exists) the deeper design doc for each.

## Design doc vs. ADR

| | ADR (`docs/decisions/`) | Design doc (`docs/design/`) |
| --- | --- | --- |
| Answers | "Why did we choose X over Y?" | "How does X currently work / how far along is it?" |
| Lifespan | Immutable once accepted; superseded by a new ADR, never edited in place | Living — edited in place as the design/implementation evolves |
| Scope | One decision | One component, feature, or subsystem — can reference many decisions |
| Numbered | Yes (`NNNN-title.md`, chronological) | No — file name is just the topic (`topic-name.md`) |
| Contains phases/progress trackers | No (keep `## Remaining Work` short; move detail here) | Yes — this is the right place for phase checklists, rollout status, open questions |

Rule of thumb: if you're tempted to add a second numbered "Decision" to an
ADR, or a growing checklist to its `## Remaining Work`, that content belongs
in a design doc instead, linked from the ADR.

## Adding a new design doc

1. Create `docs/design/topic-name.md` (no numbering — pick a stable,
   descriptive name; it may be renamed later if the topic is renamed).
2. Use the template below.
3. Link it from any ADR(s) that motivated it (`Related:` line), and link
   back to those ADRs from the doc's own `## Related Decisions` section.

### Template

```markdown
# {Component or feature name} — Design

- Status: draft | current | deprecated
- Last updated: YYYY-MM-DD

## Overview

{One or two paragraphs: what this component/feature is and does.}

## Current Design

{How it works today. Diagrams, module layout, key types/flows — whatever
helps a reader understand the current shape without reading the source.}

## Related Decisions

- [ADR-NNNN](../decisions/NNNN-title.md) — {one-line: what it decided}

## Remaining Work / Open Questions

- {Still-open items, phases not yet done, known gaps.}

## Changelog

- YYYY-MM-DD: {what changed in this doc and why}
```

Unlike ADRs, design docs are expected to be edited in place — update
`## Current Design` and `## Remaining Work` as the implementation progresses,
and add a line to `## Changelog` each time you do so a reader can see when
the description last matched reality.
