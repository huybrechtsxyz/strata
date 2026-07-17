# Operating Strata at Scale — Multi-Tenant Design

> **Status:** Design draft — not yet implemented.  
> **Context:** A company using strata to manage 100+ tenants across multiple regions with shared infrastructure and dedicated application resources.

---

## Scenario

A managed services company operates a platform for 100+ tenants. Each tenant has:

- A **code** (e.g. `acme`, `contoso`) — unique short identifier
- A **name** (e.g. "Acme Corporation")
- **Allowed regions/zones** — constrained by GDPR, data residency, or contractual requirements
- **Environments** — typically `dev`, `test`, `acceptance`, `production`
- A **tier** — determines sizing, HA, and feature availability

Key constraints:

- **In-house deployments only** — internal ops team, no tenant self-service
- **Shared infrastructure** — one AKS cluster (or similar) per zone/region, shared networking and ingress
- **Dedicated application resources** — per-customer namespace, web app, database, DNS, secrets
- **Evolving landscapes** — new services get added, old ones get deprecated; changes should be non-breaking where possible
- **Team isolation** — each team owns its own landscape and configuration independently; teams collaborate at shared interfaces (providers, base charts, global policies) but a team's entire config tree is separate from others
- **Multiple landscapes** — different teams own different landscapes with their own workspaces
- **Customer onboarding** — ~1/week; each customer gets its own config file for clean Git history

---

## Three-Layer Bootstrap Architecture

Infrastructure is bootstrapped in three layers, each with its own lifecycle and blast radius:

```
Team
│
├── Global Bootstrap (once)
│   └── Identity, DNS zones, policies, cost management, audit logging
│
├── Zone Bootstrap (per zone)
│   ├── eu (westeurope + northeurope)
│   │   └── AKS, VNet, ACR, Key Vault, ingress, monitoring, ArgoCD
│   └── us (eastus + eastus2)
│       └── AKS, VNet, ACR, Key Vault, ingress, monitoring, ArgoCD
│
└── Customer Bootstrap (per customer, per zone)
    ├── acme: namespace, RBAC, secrets scope, storage, network policies
    ├── contoso: namespace, RBAC, secrets scope, storage, network policies
    └── ...
```

### Layer 0: Global Bootstrap (one-time)

Organization-wide resources that exist once across all landscapes:

- Entra ID / identity and service principals
- Global DNS zones
- Governance policies and compliance standards
- Centralized monitoring (Log Analytics workspace)
- Cost management and billing

Managed as a single strata deployment. Changes very rarely.

### Layer 1: Zone Bootstrap (per zone, stable)

Shared infrastructure within a zone. One strata deployment per zone:

```yaml
kind: deployment
meta:
  name: landscape_alpha_eu
spec:
  properties:
    landscape: alpha
    zone: eu
  workspace: infrastructure
  environments: [zones/eu.yaml, production.yaml]
  stages:
    - name: infrastructure        # terraform: AKS, VNet, ACR, Key Vault
    - name: configure             # ansible: cluster setup, cert-manager
    - name: platform_services     # helm: ArgoCD, traefik, prometheus, loki
```

**Count:** ~2–6 deployments per landscape. Changes rarely. Standard strata workflow.

**Key resources per zone:** AKS cluster, VNet + subnets, ACR (container registry), Key Vault (zone-scoped, path-based isolation per customer), ArgoCD instance, monitoring stack. The zone’s environment file selects which provider regions are targeted.

### Layer 2: Tenant Bootstrap (per tenant, per zone)

Tenant-specific resources provisioned **into** an existing zone. Creates the isolation boundary before apps are deployed:

- Kubernetes namespace + RBAC policies
- Key Vault secret scope (path-based: `tenants/{code}/*`)
- Tenant storage account
- Network policies (ingress/egress rules)
- DNS records

This is what the **tenant slot** concept automates (see below).

### Tenant Slots (dedicated, per-tenant)

A **tenant slot** is a lightweight deployment of dedicated resources **into** an existing zone. It covers two concerns:

1. **Infrastructure bootstrap** (Terraform, via strata) — namespace, RBAC, secrets scope, storage, network policies
2. **Application deployment** (Helm, via ArgoCD) — the actual app instances running in the tenant's namespace

Strata handles concern #1 and generates the ArgoCD ApplicationSet entries for concern #2. This separation means:

- Strata manages infrastructure lifecycle (create, update, destroy namespaces and resources)
- ArgoCD manages application lifecycle (deploy, upgrade, rollback app versions)
- App teams own their Helm values; platform team owns base charts and infrastructure

**Count:** ~100 tenants × 4 environments = ~400 slots. Generated from a registry, not hand-authored.

