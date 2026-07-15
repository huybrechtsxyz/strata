# GitOps Controller Integration

- Status: proposed
- Date: 2026-07-15

## Context and Problem Statement

In fleet-scale deployments (ADR 0038) strata manages the configuration source of truth:
environment variable/secret layers, version-lock files, and promotion records. The actual
application workloads on Kubernetes are deployed by a GitOps controller (e.g., ArgoCD
ApplicationSets) that watches the configuration repository and reconciles the cluster state.

This relationship is currently **implicit and unverified**:

- Strata knows what it intends to be deployed (version-lock files, resolved env vars).
- The GitOps controller knows what is actually running (reconciliation status, sync state).
- There is no contract between them — the controller reads git changes and acts; strata has
  no way to confirm reconciliation succeeded or detect drift between intended and actual state.

Additionally, the controller needs structured input to generate Applications across hundreds
of tenants. Today this is typically achieved by a controller-specific generator (e.g., ArgoCD
ApplicationSet git generator reading path patterns) with no awareness of strata's layer model,
version-lock files, or tenant metadata.

## Related Work

- **ADR 0038 — Multi-Tenant Fleet Management Patterns**: identifies this as Gap 4 (Medium).
- **ADR 0037 — Fleet Operations and Mass Wave Deployment**: fleet deploy execution; GitOps
  health verification is the post-deploy confirmation step.
- **ADR 0011 — Promotion Strategies**: version-lock files are the artefact the GitOps
  controller should consume for chart/image version resolution.
- **ADR 0018 — Deployment Audit Traceability**: reconciliation status from the controller
  should be recorded in the audit log as a deployment confirmation event.

---

## Design Overview

### Two surfaces: output and health

This ADR covers two complementary capabilities:

1. **`strata build gitops`** — emit a structured fleet manifest consumable by an
   ApplicationSet generator, making strata the authoritative source for what the
   controller should deploy.

2. **`strata deploy health --gitops`** — query the GitOps controller's API for
   reconciliation status and surface the result in strata's health model.

---

### Surface 1: `strata build gitops`

```bash
strata build gitops -f zones/europe-west/customers/contoso/dev/deploy.yaml --output json
strata build gitops --ring prd --output json   # fleet mode (ADR 0037 discovery)
```

Emits a structured manifest per deployment:

```json
{
  "tenant": "contoso",
  "zone": "europe-west",
  "ring": "dev",
  "layers": { "zone": "europe-west", "customer": "contoso", "environment": "dev" },
  "namespace": "contoso-dev",
  "chart_versions": {
    "integrator-core": "2.4.1",
    "integrator-worker": "2.4.1"
  },
  "variables": {
    "api_base_url": "https://api.dev.contoso.example.com",
    "log_level": "debug"
  }
}
```

The manifest is designed to be consumed as an **ApplicationSet list generator source**
(ArgoCD) or equivalent. Each entry contains the resolved values the controller needs
without requiring the controller to understand strata's layer model or version-lock format.

Chart versions are resolved from the active version-lock file for the deployment's ring —
the controller does not need to read version-lock files directly.

### Surface 2: `strata deploy health --gitops`

Extends the existing `strata deploy health` command with a GitOps reconciliation check:

```bash
strata deploy health -f deploy.yaml --gitops
```

Queries the configured GitOps controller API (ArgoCD, Flux) for the Application(s)
associated with this deployment and reports:

| Field               | Source                                            |
| ------------------- | ------------------------------------------------- |
| `sync_status`       | Controller API (Synced / OutOfSync / Unknown)     |
| `health_status`     | Controller API (Healthy / Degraded / Progressing) |
| `last_synced_at`    | Controller API                                    |
| `revision`          | Controller API — git SHA last reconciled          |
| `intended_revision` | Strata — HEAD commit of config repo at build time |
| `drift`             | `true` when `revision != intended_revision`       |

Controller connection is configured in `.strata/cli.yaml`:
```yaml
gitops:
  provider: argocd          # argocd | flux
  url: https://argocd.platform.example.com
  token_secret: argocd_token
```

### Audit integration

When `--gitops` is used after a deployment, the reconciliation result is appended to
the deployment's audit record (ADR 0018), providing end-to-end traceability:
strata intended → strata applied → controller reconciled.

---

## Open Questions

1. **Provider scope** — ArgoCD first, Flux as Phase 2, or design a provider interface
   from the start?
2. **Pull vs push** — should strata poll for reconciliation status (blocking health check)
   or register a webhook and receive a callback? Webhook requires network access to strata;
   polling is simpler.
3. **ApplicationSet ownership** — should strata generate and manage the ApplicationSet
   resource itself (write to cluster), or only emit the generator source data (write to
   file, ArgoCD reads)? The latter keeps strata out of the cluster and preserves GitOps
   principles.
4. **Variable exposure** — the gitops manifest includes resolved variable values. Secrets
   must never appear in the manifest; variables that are sensitive by convention should
   be opt-out suppressible.

---

## Consequences

- Strata becomes the single source of truth for what the GitOps controller deploys:
  resolved versions from version-lock files, resolved variables from the layer stack.
- The controller no longer needs to understand strata's directory conventions — it reads
  a structured manifest.
- Promotion records in strata correlate with reconciliation events in the controller,
  closing the audit loop from "intended" to "confirmed deployed."
- Keeping strata out of direct cluster writes preserves GitOps principles and avoids
  competing with the controller for cluster ownership.
