# Deep Validation and Layer Consistency

- Status: partial
- Date: 2026-07-15

## TODO

- [x] **Rule Sets 1–3 Implementation Plan** — Implementation steps added for all three rule sets
- [ ] **Configuration Service Dependency Chain** — Define behavior when `--deep` validation runs without configuration service available; clarify `DeploymentService` ↔ `ConfigurationService` coupling
- [ ] **Backwards Compatibility & Migration Path** — Document how operators migrate existing non-compliant files; define timeline for optional → mandatory enforcement
- [ ] **Scope Precedence Algorithm** — Clarify glob matching specificity rules; define ordering when multiple conventions match the same file
- [ ] **Convention Self-Validation** — Add Pydantic validators to `PathConventionModel` to catch malformed patterns and mismatched segment names
- [ ] **Performance Considerations** — Estimate glob matching cost at fleet scale (~1000s of files); identify optimization opportunities for file-existence checks
- [ ] **Failure Mode Diagnostics** — Document edge cases: unmatched files, missing placeholders, permission errors, and appropriate warning/error handling
- [ ] **Segment Naming Conflicts** — Add guidelines to prevent semantic conflicts when multiple conventions reuse the same `{segment}` names
- [ ] **Template Drift Actionability** — Clarify `template_version` update workflow (scaffolding integration per ADR 0040); define what `--check-template-drift` output contains
- [ ] **Uncovered Files Policy** — Define whether files that match no convention scope should warn or silently pass
- [ ] **Inline vs Spec.paths Precedence** — Clarify coexistence and conflict resolution for inline conventions (deploy repos) vs `spec.paths` conventions
- [ ] **ADR 0038 Gap Closure** — Map how this ADR closes Gap 5; add gap closure checklist
- [ ] **End-to-End Example Workflow** — Add complete scenario: operator setup → file structure → validation → remediation

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

### `strata validate run --pattern "**" --deep` — fleet-wide scan

Running deep validation across all files in the repository produces a fleet-wide
consistency report, suitable for use as a CI gate on the configuration repository:

```bash
strata validate run --pattern "zones/**/*.yaml" --deep
```

Exit code 3 (validation failure) when any error-level finding exists. Warnings do not
affect exit code but appear in the report.

---

## Implementation Plan

### Rule set 1 — Layer identity consistency

**Step 1 — New checker module**

`src/strata/validators/layer_consistency_checker.py`:
- `LayerConsistencyChecker` — stateless class with a single public method
  `check(model: DeploymentModel, work_path: Path) -> List[LayerConsistencyWarning]`.
- `LayerConsistencyWarning(dataclass)`: `field: str`, `declared_value: str`,
  `conflicting_segment: str`, `env_path: str`, `message: str`.
- Private `_extract_env_path(ref: DeploymentEnvironmentRef) -> str` — strips `@repo/`
  prefix to produce the bare path string used for segment inspection.
- Private `_check_path_for_foreign_layer(path_str: str, layer_key: str,
  declared_value: str, all_layer_values: Dict[str, str]) -> str | None` — splits
  path on `/`, returns the first segment that matches any known layer value that is
  **not** the declared value for `layer_key`.

Logic for `check()`:
1. If `model.spec.layers` is `None` or `model.spec.environments` is empty → return `[]`.
2. Build `all_layer_values: Dict[str, str]` from `model.spec.layers` (e.g.
   `{"zone": "europe-west", "customer": "contoso", "environment": "dev"}`).
3. For each `(layer_key, declared_value)` in `all_layer_values`:
   - Build a set of all other layer values: `foreign_values = {v for k,v in all_layer_values.items() if k != layer_key}`.
   - For each `env_ref` in `model.spec.environments`:
     - Extract the path string.
     - Split on `/` and look for a segment that matches any value in `foreign_values` AND
       that foreign value belongs to a different key whose declared value does NOT appear
       in the path — emit a `LayerConsistencyWarning`.
4. Return de-duplicated warning list.

**Step 2 — Wire into `run_validate_command.py`**

In `ValidateCommand._run_single_file_execution()`, after the validator phases complete
and only when `self._deep` is `True` and `self._detected_kind == "deployment"`:

```python
if self._deep and self._detected_kind == "deployment":
    from strata.validators.layer_consistency_checker import LayerConsistencyChecker
    deployment_model = self._validator.get_model()  # returns parsed DeploymentModel
    if deployment_model is not None:
        checker = LayerConsistencyChecker()
        layer_warnings = checker.check(deployment_model, self._work_path)
        for w in layer_warnings:
            self._validator.add_warning(w.message)
        if layer_warnings:
            self._output_data.setdefault("layer_warnings", []).extend(
                [w.message for w in layer_warnings]
            )
```

Add `layer_warnings` to the output schema (console and JSON) alongside `warnings`.

**Step 3 — Tests**

