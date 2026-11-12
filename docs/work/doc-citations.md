# Doc citations in code - Work

- Status: in-progress (built 2026-11-10, not released)
- Last updated: 2026-11-11

## Overview

Docstrings, comments and schema descriptions cite the doc that explains them: `(docs/design/audit-trail.md's
"The audit files")`. When a doc moves between `docs/work/`, `docs/design/` and `docs/archive/`, nothing updates
those citations. A count on 2026-11-10 found 144 citations of 13 docs at a path that no longer exists, in 70 files
under `src/` and `tests/`. Some wrap across lines (`docs/design/` at the end of one docstring line, the file name
on the next), so a plain `git grep` misses them. Design 10 of [artifact-gap-analysis.md](artifact-gap-analysis.md)
fixed the `artifact-references.md` citations and left the rest as this follow-up, with a guard test.

| Cited as | Citations | Where the doc is |
| --- | --- | --- |
| `docs/design/gap_fit_v1.md` | 69 | `docs/work/` |
| `docs/design/audit-trail.md` | 31 | `docs/work/` |
| `docs/work/cross-run-step-output-delivery.md` | 18 | `docs/archive/` |
| `docs/design/remotes.md` | 4 | `docs/work/` |
| `docs/work/checkov-integration.md` | 3 | `docs/archive/` |
| `docs/work/cve-scanner-integration.md` | 3 | `docs/archive/` |
| `docs/design/tenant-zone-policy.md` | 3 | `docs/work/` |
| `docs/work/provisioner-source-dependencies.md` | 2 | `docs/design/` |
| `docs/design/build-command.md` | 2 | `docs/work/` |
| `docs/work/deploy-plan-preview.md` | 1 | `docs/design/` |
| `docs/design/solution-loading-and-phase2-validation.md` | 1 | `docs/work/` |
| `docs/work/terraform-variable-precedence.md` | 1 | `docs/design/` |
| `docs/work/unified-terraform-projection.md` | 1 | `docs/design/` |

## Design

- **Rewrite each citation to the doc's current place**, the one doc under `docs/` with that file name. Only the
  directory changes; the wording, any section name and any line wrap stay. Every cited doc still exists, so no
  citation needs a new target.
- **A citation of an archived doc stays on the archive.** Each of the 24 cites how something was decided ("Implementation
  Plan step 4", "Phase 1", a schema description's "Design notes:"), which is what the archive keeps. This is design
  10's rule: what something does now points to the reference, why or when it was decided points to the archive.
- **A guard test** (`tests/strata/test_doc_citations.py`) finds every `docs/....md` path in a file (tracked or new) under
  `src/` or `tests/`, including one wrapped after a `/`, and fails listing each path that does not exist, with
  where the doc of that name is now. A path followed by more of a file name (`docs/handover.md.j2`, a template a
  test writes into a temporary solution) is not a citation.

### Links between docs (added 2026-11-11)

The same drift inside `docs/`: 67 relative links pointed to a doc that had moved, and 138 links in finished docs
(`design`, `archive`, ADRs, `how-to`, `config`, `integrations`) pointed into `docs/work/`, whose docs move or go.

- **A broken link is re-pointed** at the one doc of that file name (64 links).
- **Only a work doc links to a work doc.** Elsewhere, a work doc is named without a link: `` `audit-trail.md` (work
  doc) ``, or the link's own text followed by `` (work doc `x.md`) ``. This applies to ADRs and the archive too
  (140 links, including 2 broken links that pointed into `docs/work/`). A link to the `docs/work/` folder itself
  became `` `docs/work/` ``.
- `docs/work/README.md` and `docs/decisions/README.md` said to link an ADR and its work doc both ways; they now say
  the ADR names the work doc without a link.
- **Guard:** `tests/strata/test_doc_links.py` checks every relative `.md` link in a Markdown file of the repository
  (`docs/`, the root `README.md`, `.github/`, ...; outside fenced code and inline code): it resolves, and it does not go from outside `docs/work/` into it.
- Markdown outside `docs/` had no broken link and none into `docs/work/`; the guard covers it so it stays so.
- Out of scope: a work doc's name written as plain text, which can go stale without breaking anything.

## Implementation plan

1. A one-off script rewrites the directory part of each stale citation, wrapped or not.
2. The guard test, with its own small tests of the pattern: a wrapped citation, an anchor, the `.md.j2` exclusion.
3. `ruff format`, the full suite, `strata validate config`.
4. The links between docs: a one-off script, then `tests/strata/test_doc_links.py`.

## Changelog

- 2026-11-10: created, designed and built.
- 2026-11-11: links between docs: broken ones re-pointed, links into `docs/work/` from finished docs removed, guard test.
