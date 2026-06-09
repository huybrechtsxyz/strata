# Release & Version Management

## Problem Statement

Four independent Git repositories:

- **IaC** — Terraform
- **Helm charts** — Kubernetes deployment manifests
- **Frontend** — React
- **Backend** — Python

Per-customer configuration (values + settings) organized as:

```text
customer / zone / environment / values.yaml, settings.xyz
```

These config files may live in any of the above repos.

### Build Assurance

Every X weeks: release branch → RC build → QA validation → release tag.

The **Helm chart is the source of truth** for application versioning. It serves as the deployment manifest, pinning the exact versions being deployed to production.

### Specific Challenges

1. **Version drift** — Independent repos evolve at different cadences; a backend fix may ship without a matching frontend update.
2. **Ordering constraints** — Terraform infra changes (new queues, databases, permissions) must be applied *before* the Helm deploy that depends on them.
3. **Configuration sprawl** — Per-customer values files multiply the surface area for version mismatches.
4. **Rollback coherence** — Rolling back the Helm chart alone may not be sufficient if Terraform state has already changed.

---

## Solution Design

### 1. Release Manifest (Single Source of Truth)

Introduce a **release manifest file** in the Helm chart repo (or a dedicated release repo) that pins all component versions for a given release:

```yaml
# release-manifest.yaml
release: 2025.3.0
components:
  backend:
    image: registry.omp.com/product/backend
    tag: 2025.3.0-rc.2
    commit: abc1234
  frontend:
    image: registry.omp.com/product/frontend
    tag: 2025.3.0-rc.2
    commit: def5678
  iac:
    ref: refs/tags/infra-2025.3.0
    commit: 789abcd
  helm-chart:
    version: 2025.3.0
```

This manifest is the contract. Nothing deploys unless all components listed here have passing builds.

### 2. Versioning Strategy

| Component  | Version Format           | Trigger                           |
| ---------- | ------------------------ | --------------------------------- |
| Backend    | `YYYY.MINOR.PATCH`       | Merge to release branch           |
| Frontend   | `YYYY.MINOR.PATCH`       | Merge to release branch           |
| Helm Chart | `YYYY.MINOR.PATCH`       | Manifest update (pins image tags) |
| Terraform  | `infra-YYYY.MINOR.PATCH` | Tag on release branch             |

- All components share the same `YYYY.MINOR` for a release train.
- Patch increments independently per component for hotfixes.
- The Helm chart version always matches the release train version.

### 3. Automated Version Bumping

Use automation (Renovate, custom pipeline, or GitHub Actions) to:

1. **Detect new image tags** pushed to the container registry.
2. **Open a PR** in the Helm chart repo updating the image tags in `values.yaml` and the release manifest.
3. **Run integration tests** on the PR to validate compatibility.
4. **Auto-merge** if tests pass and approvals are met.

```
Backend build completes → pushes image:2025.3.0-rc.2
  → Renovate detects new tag
  → PR opened in helm-chart repo updating values.yaml
  → CI runs smoke tests against new combination
  → Merge → triggers deployment pipeline
```

### 4. Deployment Pipeline Orchestration

The deployment pipeline must enforce ordering:

```
┌─────────────────────────────────────────────────────┐
│  Stage 1: Validate                                  │
│  - Verify all manifest versions exist (images, tags)│
│  - Run terraform plan (detect drift)                │
│  - Helm template + lint                             │
├─────────────────────────────────────────────────────┤
│  Stage 2: Infrastructure (Terraform)                │
│  - terraform apply (if changes detected)            │
│  - Wait for resource readiness                      │
├─────────────────────────────────────────────────────┤
│  Stage 3: Application (Helm)                        │
│  - helm upgrade --install                           │
│  - Wait for rollout completion                      │
├─────────────────────────────────────────────────────┤
│  Stage 4: Verify                                    │
│  - Health checks                                    │
│  - Smoke tests                                      │
│  - Notify on success/failure                        │
└─────────────────────────────────────────────────────┘
```

**Key rule:** If Terraform fails, Helm does not execute. If Helm fails, automatic rollback is triggered.

### 5. Per-Customer Configuration Management

