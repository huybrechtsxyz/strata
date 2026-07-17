# Rename configuration spec.repositories to spec.remotes

- Status: completed
- Date: 2026-06-18
- Issue: repo validation bug (provisioner source.repository checked against wrong set)

## Context and Problem Statement

Strata has two separate collections both named "repositories":

1. **`solution.json → spec.repositories`** — managed by `strata repo add`. Maps named
   source code repos to local filesystem paths. Enables `@repo/path` resolution so
   developers can work with multiple IaC/CaC/Cfg repos simultaneously, each at
   different local locations.

2. **`configuration.yaml → spec.repositories`** — hand-edited in team-shared config.
   A registry of named remote endpoints (git URLs, container registries, bundled paths)
   that the platform pulls from or pushes artifacts to.

The naming collision caused a validated bug: `workspace_service._validate_dynamic()`
checked provisioner `source.repository` references (which target solution repos) against
`configuration_model.spec.repositories` (which lists artifact backends). A repo properly
registered via `strata repo add` failed validation because the validator looked in the
wrong collection.

Beyond the bug, the shared name confuses contributors. The two concepts have different:

- **Ownership:** developer-local state vs team-shared configuration
- **Direction:** "where is my source code?" vs "where do artifacts come from / go to?"
- **Path semantics:** absolute filesystem paths vs relative deploy paths or remote URLs
- **Lifecycle:** `strata repo add` at dev setup vs defined once in config and committed

## Decision Drivers

- Pre-v1 — breaking YAML schema changes are still acceptable
- The naming collision already caused a production bug
- Contributors and AI agents repeatedly confuse the two concepts
- Solution repos cannot be merged into configuration repos (multiple repos bring
  multiple configs; solution repos are developer-local state)
- Configuration repos cannot be merged into solution repos (they define team-shared
  remote endpoints, not local checkouts)

## Considered Options

### Option A — Rename configuration field to `spec.remotes`

Rename the configuration-side collection:

- `ConfigurationSpecModel.repositories` → `ConfigurationSpecModel.remotes`
- `RepositoryModel` class → `RemoteModel`
- `RepositoryType` enum → `RemoteType`
- `ConfigurationService.get_repositories()` → `get_remotes()`
- `ConfigurationModel.get_repo_map()` → `get_remote_map()`
- YAML field: `spec.repositories:` → `spec.remotes:`
- Solution repos stay as `repositories` (they ARE repos)

**Pro:** "Remote" is git-familiar, naturally bidirectional (fetch from, push to),
covers all three current types:
- `type: bundled` → a local remote (like `git remote add local .`)
- `type: gitops` → a git remote for manifest push/pull
- `type: container` → a container registry remote

**Pro:** Minimal conceptual distance — developers already think "remote = named URL."

**Pro:** Clear disambiguation — `repositories` = source code checkouts,
`remotes` = named endpoints.

### Option B — Rename to `spec.stores`

**Pro:** Emphasises storage/artifact nature.
**Con:** `ManifestStoreType` enum already uses "store" — risks confusion between
the store type and the store entry. Doesn't convey "pull from" semantics well.

### Option C — Rename to `spec.origins`

**Pro:** Git-familiar ("where it came from").
**Con:** Implies read-only source; config repos are also push targets (manifest gitops).
Doesn't capture bidirectional nature.

### Option D — Rename to `spec.catalogs`

**Pro:** "Known named sources" — good for discovery/lookup.
**Con:** Implies read-only browsing; doesn't convey "push manifests here."

### Option E — Rename to `spec.backends`

**Pro:** Accurate for storage backends.
**Con:** Too Terraform-specific in connotation. Confusing for non-IaC users.

### Option F — Rename to `spec.endpoints`

**Pro:** Generic network endpoint concept.
**Con:** Better reserved for future API/service endpoint extensions.

### Option G — Keep both as `repositories` (status quo + fix descriptions only)

Fix the `SourceModel.repository` field description and validator bug. No schema change.

**Pro:** Zero breaking changes.
**Con:** Naming collision persists. Future contributors will hit the same confusion.
The bug proved this isn't theoretical.

## Decision Outcome

**Chosen option: A — Rename to `spec.remotes`**

The term "remote" is universally understood from git workflows, naturally covers all
three endpoint types (local, git, container), is bidirectional (fetch and push), and
creates a clean semantic boundary:

- `solution.spec.repositories` = "repos I have checked out locally"
- `configuration.spec.remotes` = "named endpoints the platform talks to"

## Scope of Change

| Category      | Files         | Key changes                                                                                   |
| ------------- | ------------- | --------------------------------------------------------------------------------------------- |
| Models        | 2             | `RepositoryModel` → `RemoteModel`; `RepositoryType` → `RemoteType`; field rename              |
| Services      | 1             | Method renames: `get_repositories()` → `get_remotes()`, `get_repo_map()` → `get_remote_map()` |
| Controllers   | 1             | `repository_controller.py` — all method names and internal variables                          |
| Builders      | 1             | Docstring updates (SBOM deps collector)                                                       |
| Templates     | 4             | YAML section headers: `repositories:` → `remotes:`                                            |
| Test data     | 1             | Configuration YAML fixtures                                                                   |
| Tests         | ~2            | Service method call updates                                                                   |
| Documentation | 3             | Spec references and examples                                                                  |
| **Total**     | **~14 files** |                                                                                               |

## What Does NOT Change

- `solution.json → spec.repositories` — stays as-is
- `SolutionSpecRepositoryModel` — stays as-is
- `SolutionController.get_repositories()` — stays as-is
- `strata repo add/remove/list/status` CLI — stays as-is
- `SourceModel.repository` field name — stays (but description is corrected to
  reference "solution registered repositories" not "configuration repositories")

## Consequences

**Positive:**
- Eliminates naming collision that caused the validation bug
- Clear mental model for contributors: repos = local, remotes = endpoints
- Aligns with git vocabulary that developers already know
- Pre-v1 change avoids breaking users after stable release

**Negative:**
- Breaking YAML schema change for existing configuration files
- All existing `configuration.yaml` files must update `repositories:` → `remotes:`
- ~14 files touched in a single refactoring PR

**Mitigation:**
- Provide a one-time migration note in CHANGELOG
- Consider temporary backwards-compat alias during transition (accept both field names
  with a deprecation warning) — optional, may not be needed pre-v1
