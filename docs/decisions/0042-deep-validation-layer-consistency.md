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

Checks that the file's own path on disk is consistent with declared zones and tenants.
Triggered during `validate` phase when a policy of type `path_convention` is present in
`configuration.spec.policies`.

**Configuration format:**

```yaml
policies:
  - name: fleet-path-convention
    type: path_convention
    phase: validate
    enforcement: deny
    description: "Deployment files under zones/ must use declared zone and tenant names"
    configuration:
      pattern: "zones/{zone}/customers/{tenant}"
      validate:
        zone: spec.zones[*].name          # resolved against ConfigurationModel
        tenant: customers/{tenant}/tenant.yaml  # file existence check relative to work_path
```

**`pattern`** — a path fragment (not anchored to work_path root) that the file's path must
contain. Named `{segment}` placeholders capture the actual values from the path.

**`validate`** — per-segment validation rule. Two rule types:

| Rule syntax | Meaning |
|---|---|
| `spec.zones[*].name` | Value must appear in a field of the loaded ConfigurationModel |
| `customers/{segment}/tenant.yaml` | File at this path (relative to work_path) must exist |

**Evaluation steps:**

1. Compute the file path relative to `work_path`.
2. Attempt to match the `pattern` against the relative path. If no match → skip (file is
   not under the convention tree; not a violation).
3. Extract the named segment values from the match.
4. For each segment with a `validate` rule:
   - `spec.*` rules: resolve the dot-path against the `ConfigurationModel` (requires active
     profile and configuration service in context). If no configuration service → warn and skip.
   - File existence rules: expand `{segment}` placeholders in the rule path and check
     `work_path / expanded_path` exists.
5. Emit a violation for each segment whose value is not found in the validation set.

**Example output:**

```
DENY  zones/atlantis/customers/contoso/dev/deploy.yaml
      path segment 'zone' = 'atlantis'
      not in configuration.spec.zones[*].name: [europe, nordics, us-east, us-west]

DENY  zones/europe/customers/unknown-co/prd/deploy.yaml
      path segment 'tenant' = 'unknown-co'
      customers/unknown-co/tenant.yaml does not exist
```

**Design constraints:**

- Pattern matching is prefix-based on forward-slash-split path parts. Patterns do not
  use glob syntax — `{segment}` captures exactly one path segment (no `/`).
- If `configuration_service` is absent and a `spec.*` rule is required → policy emits a
  warning (`skipped: no configuration loaded`) rather than failing. Deep validation
  (`--deep`) guarantees the configuration is loaded; surface validation does not.
- `file_path` must be added to `PolicyContext` — it is not currently present. The policy
  engine populates it from the file being validated before calling `evaluate()`.

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

**Step 1 — Add `file_path` to `PolicyContext`**

`src/strata/validators/policies/base_policy.py`:
- Add `file_path: Optional[Path] = None` to `PolicyContext`.
- The policy engine (`policy_engine.py`) must populate this from the file path being
  validated before calling `evaluate()` on each policy.

**Step 2 — New policy class**

`src/strata/validators/policies/path_convention_policy.py`:
- `PathConventionPolicy(BasePolicy)` — implements `evaluate(context)`.
- Private `_match_pattern(rel_path, pattern)` → `dict[str, str] | None` — splits both on
  `/`, aligns positionally, captures `{name}` segments.
- Private `_validate_segment(name, value, rule, context)` → `str | None` (violation or None):
  - `spec.*` rule: resolve dot-path on `context.configuration_service.model`, check membership.
  - File existence rule: expand `{segment}` in rule path, `Path(work_path / expanded).exists()`.
- Register in `src/strata/validators/policies/__init__.py`.
- Register in `policy_engine.py` type dispatch (`"path_convention": PathConventionPolicy`).

**Step 3 — Tests**

`tests/strata/validators/policies/test_policies_path_convention.py`:
- Pattern match: matching path, non-matching path (skip), partial match.
- Zone validation: valid zone, unknown zone, no configuration service (warn + skip).
- Tenant file existence: file exists, file missing.
- Mixed: both violations in one file, enforcement=warn vs deny.

**Step 4 — Documentation**

`docs/config/configuration.md` — add `path_convention` to the policy types table with
the `pattern` + `validate` fields documented.

---

## Open Questions

1. **Error vs warning classification** — layer mismatch is a likely mistake but could
   be intentional (shared env files). Should it be configurable per-rule whether a
   finding is an error or a warning?
2. **Path heuristic reliability** — the layer-in-path heuristic works for the canonical
   directory structure described in ADR 0038 but may produce false positives for non-standard
   layouts. Should operators be able to configure which path segments are significant?
3. **Performance at fleet scale** — running `--deep` across N×M×K files in CI must be
   fast. Resolution must be cached; template hash comparison must not require full
   model resolution.
4. **Rule set 4 — surface vs deep** — `path_convention` with `spec.*` rules requires the
   configuration to be loaded, which currently only happens under `--deep`. Should a
   file-existence-only `path_convention` policy run at surface validation without `--deep`?

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