```text
config-repo/
  customers/
    acme/
      eu-west/
        production/
          values.yaml       ← overrides only
          settings.xyz
        staging/
          values.yaml
      us-east/
        production/
          values.yaml
  base/
    values.yaml             ← shared defaults
```

- **Base values** define defaults; customer values only contain overrides.
- A **schema validation** step in CI ensures customer values are compatible with the current Helm chart version.
- Version constraints can be embedded: `minimumChartVersion: 2025.3.0`

### 6. Terraform ↔ Helm Synchronization

| Approach                                                       | Pros                        | Cons                       |
| -------------------------------------------------------------- | --------------------------- | -------------------------- |
| **Terraform outputs → Key Vault → Helm values from Key Vault** | Secure, decoupled           | Extra Key Vault dependency |
| **Terraform outputs → config repo PR**                         | Auditable, GitOps           | Slight delay               |
| **Shared state file read in pipeline**                         | Fast, no intermediate store | Tight coupling to pipeline |

**Recommended:** Terraform writes outputs to Azure Key Vault. Helm chart references secrets via CSI driver or external-secrets-operator. This decouples the repos while maintaining sync.

### 7. Rollback Strategy

| Scenario                       | Action                                                                             |
| ------------------------------ | ---------------------------------------------------------------------------------- |
| Helm deploy fails              | Automatic `helm rollback` to previous revision                                     |
| Post-deploy health check fails | Automatic rollback + alert                                                         |
| Terraform + Helm both changed  | Rollback Helm first, then assess Terraform (infra changes are often additive/safe) |
| Need full release rollback     | Re-deploy previous release manifest version                                        |

**Important:** Terraform changes should be designed to be **backward-compatible** — new resources are added before old ones are removed (expand-contract pattern). This ensures Helm rollback remains safe even if Terraform has already applied.

### 8. Build Assurance Integration

1. **Release branch created** → all component repos branch from `main`.
2. **RC builds trigger** → images are pushed, Terraform is planned.
3. **Release manifest PR** → automation opens a PR with all RC versions pinned.
4. **QA validates** the manifest combination in a staging environment.
5. **Release tag** → manifest is tagged, triggering production deployment pipeline.
6. **Hotfix** → patch version bump in affected repo → new manifest patch version → expedited QA → deploy.

---

## Strata Coverage Analysis

### What Strata Handles Today

| Concern                                           | Strata Coverage                                                                        |
| ------------------------------------------------- | -------------------------------------------------------------------------------------- |
| Deployment pipeline ordering (Terraform → Helm)   | ✅ Stages with DAG — `depends_on`, `on_failure: rollback/stop/continue`                 |
| Terraform provisioning                            | ✅ TerraformDeployer + TerraformBuilder (plan, apply, destroy, outputs)                 |
| Helm provisioning                                 | ✅ HelmDeployer + HelmBuilder (upgrade --install per namespace/module)                  |
| Per-customer config management (base + overrides) | ✅ Environment layering — multiple YAML files applied in order, later overrides earlier |
| Cross-repo file references                        | ✅ `@repo_name/path` notation resolved via `repo_map`                                   |
| Terraform drift detection                         | ✅ `strata env drift` (plan -detailed-exitcode)                                         |
| Value resolution (secrets, vars, features)        | ✅ Store integrations (Key Vault, Vault, Consul, etc.) + `values resolve` diagnostics   |
| Health checks post-deploy                         | ✅ Per-stage `health_checks` (HTTP/TCP)                                                 |
| Approval gates                                    | ✅ Per-deployment and per-stage `approvals`                                             |
| Schema validation                                 | ✅ Pydantic models + dynamic validation + `strata validate`                             |
| Terraform → Helm data flow (outputs to values)    | ✅ Stage outputs passed to subsequent stages; integration store reads from Key Vault    |

### Example Deployment YAML (Current Strata)

