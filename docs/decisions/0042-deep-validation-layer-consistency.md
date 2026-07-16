# Deep Validation and Layer Consistency

- Status: partial
- Date: 2026-07-15

## Context and Problem Statement

`strata validate <file>` performs structural validation: schema correctness, required fields,
reference integrity within a single file. It does not validate cross-file consistency or
semantic correctness at the fleet level.

In fleet-scale deployments (ADR 0038) two classes of silent errors emerge that structural
validation cannot catch:

**Class 1 — Layer identity mismatch**

The `layers` block in a deployment file (`zone: europe-west`, `customer: contoso`,
`environment: dev`) is metadata that should be consistent with the `environments[]` file
paths. A deployment can declare `layers.customer: contoso` while referencing env files
from a different tenant's path tree. The error is only detected at runtime when resolved
values are wrong — often long after the misconfiguration was introduced.

```yaml
# Misconfigured: layers says contoso but environments reference fabrikam
layers:
  customer: contoso
environments:
  - "@config/zones/europe-west/env.yaml"
  - "@config/customers/fabrikam/dev/env.yaml"       # wrong tenant
  - "@config/zones/europe-west/customers/contoso/dev/env.yaml"
```

**Class 2 — Fleet structural drift**

A deployment file whose `extends` template (ADR 0039) has been updated but the
instantiation has not been reviewed. Or a deployment file that was manually copied from
another tenant and retains the source tenant's `meta.name` or `tenant` field.

## Related Work

- **ADR 0038 — Multi-Tenant Fleet Management Patterns**: identifies this as Gap 5 (Low-Medium).
- **ADR 0039 — Deployment Templates**: template drift detection is part of this ADR's scope.
- **ADR 0040 — Tenant Onboarding Scaffolding**: scaffolding prevents these errors at
  creation time; deep validation catches them in existing files.
- **ADR 0014 — Guided Onboarding Experience**: `strata validate --explain` is the existing
  plain-English validation surface; deep validation extends it.

---

## Design Overview

### `strata validate --deep` extensions

`strata validate --deep` already exists for cross-reference checks requiring an active
profile. This ADR extends it with two new rule sets applied when a configuration
repository with `spec.layering` is present.

#### Rule set 1: Layer identity consistency

For each deployment file with a `layers` block and an `environments[]` list:

1. Extract the declared layer values (`zone`, `customer`, `environment`).
2. For each environment file path, check whether the path segments contain any layer
   value from a **different** identity than declared.
3. Emit a warning (not an error) when a mismatch is detected, since not all env files
   encode the full layer identity in their path.

Example output:
```
WARN  deploy-contoso-westeurope-dev.yaml
      layers.customer = 'contoso'
      environments[1] path contains 'fabrikam' — possible tenant mismatch
      → customers/fabrikam/dev/env.yaml
```

The check is heuristic — it looks for layer values as path segments. It does not attempt
to parse env file contents for tenant identity, as that would require full resolution.

#### Rule set 2: Template drift detection

For each deployment file with an `extends` reference (ADR 0039):

1. Resolve the referenced template file.
2. Compare `meta.labels.version` of the template against the version recorded in the
   instantiation file's `meta.annotations.template_version` (written by scaffolding,
   ADR 0040).
3. Emit a warning when the template version has advanced beyond what the instantiation
   was last reviewed against.

```
WARN  zones/europe-west/customers/contoso/dev/deploy.yaml
      template 'customer-ring-template' has advanced to version 1.3
      this deployment was last reviewed against version 1.1
      run 'strata validate --check-template-drift' to see what changed
```

#### Rule set 3: Tenant field vs path consistency

For each deployment file with a `tenant` field:

1. Check that the `tenant` value appears as a path segment in all `environments[]` paths
   that are in a tenant-specific directory (heuristic: paths containing `/customers/`).
2. Warn if any such path does not contain the declared tenant name.

#### Rule set 4: File path convention validation (`path_convention` policy type)

Validates that files on disk follow the declared directory naming conventions. A single
repository can contain **multiple independent path hierarchies** — each with its own
convention. For example a configuration repository may have:

```
customers/{tenant}/tenant.yaml          # tenant registry (flat)
landscape/{name}/landscape.yaml         # landscape definitions (flat)
zones/{zone}/customers/{tenant}/{env}/  # zone deployment tree (nested)
```

These are three different conventions with different depths, segments, and validation
rules. The design must support all of them without conflict.

---

**Why not `spec.layering`?**

`spec.layering` already exists on `ConfigurationModel` and serves a different purpose:
it declares deployment layer **metadata** (which layers are required, default values,
value validation patterns). Bolting path-convention fields onto it conflates two concerns:
_"what layers does this platform have"_ vs _"what does the folder tree look like on disk"_.
A platform can have zone/tenant/ring layers without implying any particular folder
structure. Path conventions are a **separate declaration**.

