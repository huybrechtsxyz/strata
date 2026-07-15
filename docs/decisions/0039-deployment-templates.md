# Deployment Templates

- Status: proposed
- Date: 2026-07-15

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

### Core concept: base template + thin instantiation

A **deployment template** (`kind: deployment-template`) declares the invariant structure:
workspace reference, stage list, locking, approval configuration. A **deployment instantiation**
(`kind: deployment`) references the template and supplies only the values that vary per tenant:

```yaml
# templates/customer-ring-template.yaml
apiVersion: strata.omp.com/v1
kind: deployment-template
meta:
  name: customer-ring-template
spec:
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
# zones/europe-west/customers/contoso/dev/deploy.yaml
apiVersion: strata.omp.com/v1
kind: deployment
meta:
  name: deploy-contoso-westeurope-dev
spec:
  extends: "@config/templates/customer-ring-template.yaml"
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

### Merge semantics

- Fields declared in `extends` are the base.
- Fields present in the instantiation file **override** the base (top-level field replacement,
  not deep merge, to keep behaviour predictable).
- `stages` is treated as a **named list** — instantiation entries are merged by `name` into the
  base list, allowing per-tenant stage overrides (e.g. a tenant-specific `on_failure` or
  `health_checks`) without redefining the full stage list.
- `environments` is always **appended** after any environments declared in the template
  (template environments = shared base; instantiation environments = tenant-specific layers).

### Validation

- `strata validate` resolves `extends` before validating — the merged model is what is checked.
- A `--check-template-drift` flag on `strata validate --path "**"` detects instantiation files
  whose base template has changed since the instantiation was last updated (hash comparison
  against the template's `meta.labels.version`).

### Compatibility with fleet discovery (ADR 0037)

Fleet discovery reads `kind: deployment` files. Template-instantiation files are `kind:
deployment` files — they are fully resolved at load time. The fleet sees the merged model;
the template mechanism is transparent to fleet operations.

---

## Open Questions

1. **Deep merge vs replacement for `stages`** — named-list merge is ergonomic but adds
   complexity to the merge algorithm. Alternative: instantiation may only append stages, not
   override existing ones.
2. **Template versioning** — should templates carry a `meta.labels.version` that instantiations
   pin to, preventing silent drift? Or is git history sufficient?
3. **Circular extends** — must be detected and rejected at load time.
4. **Multi-level inheritance** — template extending another template. Useful but adds complexity;
   may be deferred to Phase 2.

---

## Consequences

- Deployment files for fleet-scale repos reduce from N×M×K boilerplate files to a small set
  of templates + thin instantiation files.
- `strata validate` must resolve `extends` before schema validation — load order matters.
- ADR 0040 (tenant onboarding scaffolding) can generate instantiation files from a template,
  further reducing manual authoring.
- Existing deployment files without `extends` are unaffected — the feature is purely additive.