---

## Workspace-per-Layer Pattern

Each deployment layer owns **one workspace file**. This is the single most important
structural decision for operating strata at scale.

### Why one workspace per layer?

A workspace defines all possible resources and modules for a deployment scope. When
two deployment scopes share a workspace, the workspace grows to accommodate both —
and both must be kept in sync even when only one changes.

One workspace per layer keeps each workspace small (2–5 resources, 1–3 provisioners),
scoped to its layer's blast radius, and independently evolvable.

| Pattern                      | Workspace size  | Blast radius    | Independent evolution         |
| ---------------------------- | --------------- | --------------- | ----------------------------- |
| One workspace for everything | 20–50 resources | Entire platform | ❌ Changes affect all layers   |
| One workspace per layer      | 2–5 resources   | That layer only | ✅ Layers evolve independently |

### The three workspaces

For the three-layer architecture above, this means three workspace files:

```
workspaces/
  bootstrap.yaml        ← Layer 0: Key Vault, ACR, state backend
  infrastructure.yaml   ← Layer 1: AKS, VNet, ArgoCD, monitoring
  application.yaml      ← Layer 2: namespace, RBAC, secrets scope, storage
```

Each workspace defines the resources and modules for its layer. Deployments reference
exactly one workspace. The workspace does not know about other layers — it only knows
about its own resources.

### Why this works at 400+ deployments

The 400 tenant slots (100 tenants × 4 environments) all reference `application.yaml`.
The workspace defines what *can* exist in a tenant deployment. The environment files
control what *is* active for each tenant and environment. No workspace changes are
needed when onboarding a new tenant — only a new deployment manifest and environment
overrides are needed.

```
application.yaml         ← defines: namespace, RBAC, webapp, storage, cdn, monitoring
    ↑
    referenced by 400 deployment manifests
    each deployment selects its active resources via environment overrides
```

### Layer boundaries are enforced by convention

A deployment manifest in Layer 2 references `workspace: application`. If someone
accidentally puts `workspace: infrastructure` in a customer deployment, they get access
to zone-level resources — a misconfiguration that crosses the layer boundary.

`strata validate run --pattern "deployments/**"` (GAP-03 overlap check) detects namespace
and artifact_path collisions that would result from this, providing a safety net even
without an explicit layer-boundary rule.

### Example deployment structure

```
deployments/
  bootstrap.yaml                    # Layer 0, workspace: bootstrap
  zones/
    eu.yaml                         # Layer 1, workspace: infrastructure
    us.yaml                         # Layer 1, workspace: infrastructure
  tenants/
    acme-eu-prd.yaml                # Layer 2, workspace: application
    acme-eu-acc.yaml                # Layer 2, workspace: application
    contoso-eu-prd.yaml             # Layer 2, workspace: application
    ...
```

The directory structure mirrors the layer hierarchy. Each file references the workspace
for its layer. New tenants = new files, no workspace changes.

---

## New Concepts

### Tenant Registry (`kind: tenant`)

One YAML file per tenant. Onboarding = adding a file. Offboarding = removing it. Git blame shows who changed what for which tenant.

The tenant file carries an `environments` list — these are environment files that define the tenant's capability profile (sizing, modules, HA, features). There is no separate "tier" concept; tiers are just environment files by convention (e.g. `environments/tiers/enterprise.yaml`).

#### Environment Merge Order

At slot generation, strata merges two environment lists:

1. **Tenant environments** — from the tenant file (`spec.environments`)
2. **Deployment environments** — from the deployment matrix (lifecycle stage)

Later layers override earlier ones. This means deployment-level settings (secrets, endpoints, approval gates) always win over tenant-level settings (sizing, modules).

```
workspace (all resources/modules defined)
    ↓ tenant environments applied (tier: toggle modules, set sizing)
        ↓ deployment environments applied (lifecycle: secrets, endpoints)
            = final resolved configuration for this slot
```

#### Per-Tenant File (`tenants/{code}.yaml`)

```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: tenant
meta:
  name: acme
  annotations:
    description: "Acme Corporation"
  labels:
    landscape: alpha
spec:
  code: acme
  name: "Acme Corporation"
  zones: [eu]
  onboarded: 2025-03-15
  environments:
    - environments/tiers/enterprise.yaml
  features:
    new_dashboard: true
```

```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: tenant
meta:
  name: globex
  annotations:
    description: "Globex Inc"
  labels:
    landscape: alpha
spec:
  code: globex
  name: "Globex Inc"
  zones: [eu]
  onboarded: 2026-06-01
  environments:
    - environments/tiers/standard.yaml
    - environments/tenants/globex-overrides.yaml  # tenant-specific tweaks
```

