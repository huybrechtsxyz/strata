# Multi-Tenant Fleet Management Patterns and Gaps

- Status: proposed
- Date: 2026-07-14

## Context and Problem Statement

Strata is being adopted by SaaS platform operators who manage deployments at significant scale:
tens to hundreds of tenants, across multiple geographic zones, each with multiple environment
rings (dev, qas, prd). The primary motivation for adopting strata in this scenario is **not**
infrastructure provisioning — it is structured, auditable management of environment configuration
and version progression that cannot be expressed in platform-native tools such as ADO Library
Groups or GitHub Actions secrets/variables.

Observing this usage pattern reveals:

1. **What works well** — the layered environment model, version-lock files, and promotion system
   are highly compelling at this scale. The gap between what strata offers and what ADO/GitHub
   provide natively is large enough to justify adoption even when infrastructure provisioning
   is minimal.

2. **What is missing** — several gaps emerge at fleet scale that are not visible in small
   single-tenant deployments. These gaps add operational friction and limit the value strata
   can deliver.

This ADR records the observed patterns, the gaps, and the design directions needed to address them.

---

## Observed Usage Pattern

A typical fleet-scale operator structures their configuration repository as follows:

```
config/
  base.yaml                          # configuration — store policies, layers, remotes
  layers.yaml                        # layer definitions: zone, customer, environment
zones/
  <zone>/
    env.yaml                         # zone-level environment defaults
    customers/
      <tenant>/
        env.yaml                     # zone × tenant overrides
        deploy.yaml                  # tenant onboarding deployment (Terraform)
        <ring>/
          env.yaml                   # zone × tenant × ring overrides
          deploy.yaml                # ring deployment (Terraform + app workloads)
customers/
  <tenant>/
    tenant.yaml                      # tenant metadata
    <ring>/
      env.yaml                       # tenant × ring defaults (zone-agnostic)
workspaces/
  workspace-<type>.yaml              # shared workspace definitions (one per topology type)
```

**Environment merge chain** for a single deployment:

```
zones/<zone>/env.yaml                          ← zone baseline
  customers/<tenant>/<ring>/env.yaml           ← tenant × ring defaults (zone-agnostic)
    zones/<zone>/customers/<tenant>/<ring>/env.yaml  ← zone × tenant × ring specifics
```

Each layer narrows scope and overrides keys from the layer above. The deployment file is
the assembly instruction that names which layers to compose for a specific
`zone × tenant × ring` combination.

**What the operator replaces with strata:**

| ADO / GitHub native                       | Strata equivalent                               |
|-------------------------------------------|-------------------------------------------------|
| ADO Library Groups (flat, no history)     | Environment YAML (git-tracked, PR-reviewed)     |
| GitHub secrets (runtime-only)             | Secret store references (auditable declarations)|
| No version tracking across environments   | Version-lock files per ring                     |
| No promotion workflow                     | Waves, rings, gates, canary overlays            |
| No SBOM                                   | CycloneDX 1.6 SBOM across full fleet            |
| Pipeline logs (per-pipeline, ephemeral)   | Structured audit log with execution IDs         |
| Approval gates (invisible to code review) | `spec.approvals` declared in deployment file    |

**Why version-lock and promotion are the primary payoff:**

A new chart version must reach hundreds of tenants in a controlled way. The version-lock
pattern enables:

```
versions/dev.yaml   →   versions/qas.yaml   →   versions/prd.yaml
                                                       ↑
                                          versions/prd.<tenant>.yaml  ← canary
```

One promotion PR touches one lock file. All tenants in the ring move together, or a specific
tenant is canary-targeted first. ADO has no equivalent mechanism.

**Topology is structural, not operational, at this scale:**

In fleet deployments the topology block in workspace YAML is present purely to satisfy the
schema. There is no dynamic Ansible inventory requirement — infrastructure is managed by
Terraform, and application workloads are deployed by an external GitOps controller
(e.g., ArgoCD ApplicationSets) that reads strata's resolved configuration. The topology
describes the target cluster type (`kubernetes`, `standalone`) but drives no runtime
behaviour within strata itself.

---

## Gaps Identified

### Gap 1 — Deploy.yaml proliferation

Every `zones/<zone>/customers/<tenant>/<ring>/deploy.yaml` is structurally identical: same
workspace reference, same stage structure, differing only in `layers`, `tenant`, and the
env file list. At N tenants × M zones × K rings, this produces N×M×K nearly-identical
files, each of which must be kept in sync manually when the workspace or stage structure changes.

**Impact:** High. Any structural change to the deployment (new stage, changed workspace)
requires updating hundreds of files. Onboarding a new tenant requires authoring multiple
files by hand.

