# Deployment Templates

- Status: proposed
- Date: 2026-07-15

> **Deferred block — Phase 2 not yet implemented**
> Silent drift detection (`--check-template-drift`) is a known gap in Phase 1. After Phase 1
> ships, leaf files that deviate from an evolved base will not be flagged automatically.
> See [Phase 2 — Drift detection](#phase-2----drift-detection-future) in the implementation
> plan. This ADR is not `completed` until Phase 2 is delivered.

## Context and Problem Statement

In fleet-scale deployments (ADR 0038) every tenant × zone × ring combination requires its own
`deploy.yaml`. These files are structurally identical — same workspace reference, same stage
structure, same locking configuration — differing only in `layers`, `tenant`, and the list of
`environments[]` files. At N tenants × M zones × K rings this produces N×M×K nearly-identical
files.

The cost of this proliferation compounds over time:

- Any structural change to the deployment (new stage, changed workspace, updated locking
  strategy) requires editing hundreds of files in lockstep.
- Onboarding a new tenant requires authoring the same boilerplate by hand for every
  zone × ring combination.
- Drift between files is silent — a file copied six months ago may no longer reflect the
  current stage structure, but `strata validate` only checks individual files for schema
  correctness, not structural consistency across the fleet.

## Related Work

- **ADR 0038 — Multi-Tenant Fleet Management Patterns**: identifies this as Gap 1 (High impact).
- **ADR 0037 — Fleet Operations and Mass Wave Deployment**: assumes a fleet of deployment files;
  template-generated deployments must remain compatible with fleet discovery.
- **ADR 0014 — Guided Onboarding Experience**: `strata new` scaffolding is the creation-time
  complement to templates (ADR 0040).

---

## Design Overview

### Core concept: `spec.extends` + `spec.partial`

No new `kind` is introduced. All files remain `kind: deployment`. A base file that is not
deployable on its own declares `spec.partial: true`. A leaf file references the base via
`spec.extends` and supplies the values that vary per tenant:

```yaml
# templates/ring-base.yaml  — invariant structure, not deployable alone
apiVersion: strata.huybrechts.xyz/v1
kind: deployment
meta:
  name: ring-base
spec:
  partial: true
  workspace:
    name: workspace_integration
    file: "@iac/workspaces/workspace-integration.yaml"
  locking:
    enabled: true
    strategy: wrap
  stages:
    - name: provision
      provisioner: platform_iac
      scope: infrastructure
      on_failure: stop
    - name: configure
      provisioner: platform_iac
      scope: application
      depends_on: [provision]
      on_failure: stop
```

```yaml
# zones/europe-west/customers/contoso/dev/deploy.yaml  — leaf, deployable
apiVersion: strata.huybrechts.xyz/v1
kind: deployment
meta:
  name: deploy-contoso-westeurope-dev
spec:
  extends: "@config/templates/ring-base.yaml"
  layers:
    zone: europe-west
    customer: contoso
    environment: dev
  tenant: contoso
  environments:
    - "@config/zones/europe-west/env.yaml"
    - "@config/customers/contoso/dev/env.yaml"
    - "@config/zones/europe-west/customers/contoso/dev/env.yaml"
```

### `spec.partial`

`spec.partial: true` is an explicit declaration that a file is not deployable in isolation.
It carries two consequences:

1. **`strata validate` in isolation** — Phase 2 (semantic) validation is skipped. The file is
   checked for structural correctness only (valid YAML, known field names, correct types).
   Required deployment fields (`tenant`, `environments`) are not required to be present.
2. **`strata deploy`** — rejects the file immediately with a clear error. A partial file is
   never a valid deploy target.

A chain whose **leaf** is still `partial: true` is a validation error. The last file in any
`extends` chain must be a complete, deployable deployment file.

### `spec.extends`

`spec.extends` accepts a single `@repo/path` reference (same resolution rules as all other
cross-file references in strata). Multi-level chains are supported (`A extends B extends C`);
the chain is resolved depth-first before merging. Circular references are detected at load
time and rejected as a hard error.

### Merge semantics

Resolution produces a single merged model. The child always wins on conflict:

- **Top-level fields** — child value replaces base value (field-level replacement, not deep
  merge, to keep behaviour predictable).
- **`stages`** — merged by `name`. A child entry with a matching `name` overrides that stage's
  fields; child stages with new names are appended. This allows a tenant to adjust `on_failure`
  or add `health_checks` on a specific stage without rewriting the full list.
- **`environments`** — always appended. Base environments come first (shared infrastructure
  layers); child environments follow (tenant-specific layers).
- **`partial`** — consumed during resolution and stripped from the merged model. The merged
  result is never partial.
- **`extends`** — consumed during resolution and stripped from the merged model.

### Two-phase validation

`strata validate` always runs both phases on the **merged** model. On a raw partial file it
runs Phase 1 only.

| Phase              | Scope              | What is checked                                                                        |
| ------------------ | ------------------ | -------------------------------------------------------------------------------------- |
| **1 — Structural** | Every file, always | Valid YAML; field names known to schema; correct types                                 |
| **2 — Semantic**   | Merged model only  | Required fields present; cross-references resolve; stage names unique; no partial leaf |

### Compatibility with fleet discovery (ADR 0037)

Fleet discovery reads `kind: deployment` files. Partial files are excluded from fleet
discovery (they have no `tenant` and are not deployable). Leaf files with `spec.extends` are
fully resolved at load time; the fleet sees the merged model. The `extends` mechanism is
transparent to fleet operations.

### Drift detection

`strata validate --check-template-drift` (fleet-wide) detects leaf files whose base has
changed since the leaf was last updated, using a hash of the resolved base stored in
`meta.annotations["strata.huybrechts.xyz/base-hash"]`. The annotation is written by
`strata build` and checked by `strata validate`.

---

## Implementation Plan

### Phase 1 — Model, resolver, and validation (this ADR)

**Goal:** `spec.partial` and `spec.extends` are understood by the schema, resolver, and
validator. Build and deploy are guarded against partial files. No new CLI flags.

#### 1.1 `models/deployment_model.py`

- Add `partial: bool = Field(False, ...)` to `DeploymentSpecModel`.
- Add `extends: Optional[str] = Field(None, ...)` to `DeploymentSpecModel`.
- Make `workspace` and `environments` optional (`Optional[...]`, default `None`) so partial
  files parse without them. Phase 2 enforces their presence on the merged model.

#### 1.2 `services/deployment_extension_resolver.py` (new)

Pure resolution — no side effects beyond file loading:

```python
class DeploymentExtensionResolver:
    def resolve(
        self,
        file_path: Path,
        work_path: Path,
        repo_map: dict,
        _visited: frozenset[str] | None = None,
    ) -> DeploymentSpecModel:
        """Load file, recurse into spec.extends, merge, return resolved spec."""
```

Merge rules applied in order:

1. Recursively resolve `spec.extends` to get the base spec.
2. For each top-level field: child wins (field-level replacement).
3. `stages`: merge by `name`; child entries override matching base entries; new names appended.
4. `environments`: base list + child list (append).
5. Strip `partial` and `extends` from the returned model.

Cycle detection: track visited `file_path` strings in `_visited`; raise on collision.

#### 1.3 `validators/platform_validator.py`

In `validate()`, before Phase 1 loads the service:

1. If `spec.extends` is present → call `DeploymentExtensionResolver.resolve()` → replace the
   raw parsed spec with the merged spec before the service is constructed.
2. If `spec.partial: true` on the **raw** file (before resolution) → skip Phase 2 entirely.
3. After resolution: if the merged spec still has `partial: true` → hard error
   (`PARTIAL_LEAF_ERROR`): the last file in the chain must not be partial.

#### 1.4 `services/deployment_service.py`

Add a pre-flight guard at the top of `validate()`:

```python
if self.model.spec.partial:
    self._errors.append("partial deployment cannot be validated as a deploy target")
    return False
```

#### 1.5 `commands/deploy/base_deploy_command.py`

Add a pre-flight check in `_before_execute()` (or `_execute()`) before the deployment
service is used:

```python
if deployment.spec.partial:
    self._errors.append(
        f"'{self._file_path.name}' is a partial deployment and cannot be deployed. "
        "A leaf deployment that extends this file is required."
    )
    return False
```

#### Phase 1 test coverage

| Area         | Tests                                                                                                              |
| ------------ | ------------------------------------------------------------------------------------------------------------------ |
| Model        | `partial` and `extends` fields parse; `workspace`/`environments` optional on partial                               |
| Resolver     | Single-level merge; multi-level chain; field replacement; stages named merge; environments append; cycle detection |
| Validator    | Partial file → Phase 2 skipped; leaf-is-partial → hard error; merged model validated                               |
| Deploy guard | `partial: true` file → rejected before any operation                                                               |

### Phase 2 — Drift detection (future)

`strata validate --check-template-drift`: hash the resolved base at build time
(`meta.annotations["strata.huybrechts.xyz/base-hash"]`); compare at validate time to detect
leaf files whose base has silently changed.

---

## Consequences

- Deployment files for fleet-scale repos reduce from N×M×K boilerplate files to a small set
  of partial base files + thin leaf files.
- No new `kind` — the schema surface grows by two optional fields (`partial`, `extends`) on
  the existing deployment kind.
- `strata validate` must resolve `extends` chains before Phase 2 — load order matters for
  validation but not for fleet discovery.
- `strata deploy` gains a pre-flight check: reject `partial: true` files before any
  infrastructure operation begins.
- ADR 0040 (tenant onboarding scaffolding) can generate leaf files from a named base,
  further reducing manual authoring.
- Existing deployment files without `extends` are unaffected — the feature is purely additive.
- Multi-level chains (`A extends B extends C`) are supported from day one; depth is unbounded
  but circular references are rejected.
