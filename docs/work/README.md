# Work Docs

This directory holds **in-progress** docs for strata-v2 components and
features — phase checklists, open design questions, drafts, exploration
notes, and decision-evolution narrative — as distinct from
[`docs/decisions/`](../decisions/README.md) (immutable, decided) and
[`docs/design/`](../design/README.md) (finished, nothing pending).

If you're trying to answer "where are we on X, and what's still open?", this
is the directory to check.

## Work doc vs. ADR vs. design doc

|                                           | ADR (`docs/decisions/`)       | Work doc (`docs/work/`)                       | Design doc (`docs/design/`)                |
| ----------------------------------------- | ----------------------------- | --------------------------------------------- | ------------------------------------------ |
| Answers                                   | "Why did we choose X over Y?" | "Where are we on X, what's still open?"       | "How does X work, now that it's finished?" |
| Lifespan                                  | Immutable once accepted       | Living — edited constantly as work progresses | Edited in place, but only while accurate   |
| Contains phases/open questions/changelogs | No                            | Yes — this is the right place                 | No                                         |

## Life cycle of a work doc

1. **Created** when a feature/decision needs ongoing design thought, a phased
   build-out, or has open questions not yet resolved — often right after the
   ADR that motivated it (the work doc links the ADR; the ADR names the work
   doc without a link, see below).
2. **Updated in place** as phases complete, questions resolve, or the design
   changes — a `## Changelog` section is expected and encouraged here (unlike
   in `docs/design/`, where it isn't).
3. **Graduates to `docs/design/`** once everything in it is actually
   finished — nothing pending, no open questions left. At that point,
   collapse the phase-by-phase `## Remaining Work / Open Questions` and
   `## Changelog` sections down into one cleaned-up trailing `## History`
   section (durable rationale only — see
   [`docs/design/README.md`](../design/README.md#the-trailing--history-section)),
   and move the file.
4. If a work doc is fully superseded or abandoned without becoming a design
   doc, delete it (or mark it `Status: abandoned` with a one-line reason) —
   don't let stale WIP accumulate here indefinitely.

## Adding a new work doc

1. Create `docs/work/topic-name.md` (no numbering — matches `docs/design/`'s
   naming convention, since the file may move there later without a rename).
2. Use the template below.
3. Name it, without a link, in any ADR(s) it tracks follow-on work for
   (`Related:` line): `` `topic-name.md` (work doc) ``. Only a work doc links to a
   work doc. A work doc moves to `docs/design/` or `docs/archive/` and may be
   deleted, so a link to it from a finished doc would break;
   `tests/strata/test_doc_links.py` fails on one.

### Template

```markdown
# {Component or feature name} — Work

- Status: draft | in-progress | blocked
- Last updated: YYYY-MM-DD

## Overview

{One or two paragraphs: what this is, and why it's not in docs/design/ yet.}

## Current Design / Progress

{What's built so far, what the shape looks like today, even if incomplete.}

## Related Decisions

- [ADR-NNNN](../decisions/NNNN-title.md) — {one-line: what it decided}

## Remaining Work / Open Questions

- {Still-open items, phases not yet done, known gaps.}

## Changelog

- YYYY-MM-DD: {what changed in this doc and why}
```

## Citing docs, and moving them

Docs move between `docs/work/`, `docs/design/` and `docs/archive/`, so every
citation of one has to follow it. Two guard tests keep that true:

- **From code.** A docstring, comment or schema description cites a doc by
  its repository path, optionally with a section: `(docs/design/deploy-command.md's
  "Docker context pin")`. Cite what something does now from the design or
  config doc; cite why or when it was decided from the archive.
  `tests/strata/test_doc_citations.py` finds every `docs/....md` path in a
  file under `src/` or `tests/`, including one wrapped across lines after a
  `/`, and fails on a path that does not exist, naming where a doc of that
  name is now. A path followed by more of a file name (`docs/handover.md.j2`)
  is not a citation.
- **Between docs.** `tests/strata/test_doc_links.py` checks every relative
  `.md` link in the repository's Markdown (outside code): it must resolve,
  and it must not go from outside `docs/work/` into it. Only a work doc links
  to a work doc; anywhere else, name it without a link: `` `audit-trail.md`
  (work doc) ``.

When a doc moves or merges into another, update its citations in the same
change: rewrite the directory part (and the section name, if it merged), and
keep the wording.