#### Tier Environment Files

Tiers are standard `kind: environment` files that use the existing override mechanism to toggle modules and set sizing:

```yaml
# environments/tiers/enterprise.yaml
apiVersion: strata.huybrechts.xyz/v1
kind: environment
meta:
  name: tier_enterprise
  annotations:
    description: "Enterprise tier — HA, monitoring, CDN"
spec:
  properties:
    tier: enterprise
    ha_enabled: true
  overrides:
    resources:
      - name: webapp
        count: 3
      - name: monitoring
        enabled: true
      - name: backup
        enabled: true
      - name: cdn
        enabled: true
```

```yaml
# environments/tiers/standard.yaml
apiVersion: strata.huybrechts.xyz/v1
kind: environment
meta:
  name: tier_standard
  annotations:
    description: "Standard tier"
spec:
  properties:
    tier: standard
  overrides:
    resources:
      - name: webapp
        count: 1
      - name: monitoring
        enabled: false
      - name: backup
        enabled: false
      - name: cdn
        enabled: false
```

No new model or kind is needed for tiers — they reuse the existing `EnvironmentModel` with its `overrides` block.

#### Why One File Per Customer?

| Concern             | Single registry file                            | One file per customer                                  |
| ------------------- | ----------------------------------------------- | ------------------------------------------------------ |
| **Git diffs**       | Every change touches the same file              | Changes are isolated per customer                      |
| **PR reviews**      | Reviewer must scan a long diff                  | PR adds/modifies exactly one file                      |
| **Merge conflicts** | Two teams onboarding simultaneously = conflict  | No conflicts — different files                         |
| **Git blame**       | Shows last editor of the file, not per customer | Shows exactly who changed this customer's config       |
| **CODEOWNERS**      | Cannot scope ownership per customer             | Can assign ownership per customer or per landscape dir |
| **CI validation**   | Must validate all customers on every change     | Can validate only the changed customer                 |

Strata discovers all `*.yaml` files in `customers/` and assembles the full registry at load time.

#### Validation Rules

- Customer codes are unique within a landscape (enforced by filename: `{code}.yaml`)
- Customer zones must exist as zones in the owning landscape
- Customer zones must be valid for data residency constraints (cross-referenced with configuration provider regions)
- All environment file paths in `spec.environments` must resolve to valid `kind: environment` files
- Strata assembles the full customer list by scanning `customers/*.yaml` at load time

### Generated Deployments (per customer)

`strata tenant generate` produces standard `kind: deployment` files — one per tenant × zone × lifecycle environment. No new kind needed.

```yaml
# build/tenants/acme-eu-production.yaml (GENERATED — not hand-authored)
apiVersion: strata.huybrechts.xyz/v1
kind: deployment
meta:
  name: acme_eu_production
  labels:
    tenant: acme
    zone: eu
spec:
  properties:
    tenant: acme
    zone: eu
  workspace: application
  environments:
    - environments/tiers/enterprise.yaml     # from tenant.environments
    - environments/production.yaml            # from deployment matrix
  inputs:
    from: deploy/zone-eu.yaml                # cross-workspace reference
```

The application workspace defines all possible customer resources (namespace, RBAC, storage, network policies, app modules). The merged environments control which are active and how they're sized. Standard strata build/deploy pipeline handles the rest.

---

## Variable Flow: Tenant Metadata → Terraform

Tenant files carry two kinds of data that look similar but flow differently:

| Field                                                | Purpose                                             | Reaches Terraform?        |
| ---------------------------------------------------- | --------------------------------------------------- | ------------------------- |
| `spec.configuration`                                 | Deployment metadata — routing, labeling, audit      | ❌ Not automatically       |
| `spec.environments[]` → tier file `spec.variables[]` | Runtime values resolved at deploy time              | ✅ Yes, as `TF_VAR_*`      |
| `spec.references.variables`                          | Declares which variable keys this tenant *requires* | Contract only — no values |

### `spec.configuration` is metadata, not Terraform input

`spec.configuration` holds arbitrary key/value pairs that are injected into the
generated deployment's `spec.properties` at slot generation time. These appear in
the deployment manifest and are useful for routing, labeling, and audit. They are
**not** automatically passed to Terraform.

```yaml
# customers/acme.yaml
spec:
  configuration:
    crm_id: "42"
    invoice_prefix: "ACM"
    support_tier: gold
```

After slot generation, `spec.properties` in the deployment manifest contains:

```yaml
# build/customers/acme-eu-production.yaml (generated)
spec:
  properties:
    customer: acme
    zone: eu
    crm_id: "42"          # ← from spec.configuration
    invoice_prefix: "ACM"
    support_tier: gold
```