`tests/strata/validators/test_layer_consistency_checker.py`:
- No layers block → no warnings.
- Layers block with matching env paths → no warnings.
- `layers.customer = contoso`, env path contains `fabrikam` → one warning, correct field and segment reported.
- Multiple mismatched env paths → one warning per mismatch.
- Env path with `@repo/` prefix → prefix stripped before segment inspection.
- Single-segment paths (e.g. `shared.yaml`) → no false positives.
- All layer values appear in different paths → only foreign-key mismatches reported.

---

### Rule set 2 — Template drift detection

**Step 1 — Resolve template version at validation time**

`src/strata/validators/template_drift_checker.py`:
- `TemplateDriftChecker` — requires `repo_map: Dict[str, str]` and `work_path: Path` at
  construction (same pattern as `PlatformValidator`).
- `check(model: DeploymentModel) -> Optional[TemplateDriftWarning]` — returns a warning
  if a drift condition is detected, `None` otherwise.
- `TemplateDriftWarning(dataclass)`: `template_ref: str`, `template_version: str`,
  `instantiation_version: str`, `message: str`.

Logic for `check()`:
1. If `model.spec.extends` is `None` → return `None`.
2. Resolve the `@repo/path` reference using `resolve_path(work_path, extends, repo_map)`.
   If the file does not exist → return a warning: `"template '{ref}' could not be resolved"`.
3. Parse the template file with `yaml.safe_load`. Extract
   `meta.labels.version` → `template_version: str | None`.
4. Extract `model.meta.annotations.get("template_version")` from the instantiation file
   → `instantiation_version: str | None`.
5. If either version is `None` → return `None` (no version tracking in place; skip).
6. Compare using `packaging.version.Version` (already a transitive dependency via
   other tooling) or simple semver string comparison:
   - `template_version > instantiation_version` → return `TemplateDriftWarning`.
   - Otherwise → return `None`.

**Step 2 — Wire into `run_validate_command.py`**

Same insertion point as Rule Set 1, immediately after layer consistency checks:

```python
if self._deep and self._detected_kind == "deployment":
    from strata.validators.template_drift_checker import TemplateDriftChecker
    repo_map = self._solution_controller.get_repo_map()
    drift_checker = TemplateDriftChecker(repo_map=repo_map, work_path=self._work_path)
    drift_warning = drift_checker.check(deployment_model)
    if drift_warning:
        self._validator.add_warning(drift_warning.message)
        self._output_data.setdefault("template_drift", []).append({
            "template": drift_warning.template_ref,
            "template_version": drift_warning.template_version,
            "reviewed_against": drift_warning.instantiation_version,
        })
```

**Step 3 — `meta.annotations.template_version` population**

Scaffolding (ADR 0040) is responsible for writing `template_version` at creation time.
This ADR requires only that the _reader_ side exists. The annotation key is
`template_version` on `meta.annotations` (a free-form `Dict[str, str]` on
`PlatformMetaModel` — no model change needed).

**Step 4 — Tests**

`tests/strata/validators/test_template_drift_checker.py`:
- No `extends` field → no warning.
- `extends` present, template file missing → warning about unresolvable reference.
- Template and instantiation at same version → no warning.
- Template version newer than instantiation version → drift warning with correct versions.
- Template has no `meta.labels.version` → no warning (skip gracefully).
- Instantiation has no `meta.annotations.template_version` → no warning (skip gracefully).
- Semver comparison edge cases: `1.10` > `1.9`, `2.0` > `1.9`.

---

### Rule set 3 — Tenant field vs path consistency

**Step 1 — Extend `LayerConsistencyChecker`**

Add a second public method to the existing `LayerConsistencyChecker`:

```python
def check_tenant_paths(
    self, model: DeploymentModel
) -> List[LayerConsistencyWarning]:
    ...
```

Logic:
1. If `model.spec.tenant` is `None` → return `[]`.
2. `declared_tenant = model.spec.tenant`.
3. For each `env_ref` in `model.spec.environments`:
   - Extract path string.
   - Heuristic: if `/customers/` appears as a literal segment in the path, this is a
     tenant-specific path → verify `declared_tenant` appears as a path segment
     immediately following `customers/`.
   - If `customers/{X}/` is present and `X != declared_tenant` → emit a warning:
     `"spec.tenant = '{declared_tenant}' but environments[N] path is under customers/{X}/ — possible tenant mismatch"`.
4. Return warning list.

Reuses `LayerConsistencyWarning` dataclass — set `field="spec.tenant"`.

**Step 2 — Wire into `run_validate_command.py`**

Call `checker.check_tenant_paths(deployment_model)` immediately after
`checker.check(deployment_model)` at the same insertion point. Merge warnings into the
same `layer_warnings` output key.

**Step 3 — Tests**

`tests/strata/validators/test_layer_consistency_checker.py` (extend existing file):
- No `tenant` field → no warnings.
- `tenant = contoso`, all env paths under `customers/contoso/` → no warnings.
- `tenant = contoso`, one env path under `customers/fabrikam/` → one warning.
- Env paths without `/customers/` segment → not checked (shared env files).
- Mixed: some tenant-specific, some shared → only tenant-specific paths checked.
- `@repo/` prefix in env path → stripped before segment inspection.

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