**Design direction:** A deployment template mechanism — a base deployment file that declares
the invariant structure, with a thin per-tenant instantiation file that supplies only the
`layers`, `tenant`, and env file overrides. See also ADR 0037 (mass wave deployment) which
already assumes discovery of a fleet of deployment files.

---

### Gap 2 — Tenant onboarding friction

Adding a new tenant to the fleet requires manually creating files in multiple directories
across the correct path structure. A mistake in the path (`zones/europe-west/customers/` vs
`customers/`) is not caught until a deployment is attempted.

**Impact:** Medium-High. At low tenant count this is manageable; as the fleet grows it
becomes a source of configuration errors and slows adoption of the platform.

**Design direction:** `strata new tenant --name <name> --zones <z1> <z2> --rings dev qas prd`
that scaffolds the full tenant tree from a template, validates path consistency, and
optionally pre-populates env file stubs.

---

### Gap 3 — No fleet-level visibility

`strata deploy status -f <file>` operates on one deployment. There is no command to answer:

- What version of service X is deployed across all tenants in prd?
- Which tenants are on the canary ring?
- Which tenants have unresolved secret rotation warnings?
- Did the last wave succeed for all tenants in europe-west?

At fleet scale, per-deployment commands are insufficient for operational oversight.

**Impact:** High. Operators must script their own aggregation over hundreds of deployments,
or rely on external dashboards that are not aware of strata's version-lock model.

**Design direction:** Fleet-level read commands that operate across all deployments matching
a filter (ring, zone, tenant, wave). `strata fleet status --ring prd` and
`strata promote matrix --ring prd` (reading lock files across the fleet) are the minimum
viable surface. ADR 0037 already proposes fleet-level deploy execution; fleet-level
visibility is the read-only counterpart.

---

### Gap 4 — GitOps controller integration is implicit

The version-lock files and resolved environment variables are the source of truth strata
manages. A GitOps controller (e.g., ArgoCD) acts on git changes to the config repository
and renders those values into running workloads. This relationship is implicit — strata
has no visibility into whether the controller has reconciled, and the controller has no
structured contract with strata's build output format.

**Impact:** Medium. Today the operator must verify reconciliation through the GitOps
controller's own UI/API. Promotion records show what strata intended; there is no
confirmation of what was actually deployed.

**Design direction:** A `strata build gitops -f deploy.yaml` output mode that emits a
structured fleet manifest (JSON/YAML) consumable as an ApplicationSet generator source.
A `strata deploy health` integration hook that can query the GitOps controller for
reconciliation status. These keep strata as the control plane while the GitOps controller
remains the execution engine.

---

### Gap 5 — Layer consistency is not validated

The `layers` block in a deployment file (`zone: europe-west`, `customer: contoso`,
`environment: dev`) is metadata. It is not cross-validated against the paths of the
`environments[]` files listed in the same deployment. A deployment could declare
`layers.customer: contoso` while referencing an env file from a different tenant's
path — the error would only surface at runtime when resolved values are unexpected.

**Impact:** Low-Medium. This is primarily an onboarding and copy-paste error vector.
Automated scaffolding (Gap 2) would eliminate most occurrences.

**Design direction:** Add a `--deep` validation rule that checks `layers.*` values against
the directory structure of referenced `environments[]` files, warning when the declared
layer identity does not match the path of any referenced file.

---

## Decision

Accept this ADR as a design record capturing the fleet-scale usage pattern and the gaps
above as inputs to the strata roadmap. The gaps are ranked by impact:

| Priority | Gap                             | Proposed ADR / Feature          |
|----------|---------------------------------|---------------------------------|
| 1        | Fleet-level visibility          | Extend ADR 0037 read-side       |
| 2        | Deploy.yaml proliferation       | New ADR — deployment templates  |
| 3        | Tenant onboarding friction      | Extend ADR 0014 scaffolding     |
| 4        | GitOps controller integration   | New ADR — gitops output mode    |
| 5        | Layer consistency validation    | Extend `strata validate --deep` |

No changes to the current strata schema or CLI are made by this ADR. Each gap will be
addressed in a follow-on ADR or issue.

## Consequences

- The layered environment model (ADR 0003) is validated by real-world fleet usage. No
  structural changes to the model are indicated.
- The version-lock and promotion system (ADR 0011) is the primary adoption driver at
  fleet scale. Continued investment in that surface is justified.
- The topology block in workspace YAML is functioning as a structural declaration for
  fleet deployments that do not require dynamic inventory. No change needed; this usage
  is intentional and supported.
- Ansible dynamic inventory (topology → `stage_outputs`) remains the correct mechanism
  for deployments where infrastructure connection details are not known until Terraform
  applies. This capability is orthogonal to fleet-scale config management.