These properties appear in `strata deploy list` output and the deployment manifest,
but Terraform does not see them unless you also expose them as variables.

### How values actually reach Terraform

```
customer.yaml  spec.environments            spec.references.variables
      │                  │                          │
      │            tier file                   (contract only —
      │          spec.variables[]              declares required keys)
      │                  │
      │         environment variable             deployment
      │           store definition             spec.variables[]
      │                  │                          │
      └──────────────────┴──────────────────────────┘
                         │
                  ValueController.resolve()
                         │
                  ResolvedValues.variables
                         │
                  inject_tf_vars()
                         │
                  TF_VAR_<key> in subprocess env
                         │
                  terraform plan / apply
                         │
                  var.<key> in .tf files
```

### Pattern A — Tier-wide constants (same value for all customers in a tier)

Put the variable in the tier environment file. All customers using that tier share
the same value.

```yaml
# environments/tiers/enterprise.yaml
spec:
  properties:
    tier: enterprise
  variables:
    - key: vm_size
      store: constant
      value: Standard_D4s_v3
    - key: replica_count
      store: constant
      value: "3"
    - key: backup_retention_days
      store: constant
      value: "30"
```

Terraform root module:

```hcl
variable "vm_size"               {}
variable "replica_count"         {}
variable "backup_retention_days" {}
```

### Pattern B — Tenant-specific constants

Put the variable in a per-tenant environment override file and reference it from
the tenant file. Only that tenant sees the value.

```yaml
# environments/tenants/acme-overrides.yaml
spec:
  variables:
    - key: crm_id
      store: constant
      value: "42"
    - key: invoice_prefix
      store: constant
      value: ACM
```

```yaml
# tenants/acme.yaml
spec:
  environments:
    - environments/tiers/enterprise.yaml
    - environments/tenants/acme-overrides.yaml   # ← tenant-specific values
```

Terraform root module:

```hcl
variable "crm_id"          {}
variable "invoice_prefix"  {}
```

### Pattern C — CI-injected values

Use `store: environment` to read from an environment variable in the CI runner.
Useful for values that differ per pipeline run (e.g. a build number or deployment token)
or values that must not appear in YAML source.

```yaml
# environments/tiers/enterprise.yaml (or deployment spec.variables)
spec:
  variables:
    - key: deploy_token
      store: environment
      value: STRATA_DEPLOY_TOKEN    # reads os.environ["STRATA_DEPLOY_TOKEN"]
```

The CI pipeline sets `STRATA_DEPLOY_TOKEN` before running `strata deploy`. Strata
reads it, adds it to `ResolvedValues.variables`, and injects it as `TF_VAR_deploy_token`.

### Feature flags → Terraform booleans

Feature flags set in a customer file or tier environment flow to Terraform as
`TF_VAR_<flag>` with the value `"true"` or `"false"`.

```yaml
# customers/acme.yaml
spec:
  features:
    new_dashboard: true
```

In Terraform:

```hcl
variable "new_dashboard" {
  type    = bool
  default = false
}

resource "kubernetes_config_map" "features" {
  data = {
    new_dashboard = var.new_dashboard
  }
}
```

### Secrets

Secrets follow the same path as variables but are marked sensitive. Define them
in environment files with a secret store backend:

```yaml
spec:
  secrets:
    - key: customer_api_key
      store: azure-keyvault
      value: customers/acme/api-key       # path in Key Vault
```

Strata injects secrets as `TF_VAR_<key>` alongside variables. Terraform treats
them the same way — `variable "customer_api_key" { sensitive = true }`.

### Declaring required keys (`spec.references`)

Use `spec.references.variables` on the customer file to declare which variable
keys a customer's deployments depend on. This is a contract — strata validates that
every key is defined in the merged environment before the build runs:

```yaml
# customers/acme.yaml
spec:
  references:
    variables: [vm_size, crm_id, deploy_token]
    secrets:   [customer_api_key]
    features:  [new_dashboard]
```

If a referenced key is missing from all merged environment layers, `strata build`
fails with: *"Tenant 'acme' requires variable 'crm_id' but it is not defined in any
merged environment layer."*

### Merge precedence (lowest → highest)

1. Workspace defaults
2. Tenant tier environment files (`spec.environments`, in order)
3. Lifecycle/deployment environment files (dev, test, acceptance, production)
4. Deployment `spec.variables` (highest — overrides everything)

Later layers override earlier ones by key. This means production secrets always
win over tier defaults, and deployment-level overrides are the final word.

---

## Cross-Workspace References

The infrastructure workspace produces outputs (AKS endpoint, resource group, ingress IP, ACR login server) that the application workspace needs as inputs. This is a cross-workspace dependency.

