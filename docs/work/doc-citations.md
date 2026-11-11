# Doc citations in code - Work

- Status: in-progress (built 2026-11-10, not released)
- Last updated: 2026-11-10

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
- **A guard test** (`tests/strata/test_doc_citations.py`) finds every `docs/....md` path in a tracked file under
  `src/` or `tests/`, including one wrapped after a `/`, and fails listing each path that does not exist, with
  where the doc of that name is now. A path followed by more of a file name (`docs/handover.md.j2`, a template a
  test writes into a temporary solution) is not a citation.
- Out of scope: relative links between docs (`[x](../design/x.md)`). They are a separate check, over Markdown links,
  and do not go into `strata schema`.

## Implementation plan

1. A one-off script rewrites the directory part of each stale citation, wrapped or not.
2. The guard test, with its own small tests of the pattern: a wrapped citation, an anchor, the `.md.j2` exclusion.
3. `ruff format`, the full suite, `strata validate config`.

## Changelog

- 2026-11-10: created, designed and built.
