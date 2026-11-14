# `Version.spec.workspace` is deprecated: the deployments that list a version document decide where it applies

- Status: superseded — see ADR-0065 (the field is removed in 2.7, not after a deprecation until 3.0)
- Date: 2026-11-14
- Related: [ADR-0063](0063-a-deployment-lists-several-version-documents.md) (a deployment lists several version
  documents), [ADR-0019](0019-version-pinning.md) (`kind: version`), `version-composition.md` (work doc)

## Context and Problem Statement

`Version.spec.workspace` binds a version document to one workspace. When it is unset, the first deployment that
references the document fixes the workspace for every other one, so a document shared by deployments of two
workspaces fails validation (`version_workspace_mismatch`). It also groups documents for `promote status` and
`promote apply`, and `strata version new` requires it.

With version documents composed at the deployment (ADR-0063), a shared document such as `prd-core` is meant to be
listed by deployments of every product. What is the field for?

## Considered Options

- **Keep it as an optional limit**: set, only that workspace's deployments may list the document; unset, any may.
- **Remove it**, with a deprecation period: which workspaces use a document follows from the deployments that
  list it, as it does for artifacts, modules and every other shared document.

## Decision Outcome

Chosen: **remove it, with a deprecation period**, because each of its jobs is done from the deployment side:

| Job of `spec.workspace`                              | Done instead by                                                         |
| ---------------------------------------------------- | ----------------------------------------------------------------------- |
| Only that workspace's deployments may list it        | `version_document_unused` (a listed document whose pins the deployment uses none of, while others do) and `version_pin_conflict` |
| Unset: the first deployment fixes it                 | nothing: any deployment may list any document                           |
| Grouping for `promote status` and `promote apply`    | the workspaces of the deployments                                       |
| `version new --workspace` (required)                 | nothing: the flag never chose a pin                                     |

- **2.7:** the first-deployment rule is gone, so a document without `spec.workspace` can be shared. A declared
  value is still a limit (`version_workspace_mismatch`), and a warning (`version_workspace_deprecated`) asks to
  remove it. `version new` no longer writes it and ignores `--workspace` with the same warning.
- **3.0:** the field and `--workspace` are removed; a document that still declares it fails to load.

### Consequences

- Good, because the reported error for a document shared by two workspaces is gone, and a solution that validated
  before still validates.
- Good, because there is one place that says where a document applies: the deployments.
- Bad, because the early, explicit error when a deployment lists another product's document becomes a warning,
  and a document pinning only targets both products use is not caught at all. Naming (`prd-datahub`) and
  `strata version show` carry what the field said.

Keeping it as an optional limit was rejected: an optional field that only restricts is a second statement of what
the deployments already say, and goes stale silently when they change.