### Why strata should know about it

| Concern                   | Terraform-only (remote state)                                  | Strata-managed                                      |
| ------------------------- | -------------------------------------------------------------- | --------------------------------------------------- |
| **Dependency validation** | Implicit — Terraform fails at plan time if state doesn't exist | Explicit — strata validates before build            |
| **Ordering**              | Operators must know to deploy infra first                      | `strata deploy` can enforce order                   |
| **Discovery**             | Hardcoded backend config in Terraform source                   | Declared in deployment YAML, resolved at build time |
| **Audit trail**           | Not captured in deployment manifest                            | Recorded: which infra deployment provided inputs    |

### How it works

**Layer 1: Deployment declares the dependency**

The application deployment references an infrastructure deployment via `spec.inputs`:

```yaml
# build/customers/acme-eu-production.yaml
spec:
  workspace: application
  environments: [...]
  inputs:
    from: deploy/zone-eu.yaml        # infrastructure deployment
```

**Layer 2: Infrastructure deployment exposes outputs**

After `strata build` completes for the infrastructure workspace, outputs are written to the build artifact (`platform.json`):

```json
{
  "deployment": "landscape_alpha_eu",
  "outputs": {
    "aks_cluster_name": "alpha-eu-aks",
    "aks_resource_group": "rg-alpha-eu",
    "ingress_ip": "20.1.2.3",
    "acr_login_server": "alphaeuregistry.azurecr.io",
    "keyvault_name": "kv-alpha-eu"
  }
}
```

**Layer 3: Application deployment consumes them**

At build time, strata resolves the `inputs.from` reference, reads the upstream `platform.json`, and injects outputs as deployment properties. These flow into Terraform as variables — no hardcoded remote state config needed in the Terraform source.

```
infrastructure deployment (zone-eu)
    │
    │  outputs → platform.json
    ▼
application deployment (acme-eu-production)
    │
    │  inputs resolved from platform.json → injected as properties
    ▼
terraform plan/apply receives: var.aks_cluster_name, var.aks_resource_group, ...
```

### Terraform can still use remote state directly

This is not either/or. Teams can choose:

- **Strata-managed inputs** — declared in deployment YAML, resolved at build time, validated, auditable
- **Terraform remote state** — `data "terraform_remote_state" "zone" { ... }` in the Terraform source, invisible to strata
- **Data sources** — `data "azurerm_kubernetes_cluster" "zone" { ... }` lookups, no state dependency at all

Strata-managed inputs are preferred because they make the dependency explicit and enable `strata validate` to catch broken references before `terraform plan` runs.

### Validation rules

- `inputs.from` must reference a deployment file that exists
- The referenced deployment must have been built (its `platform.json` exists in the build output)
- If not built, `strata build` errors: *"Deployment 'acme_eu_production' depends on 'landscape_alpha_eu' which has not been built. Run `strata build --deployment zone-eu` first."*

---

## Hybrid Ownership Model

Not everything in a customer deployment is platform-owned. Resources follow this ownership rule:

> **If a resource dies with the app, it lives with the app. If it's shared or platform-scoped, it lives in the platform.**

| Resource                    | Owner         | Where                             | Managed by |
| --------------------------- | ------------- | --------------------------------- | ---------- |
| Kubernetes namespace + RBAC | Platform team | Application workspace (Terraform) | Strata     |
| Network policies            | Platform team | Application workspace (Terraform) | Strata     |
| Key Vault secret scope      | Platform team | Application workspace (Terraform) | Strata     |
| Base Helm chart templates   | Platform team | Shared chart library              | ArgoCD     |
| App-specific Helm values    | App team      | App repo (`helm/values*.yaml`)    | ArgoCD     |
| App database, queue, cache  | App team      | App repo (`terraform/`)           | App CI/CD  |
| DNS records                 | Platform team | Application workspace or global   | Strata     |

The application workspace provisions the **isolation boundary** (namespace, RBAC, networking, secrets scope). App-specific resources (database, queue, cache) are owned by the app team and deployed via the app's own CI/CD pipeline.

---

## Zones and Data Residency

### Zones in Configuration

### Zones in Configuration

A **zone** is a logical grouping of provider regions. Zones are defined in the configuration model and serve as the bridge between customer constraints and physical infrastructure.

