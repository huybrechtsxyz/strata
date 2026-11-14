# `Version.spec.workspace` is removed in 2.7, not deprecated until 3.0

- Status: accepted
- Date: 2026-11-14
- Related: [ADR-0064](0064-deprecate-version-spec-workspace.md) (the deprecation this replaces the timing of),
  [ADR-0063](0063-a-deployment-lists-several-version-documents.md) (a deployment lists several version documents),
  `version-composition.md` (work doc, Phase 8)

## Context and Problem Statement

ADR-0064 decided that the deployments listing a version document decide where it applies, and planned the removal
of `Version.spec.workspace` in two steps: deprecated in 2.7 (still enforced when declared, with a warning), removed
in 3.0. The deprecation period exists for users who need a release to drop the field.

strata has two users, both reachable directly. Keeping the field for a release means keeping a second, conflicting
way to say where a document applies: the `version_workspace_mismatch` check, a promotion view that also groups by
the declared field, and `version new --workspace` accepted and ignored. When should the field go?

## Considered Options

- **Deprecate in 2.7, remove in 3.0**, as ADR-0064 planned.
- **Remove in 2.7**, together with the feature that replaces it.

## Decision Outcome

Chosen: **remove in 2.7**, because the two users can drop one line per version document in the same upgrade, and a
single release then has one way to say where a document applies instead of two.

- A version document that still declares `spec.workspace` fails to load, with a message that names the removal and
  the fix (delete the `workspace:` line), not pydantic's generic "extra inputs are not permitted".
- `version_workspace_mismatch` and `version_workspace_deprecated` are gone, and so is `strata version new
  --workspace`.
- `promote status` and `promote apply` read a workspace's rollout only from its deployable deployments.
- `strata path list version --workspace W` lists the version documents the deployments of `W` list.
- `strata deploy list` rows keep only `versions`; the single `version` field is gone.

### Consequences

- Good, because there is one statement of where a version document applies: the deployments that list it.
- Good, because the promotion code has no fallback for documents no deployment lists.
- Bad, because the change is breaking within a minor release: a solution whose version documents declare
  `spec.workspace`, or a script that runs `version new --workspace` or reads `deploy list`'s `version`, fails until
  it is changed. Acceptable with two known users; the CHANGELOG says so under `Removed`.