---

**New model: `spec.paths`**

A list of scoped convention rules, declared once in the configuration file. Each
convention targets a subtree of the repository via a `scope` glob and defines the
expected path structure within that scope.

```yaml
# configuration.yaml
spec:
  paths:
    - name: tenant-registry
      scope: "customers/**"
      pattern: "customers/{tenant}"
      validate:
        tenant: "customers/{tenant}/tenant.yaml"   # file must exist

    - name: zone-deployment-tree
      scope: "zones/**"
      pattern: "zones/{zone}/customers/{tenant}/{env}"
      validate:
        zone: spec.zones[*].name                   # must be a declared zone
        tenant: "customers/{tenant}/tenant.yaml"   # tenant registered at root
        env: spec.environments[*].name             # must be a declared environment

    - name: landscape-registry
      scope: "landscape/**"
      pattern: "landscape/{landscape}"
      validate:
        landscape: "landscape/{landscape}/landscape.yaml"
```

**Fields:**

| Field      | Required | Description                                                        |
| ---------- | -------- | ------------------------------------------------------------------ |
| `name`     | yes      | Unique name for diagnostics and policy reference                   |
| `scope`    | yes      | Glob pattern — only files matching this scope are checked          |
| `pattern`  | yes      | Path template with `{segment}` captures (one segment = one folder) |
| `validate` | no       | Per-segment validation rules (see rule types below)                |

**`scope`** — determines which files this convention applies to. A file is checked
against a convention only if its relative path (from `work_path`) matches the glob.
Multiple conventions may match the same file if their scopes overlap — all matching
conventions are evaluated independently.

**`pattern`** — matched against the relative path from `work_path`. Anchored at the
start. `{segment}` captures exactly one path part (no `/`). Remaining path parts after
the pattern (e.g. ring directory, filename) are ignored — they don't need to match.
If the path doesn't match the pattern → skip (not a violation; file is in scope but
at a shallower depth).

**`validate`** — per-segment rules. Two rule types:

| Rule syntax                      | Meaning                                                       |
| -------------------------------- | ------------------------------------------------------------- |
| `spec.zones[*].name`             | Value must appear in a field of the loaded ConfigurationModel |
| `customers/{tenant}/tenant.yaml` | File at this path (anchored at `work_path` root) must exist   |

Placeholder expansion: `{segment}` references in validate rules are expanded from the
captured values dict. E.g., if `tenant = "contoso"` was captured, then
`customers/{tenant}/tenant.yaml` → `customers/contoso/tenant.yaml`.

---

**Policy declaration — triggers enforcement:**

```yaml
policies:
  - name: enforce-path-conventions
    type: path_convention
    phase: validate
    enforcement: deny
    description: "All files must follow declared path conventions"
    # reads spec.path_conventions automatically — no configuration block needed
```

When a `path_convention` policy is declared, the engine reads `spec.paths`
from the loaded `ConfigurationModel`. If `spec.paths` is absent or empty,
the policy skips with a warning.

The `enforcement` level (`deny` / `warn`) applies to all conventions uniformly. If
per-convention enforcement granularity is needed, declare multiple policies with a
`conventions` filter:

```yaml
policies:
  - name: strict-zone-check
    type: path_convention
    phase: validate
    enforcement: deny
    configuration:
      conventions: [zone-deployment-tree]   # only check this convention

  - name: advisory-landscape-check
    type: path_convention
    phase: validate
    enforcement: warn
    configuration:
      conventions: [landscape-registry]
```

---

**Evaluation steps:**