```yaml
# In configuration.yaml
spec:
  zones:
    - name: eu
      description: "European Union — GDPR-compliant regions"
      regions: [westeurope, northeurope]            # Azure: Dublin, Amsterdam
      jurisdiction: eu

    - name: us
      description: "United States"
      regions: [eastus, eastus2, eastus3, westus]   # Azure: Virginia, Virginia2, Georgia, California
      jurisdiction: us

    - name: apac
      description: "Asia-Pacific"
      regions: [southeastasia, australiaeast]
      jurisdiction: apac

  jurisdictions:
    - name: eu
      description: "European Union — GDPR"
      data_residency: strict        # data must not leave jurisdiction
    - name: us
      description: "United States"
      data_residency: standard
    - name: apac
      description: "Asia-Pacific"
      data_residency: standard
```

### How zones link to providers

Providers declare which regions they operate in. Zones group those regions logically:

```
configuration.zones[eu].regions = [westeurope, northeurope]
                                        │            │
                                        ▼            ▼
providers/azure-eu-west.yaml:  regions: [westeurope]     ← Dublin
providers/azure-eu-north.yaml: regions: [northeurope]    ← Amsterdam
```

A customer says `zones: [eu]`. At validation time, strata resolves:
1. `customer.zones` → `configuration.zones[eu].regions` → `[westeurope, northeurope]`
2. Checks that the deployment's provider operates in one of those regions
3. Checks that the zone's jurisdiction allows the customer's data classification

### Customers reference zones

```yaml
# tenants/acme.yaml
spec:
  code: acme
  zones: [eu]           # allowed in EU zone only — Dublin or Amsterdam
  environments:
    - environments/tiers/enterprise.yaml
```

```yaml
# tenants/globalcorp.yaml
spec:
  code: globalcorp
  zones: [eu, us]       # multi-zone — allowed in both EU and US
  environments:
    - environments/tiers/enterprise.yaml
```

### Zone deployments

Each zone gets one infrastructure deployment. The zone determines which provider and regions are used:

```yaml
# deploy/zone-eu.yaml
kind: deployment
meta:
  name: landscape_alpha_eu
spec:
  properties:
    landscape: alpha
    zone: eu
  workspace: infrastructure
  environments: [zones/eu.yaml, production.yaml]
```

The zone environment file (`environments/zones/eu.yaml`) sets the provider, region-specific sizing, and any zone-specific overrides.

### Validation rules

- Customer `zones` must reference zones defined in `configuration.zones`
- Each zone must have at least one provider whose `regions` intersect with the zone's `regions`
- If jurisdiction has `data_residency: strict`, customer data cannot be replicated outside the zone's regions
- **Hard error** if validation fails — build stops, not a warning

### Audit Trail

The deployment manifest records:

- Tenant code and name
- Zone deployed to (logical) and region deployed to (physical)
- Jurisdiction and data residency classification
- Tier and feature flags active at deploy time

---

## CLI Commands

### `strata tenant` Command Group

```
strata tenant list                              # list all tenants from registry
strata tenant show --code acme                  # show details for one tenant
strata tenant status                            # deployment status across all tenants
strata tenant status --landscape alpha           # filter by landscape
strata tenant status --env production            # filter by environment
```

### Onboarding

```
strata tenant onboard --code newcorp --name "New Corp" \
  --zones eu --env environments/tiers/standard.yaml
```

Steps:
1. Validate zones against configuration provider regions and data residency constraints
2. Create tenant YAML file (from template)
3. Generate slot descriptors for all deployment environments
4. Create secret placeholders (team fills in values)
5. Optionally run `strata build` + `strata deploy` for the dev environment

### Lifecycle

```
strata tenant upgrade --chart-version "3.3.0"              # upgrade all tenants
strata tenant upgrade --code acme --chart-version "3.3.0"  # upgrade one
strata tenant upgrade --env dev --chart-version "3.3.0"    # upgrade all dev envs

strata tenant offboard --code oldcorp --confirm            # remove tenant
strata tenant migrate --code contoso --from eu --to us      # zone migration
```

### Slot Generation

```
strata tenant generate                          # generate all slot descriptors
strata tenant generate --code acme              # generate for one tenant
strata tenant generate --env production         # generate all production slots
```

Generated slot files go into `build/tenants/` — they are outputs, not hand-edited source files.

### Status Dashboard

```
strata tenant status --landscape alpha

LANDSCAPE: alpha
ZONE: eu (AKS: healthy, nodes: 12/12)

CODE       TIER         ZONE    DEV    TEST   ACC    PROD   APP       DRIFT
acme       enterprise   eu      ✅     ✅     ✅     ✅     3.2.1     none
contoso    standard     eu      ✅     ✅     ✅     ✅     3.2.1     none
widgetco   standard     eu      ✅     ✅     ⚠️     ✅     3.2.0     acc: 1 ver behind
newcorp    standard     eu      ✅     🔄     —      —      3.2.1     onboarding
```

### Bulk Operations