```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: deployment
meta:
  name: acme_eu_production
spec:
  layers:
    environment: prd
    customer: acme
    zone: eu-west
  workspace:
    file: "@helm-charts/stack/xyz-ws-platform.yaml"
  environments:
    - "@config-repo/base/values.yaml"
    - "@config-repo/customers/acme/eu-west/production/values.yaml"
  stages:
    - name: validate
      provisioner: xyz_iac
      on_failure: stop
    - name: infrastructure
      topology: platform
      depends_on: [validate]
      on_failure: rollback
    - name: application
      provisioner: xyz_helm
      depends_on: [infrastructure]
      on_failure: rollback
      health_checks:
        - name: api_health
          type: http
          url: "https://api.acme.eu.example.com/health"
          timeout: 30
```

---

## Gaps — What's Missing from Strata

| #   | Gap                                            | Impact                                                                                                            | Complexity              |
| --- | ---------------------------------------------- | ----------------------------------------------------------------------------------------------------------------- | ----------------------- |
| 1   | **Release Manifest model**                     | No `kind: release-manifest` to pin component versions (images, tags, commits) across repos in a single file       | Medium                  |
| 2   | **Version correlation / lockfile**             | Can't express "backend:2025.3.0 + frontend:2025.3.0 + iac:infra-2025.3.0 must deploy together"                    | Medium                  |
| 3   | **Container registry awareness**               | No integration to detect new image tags or validate image existence before deploy                                 | Medium                  |
| 4   | **Automated version bumping** (Renovate-like)  | Strata doesn't watch external sources and open PRs/update values when new versions appear                         | High (pipeline concern) |
| 5   | **Rollback command**                           | No `strata deploy rollback` — deployers support destroy but not "revert to previous revision"                     | Medium                  |
| 6   | **Multi-repo clone/sync with git ref pinning** | `@repo_name` resolves paths but doesn't clone/checkout repos or pin to specific git refs (branch/tag/commit)      | Medium                  |
| 7   | **Cross-stage output piping contract**         | `stage_outputs` exists in RunDeployCommand but isn't formalized as a model contract — fragile                     | Low                     |
| 8   | **Pre-deploy artifact validation**             | No "verify all manifest versions exist" step (check registry for images, check Terraform module versions)         | Medium                  |
| 9   | **Release train / branch coordination**        | No concept of "release branch → RC → QA gate → production tag" lifecycle                                          | High (process)          |
| 10  | **Expand-contract migration awareness**        | No way to express "this Terraform change is backward-compatible" or "safe to rollback Helm while Terraform stays" | Low-Medium              |

---

## Recommendations (Priority Order)

### Quick Wins (Extend Existing Architecture)

1. **Formalize cross-stage output contract** — add an `outputs` model to stages so Terraform outputs automatically feed Helm values
2. **Git ref pinning in repo_map** — extend `@repo_name` to support `@repo_name@tag/path` or add a `ref` field to workspace/environment file references
3. **`strata deploy rollback`** — call `helm rollback` for Helm stages, previous state for Terraform

### New Capabilities Needed

4. **Release Manifest (`kind: release-manifest`)** — a new YAML document type that pins component versions, validated before any deploy runs
5. **Registry integration** — check image existence + tag freshness (supports the "validate" stage)
6. **Pre-flight validator** — given a manifest, verify all components exist and are reachable before pipeline starts

### Out of Scope for CLI (Pipeline/GitOps Layer)

7. Automated version bumping (Renovate / pipeline webhook) — not a CLI concern
8. Release train lifecycle (branch strategy, QA gates) — process, not tooling
9. Event-driven triggers (registry push → PR) — CI/CD platform feature

---

## Summary

| Concern                    | Solution                                           |
| -------------------------- | -------------------------------------------------- |
| Version correlation        | Release manifest pins all components               |
| Automation                 | Renovate/pipeline bumps image tags in Helm values  |
| Ordering                   | Pipeline enforces Terraform → Helm sequence        |
| Config management          | Base/override pattern with schema validation       |
| Terraform ↔ Helm data flow | Key Vault + external-secrets-operator              |
| Rollback                   | Helm auto-rollback + expand-contract for Terraform |
| Auditability               | Git history on manifest + tagged releases          |

The biggest architectural gap is **the release manifest concept** — strata has all the deployment mechanics but lacks a single document that says "these exact versions go together." Everything else is either already present or a natural extension of existing patterns.
