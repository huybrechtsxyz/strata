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

### `strata validate --path "**" --deep` — fleet-wide scan

Running deep validation across all files in the repository produces a fleet-wide
consistency report, suitable for use as a CI gate on the configuration repository:

```bash
strata validate --path "zones/**/*.yaml" --deep
```

Exit code 3 (validation failure) when any error-level finding exists. Warnings do not
affect exit code but appear in the report.

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