```
strata tenant build --all                        # build all 400 slots
strata tenant build --code acme                  # build 4 slots for acme
strata tenant build --env production             # build 100 production slots
strata tenant deploy --code acme --env prod      # deploy one specific slot
```

---

## Repository Strategy

At scale, configuration data and tooling have different change frequencies and reviewers. The recommended split:

| Repository           | Purpose                                                          | Changes by    | Frequency            |
| -------------------- | ---------------------------------------------------------------- | ------------- | -------------------- |
| `platform-workspace` | Strata tooling, schemas, scripts, pipelines                      | Platform team | Infrequent           |
| `{team}-config`      | Team's config: tenants, environments, providers, deployments     | Team          | Frequent             |
| `platform-global`    | Global bootstrap (Terraform + Ansible)                           | Platform team | Rare                 |
| `{team}-zone`        | Zone bootstrap (AKS, networking, ArgoCD) per team                | Team          | On infra changes     |
| `{team}-tenant`      | Tenant bootstrap templates (namespace, RBAC) per team            | Team          | Infrequent           |
| `deploy-charts`      | Shared base Helm charts                                          | Platform team | On platform releases |
| `deploy-argocd`      | ArgoCD Application manifests and ApplicationSets                 | Platform team | On app deployments   |
| `app-*`              | Application source + app-owned Helm values + app-owned Terraform | App teams     | Frequent             |

**Team isolation principle:** Each team has its own `{team}-config` repo (or directory) containing its tenants, environments, and zone configs. Teams share only global resources (identity, DNS, base charts) and interfaces (provider definitions, environment templates). A team can evolve its landscape — add services, deprecate old ones — without affecting other teams.

**Why separate config from workspace?**

- Config is data, not code — lightweight review process
- App teams can propose tenant config changes without touching tooling
- CI/CD clarity: config repo triggers `strata validate` → `strata deploy`; workspace repo triggers tool tests

## Directory Structure

### Config Repository (`{team}-config`)

Each team has its own config repo. Teams don't see or touch each other's tenants.

```
alpha-config/
├── .strata/                         # links to platform-workspace
├── tenants/
│   ├── acme.yaml                    # one file per tenant
│   ├── contoso.yaml
│   ├── globex.yaml
│   └── ...                          # ~100 files, one per tenant
├── environments/
│   ├── tiers/
│   │   ├── starter.yaml             # kind: environment — minimal modules
│   │   ├── standard.yaml            # kind: environment — base modules
│   │   └── enterprise.yaml          # kind: environment — all modules, HA
│   ├── tenants/
│   │   └── acme-overrides.yaml      # optional per-tenant env overrides
│   ├── dev.yaml                     # lifecycle environments
│   ├── test.yaml
│   ├── acceptance.yaml
│   └── production.yaml
├── workspaces/
│   ├── infrastructure.yaml          # shared infrastructure workspace
│   └── application.yaml             # per-tenant application workspace
├── providers/
│   ├── azure-eu-west.yaml           # westeurope (Dublin)
│   ├── azure-eu-north.yaml          # northeurope (Amsterdam)
│   └── azure-us-east.yaml           # eastus (Virginia)
├── stack/
│   ├── zone-eu.yaml                 # zone bootstrap config
│   └── zone-us.yaml
└── deploy/
    ├── zone-eu.yaml                 # deploy zone
    └── zone-us.yaml
```

### Build Output

```
build/
├── landscape-alpha-eu/            # landscape build output
│   ├── platform.json
│   └── sbom.json
├── tenants/                         # generated tenant slots
│   ├── acme-dev/
│   ├── acme-test/
│   ├── acme-acceptance/
│   ├── acme-production/
│   ├── contoso-dev/
│   │   ...
│   └── globex-production/
└── argocd/                          # generated ArgoCD manifests
    ├── tenant-appsets.yaml         # ApplicationSets per tenant
    └── overlays/
        ├── acme-dev/
        │   └── values.yaml
        └── acme-production/
            └── values.yaml
```

---

## Scale Characteristics

| Dimension                      | Count          | Management                                   |
| ------------------------------ | -------------- | -------------------------------------------- |
| Landscapes                     | 2–5            | One config repo per team                     |
| Zones per landscape            | 1–3            | Manual, evolves with the landscape           |
| Landscape deployments          | ~6–15          | Standard strata workflow                     |
| Tenant files                   | ~100           | One YAML per tenant per landscape            |
| Tenant deployments (generated) | ~400           | Generated from tenant files × lifecycle envs |
| Onboarding rate                | ~1/week        | Add one YAML file via PR                     |
| App upgrade frequency          | Weekly–monthly | Update tier env file or per-tenant env       |
| Infrastructure changes         | Ongoing        | Non-breaking evolution of the landscape      |