1. Load `spec.paths` from the `ConfigurationModel`.
2. For the file being validated, compute its path relative to `work_path`.
3. For each convention whose `scope` glob matches the relative path:
   a. Attempt to match `pattern` against the relative path. If no match → skip (file
      is at a depth the pattern doesn't reach; not a violation).
   b. Extract captured segment values into a dict (e.g., `{"zone": "europe", "tenant": "contoso"}`).
   c. For each segment with a `validate` rule:
      - `spec.*` rules: resolve the dot-path against the `ConfigurationModel`. If no
        configuration service → warn and skip.
      - File existence rules: expand `{segment}` placeholders from the captured dict,
        check `work_path / expanded_path` exists.
   d. Emit a violation for each segment whose value fails validation.

---

**Example output:**

```
DENY  zones/atlantis/customers/contoso/dev/deploy.yaml
      convention 'zone-deployment-tree' — segment 'zone' = 'atlantis'
      not in spec.zones[*].name: [europe, nordics, us-east, us-west]

DENY  zones/europe/customers/unknown-co/prd/deploy.yaml
      convention 'zone-deployment-tree' — segment 'tenant' = 'unknown-co'
      customers/unknown-co/tenant.yaml does not exist

WARN  landscape/ghost/landscape.yaml
      convention 'landscape-registry' — segment 'landscape' = 'ghost'
      landscape/ghost/landscape.yaml does not exist (self-reference — file is being created?)
```

---

**How the matcher handles variant structures:**

```
# Convention: zone-deployment-tree
#   scope: "zones/**"
#   pattern: "zones/{zone}/customers/{tenant}/{env}"

# Structure A — zones/{zone}/customers/{tenant}/{env}/deploy.yaml
path: zones/europe/customers/contoso/prd/deploy.yaml
  match pattern at: zones/europe/customers/contoso/prd → ✓
  captures: zone=europe, tenant=contoso, env=prd
  validate zone: "europe" ∈ spec.zones[*].name → ✓
  validate tenant: customers/contoso/tenant.yaml exists → ✓
  validate env: "prd" ∈ spec.environments[*].name → ✓

# Structure B — zones/{zone}/{tenant}/{env}/deploy.yaml (no customers/ folder)
# Requires a DIFFERENT convention entry:
#   pattern: "zones/{zone}/{tenant}/{env}"
path: zones/europe/contoso/prd/deploy.yaml
  match "zones/{zone}/customers/{tenant}/{env}" → NO MATCH (no "customers" literal)
  → this file needs its own convention with the flat pattern
```

This is the key insight: **variant folder structures require separate convention entries**,
not a single convention with optional markers. Each entry is explicit about what the
path looks like. The `scope` globs can overlap — the engine evaluates all matching
conventions independently.

```yaml
# Supporting both structures in the same repo:
paths:
  - name: zone-tree-with-customers-folder
    scope: "zones/**/customers/**"
    pattern: "zones/{zone}/customers/{tenant}/{env}"
    validate:
      zone: spec.zones[*].name
      tenant: "customers/{tenant}/tenant.yaml"
      env: spec.environments[*].name

  - name: zone-tree-flat
    scope: "zones/**"
    pattern: "zones/{zone}/{tenant}/{env}"
    validate:
      zone: spec.zones[*].name
      tenant: "customers/{tenant}/tenant.yaml"
      env: spec.environments[*].name
```

Scope ordering ensures the more specific `zones/**/customers/**` matches first. If a
file matches both, both are evaluated — but the flat pattern won't match a path that
contains `customers/` as a literal segment (because `{tenant}` would capture
"customers" and fail the tenant file existence check). In practice, operators declare
one or the other — not both.

---

**Deploy-repo usage:**

Deploy repositories have no `ConfigurationModel` with `spec.paths`. For these,
the explicit `configuration` block on the policy carries the convention inline:

```yaml
# In the deployment workspace's configuration
policies:
  - name: deploy-landscape-convention
    type: path_convention
    phase: validate
    enforcement: deny
    configuration:
      # Inline convention — used when spec.path_conventions is absent
      scope: "deploy/**"
      pattern: "deploy/{landscape}/{ring}"
      validate:
        landscape: "deploy/{landscape}/landscape.yaml"
```

When `configuration.scope` + `configuration.pattern` are present on the policy, the
engine uses them directly instead of reading from `spec.paths`. This is the
mechanism for deployment repositories or any context without a configuration model.

---

**Design constraints:**

- **`{segment}` captures exactly one path part** (no `/`). Literal folder names in the
  pattern must match verbatim. `zones/{zone}/customers/{tenant}` has two captures and
  two literals.
- **Pattern is anchored at start of relative path.** Trailing path parts after the last
  segment are ignored (allows ring directories, filenames, etc. to vary freely).
- **Placeholder names link `pattern` → `validate`.** Keys in `validate` must exactly
  match `{name}` placeholders in `pattern`. Placeholders inside validate rule paths
  (e.g., `customers/{tenant}/tenant.yaml`) are also expanded from the same captured dict.
- **File existence rules are always anchored at `work_path` root.** The path in the rule
  is not relative to the file being validated — it's relative to the workspace root.
- **`spec.*` rules require configuration to be loaded** (i.e., `--deep` mode). Without
  it, the policy emits `skipped: no configuration loaded` and does not fail.
- **`file_path` must be added to `PolicyContext`** — the policy engine populates it from
  the file being validated before calling `evaluate()`.
- **Multiple conventions may match a single file.** All are evaluated independently.
  This is intentional — a file at `zones/europe/customers/contoso/prd/deploy.yaml` can
  be checked against both the zone convention and the tenant-registry convention.

---

**Model changes required:**

New field on `ConfigurationSpecModel` (`src/strata/models/configuration_model.py`):

```python
class PathConventionModel(PlatformBaseModel):
    """A single path convention rule for directory structure validation."""

    name: PlatformName = Field(description="Unique convention name for diagnostics")
    scope: str = Field(description="Glob pattern — only files matching this are checked")
    pattern: str = Field(
        description="Path template with {segment} captures, anchored at work_path root"
    )
    validate: Optional[Dict[str, str]] = Field(
        None,
        description="Per-segment validation rules (spec.* or file existence templates)",
    )


# On ConfigurationSpecModel:
paths: Optional[List[PathConventionModel]] = Field(
    None,
    description="Declared directory structure conventions for path validation policy",
)
```

`spec.layering` remains unchanged — it continues to serve its original purpose
(deployment layer metadata). Path conventions are a separate concern.

### `strata validate --path "**" --deep` — fleet-wide scan

Running deep validation across all files in the repository produces a fleet-wide
consistency report, suitable for use as a CI gate on the configuration repository:

```bash
strata validate --path "zones/**/*.yaml" --deep
```

Exit code 3 (validation failure) when any error-level finding exists. Warnings do not
affect exit code but appear in the report.

---

## Implementation Plan

### Rule sets 1–3 (existing gap)

Not yet implemented. Requires extending the `--deep` validation path in
`run_validate_command.py` to run the layer consistency checks after the
`DeploymentService` is loaded.

### Rule set 4 — `PathConventionPolicy`

**Step 1 — New model: `PathConventionModel`**

`src/strata/models/configuration_model.py`:
- Add `PathConventionModel(PlatformBaseModel)` with fields: `name`, `scope`, `pattern`,
  `validate: Optional[Dict[str, str]]`.
- Add `paths: Optional[List[PathConventionModel]]` to `ConfigurationSpecModel`.
- `spec.layering` remains unchanged (separate concern).

**Step 2 — Add `file_path` to `PolicyContext`**

`src/strata/validators/policies/base_policy.py`:
- Add `file_path: Optional[Path] = None` to `PolicyContext`.
- The policy engine (`policy_engine.py`) populates this from the file being validated.

**Step 3 — New policy class**

`src/strata/validators/policies/path_convention_policy.py`:
- `PathConventionPolicy(BasePolicy)` — implements `evaluate(context)`.
- Convention source resolution:
  - If `policy.configuration` has `scope` + `pattern` → use inline convention (deploy-repo mode).
  - Otherwise → read `context.configuration_service.model.spec.paths`.
  - If `configuration.conventions` filter is set → only evaluate named conventions.
- Private `_match_scope(rel_path, scope_glob)` → `bool`.
- Private `_match_pattern(rel_path, pattern)` → `dict[str, str] | None` — splits both on
  `/`, aligns positionally, captures `{name}` segments.
- Private `_validate_segment(name, value, rule, context)` → `str | None` (violation msg):
  - `spec.*` rule: resolve dot-path on configuration model, check membership.
  - File existence rule: expand `{segment}` placeholders, check `work_path / expanded`.
- Register in `__init__.py` and `policy_engine.py` type dispatch.

**Step 4 — Tests**

`tests/strata/validators/policies/test_policies_path_convention.py`:
- Scope matching: file in scope, file outside scope (skip).
- Pattern matching: matching path, non-matching path (skip), trailing parts ignored.
- Multiple conventions: both evaluated, violations from each reported independently.
- Zone validation: valid zone, unknown zone, no configuration service (warn + skip).
- Tenant file existence: file exists, file missing.
- Inline convention (deploy-repo mode): policy with `configuration.scope` + `configuration.pattern`.
- Convention filter: `configuration.conventions` limits which named conventions run.
- Enforcement levels: warn vs deny.

**Step 5 — Documentation**

- `docs/config/configuration.md` — add `paths` to the spec fields table;
  add `path_convention` to the policy types table.

---

## Decisions (resolved from open questions)

1. **Error vs warning** — the `enforcement` level on the policy entry controls this.
   Operators declare `deny` or `warn` per policy. No per-rule granularity needed.
2. **Path heuristic reliability** — v1 uses heuristic matching. Future versions may
   add per-convention segment significance configuration if false positives emerge.
3. **Performance** — caching is out of scope (separate ADR). File-per-file evaluation
   is acceptable for v1.
4. **Surface vs deep** — `path_convention` runs only during `--deep` validation.
   File-existence-only checks also require deep mode (configuration must be loaded to
   resolve `spec.paths`).
5. **Overlapping scopes** — evaluate all matching conventions independently. Operators
   can use the `conventions` filter on the policy to restrict which conventions apply.

---

## Consequences

- Silent misconfiguration errors in fleet deployments are caught in CI rather than
  at deployment time, reducing blast radius.
- Template drift warnings encourage operators to review instantiation files after
  template changes, preventing structural divergence.
- The heuristic nature of layer consistency checks means false positives are possible;
  the warning-not-error default ensures this does not block legitimate deployments.
- ADR 0040 (scaffolding) is the prevention layer; this ADR is the detection layer.
  Both are needed — scaffolding prevents new errors; deep validation catches existing ones.