---

## Implementation Phases

### Phase 1: Tenant Model + Directory-Based Discovery

- New `tenant` kind — model, service, validation
- Directory-based discovery: scan `tenants/*.yaml`, assemble registry at load time
- Tenant `environments` list resolved and validated (files must exist, must be `kind: environment`)
- `strata tenant list` / `strata tenant show`
- Zone validation against configuration provider regions
- No generation, no slots — just the tenant files as source of truth

### Phase 2: Deployment Generation

- `strata tenant generate` — generates standard `kind: deployment` files per tenant × zone × lifecycle env
- Merge order: tenant environments first, deployment environments on top
- Generated files reference the application workspace + merged environments
- Generated files are build outputs, stored in `build/tenants/`
- Standard `strata build` / `strata deploy` pipeline handles them — no new builder

### Phase 3: Cross-Workspace Inputs

- `spec.inputs.from` field on deployment model — references upstream deployment
- Build resolves upstream `platform.json` and injects outputs as properties
- Validation: upstream must be built before downstream can build
- `strata tenant build` resolves infrastructure outputs automatically
- `strata tenant deploy` deploys into existing infrastructure

### Phase 4: Lifecycle Commands

- `strata customer onboard` — add to registry + generate + build + deploy dev
- `strata customer offboard` — remove resources + archive registry entry
- `strata customer upgrade` — bulk chart version update across slots
- `strata customer status` — dashboard across all customers and environments

### Phase 5: Data Residency Enforcement

- Configuration-level jurisdiction definitions
- Hard build-time validation: customer zones vs. allowed regions
- Audit trail in deployment manifests
- SBOM annotations for data classification

---

## Application Deployment via ArgoCD

Strata does **not** deploy customer applications directly. Instead, it generates ArgoCD ApplicationSet configurations that ArgoCD syncs from Git.

### How it works

1. **Strata generates** customer bootstrap infrastructure (namespace, RBAC, secrets) via Terraform
2. **Strata generates** ArgoCD ApplicationSet entries from the customer registry
3. **ArgoCD syncs** application deployments from Git (base chart + app-specific values)
4. **App teams** own their Helm values in their app repos; platform team owns base charts

### ApplicationSet Generation

From the customer registry, strata can generate an ArgoCD ApplicationSet that creates per-customer Application resources:

```yaml
# Generated by: strata customer generate --argocd
apiVersion: argoproj.io/v1alpha1
kind: ApplicationSet
metadata:
  name: customer-webapp
spec:
  generators:
    - list:
        elements:
          - code: acme
            namespace: acme-prod
            tier: enterprise
            zone: eu
          - code: contoso
            namespace: contoso-prod
            tier: standard
            zone: eu
  template:
    spec:
      source:
        repoURL: https://git.company.com/deploy-charts
        chart: company-webapp
        targetRevision: "3.2.1"
        helm:
          valueFiles:
            - values-{{tier}}.yaml
            - customers/{{code}}/values.yaml
      destination:
        server: https://aks-{{zone}}.company.internal
        namespace: "{{namespace}}"
```

This keeps strata in the infrastructure lane and ArgoCD in the application lane.

---

## Open Questions

1. **Secret management at scale** — One Azure Key Vault per zone with path-based isolation per customer (`customers/{code}/*`) is the likely pattern. Strata provisions the secret scope during customer bootstrap; app teams and CI/CD write actual secret values.

2. **Rollout ordering** — When upgrading the app for all customers, what's the rollout strategy? All dev first → all test → all acc → all prod? Or customer-by-customer through all environments? ArgoCD progressive rollout features (e.g. Argo Rollouts) may handle this natively.

3. **Drift detection** — How does `strata customer status` know a deployment is "behind"? For infrastructure: compare Terraform state. For apps: query ArgoCD sync status via API or CLI.

4. **Multi-zone customers** — Contoso has `zones: [eu, us]`. Does that mean primary + DR replica? Active-active? How is the relationship between zones modeled for a single customer?

5. **Landscape team boundaries** — Can a customer span multiple landscapes (different teams)? Or is a customer always owned by exactly one landscape/team?

6. **App-owned infrastructure** — App-specific resources (database, queue, cache) are deployed by the app team's CI/CD, not by strata. How does strata validate that required app-owned resources exist before deploying the customer deployment? Or is this left to the app pipeline?

7. **Inputs shape** — Should `spec.inputs.from` be a single reference or a list? A customer deployment might need outputs from multiple infrastructure deployments (e.g., zone infra + global DNS). List is more flexible but adds complexity to resolution order.
