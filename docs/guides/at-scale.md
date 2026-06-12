# Operating Strata at Scale — Multi-Customer Design

> **Status:** Design draft — not yet implemented.  
> **Context:** A company using strata to manage 100+ customers across multiple regions with shared infrastructure and dedicated application resources.

---

## Scenario

A managed services company operates a platform for 100+ customers. Each customer has:

- A **code** (e.g. `acme`, `contoso`) — unique short identifier
- A **name** (e.g. "Acme Corporation")
- **Allowed regions/zones** — constrained by GDPR, data residency, or contractual requirements
- **Environments** — typically `dev`, `test`, `acceptance`, `production`
- A **tier** — determines sizing, HA, and feature availability

Key constraints:

- **In-house deployments only** — internal ops team, no customer self-service
- **Shared infrastructure** — one AKS cluster (or similar) per zone/region, shared networking and ingress
- **Dedicated application resources** — per-customer namespace, web app, database, DNS, secrets
- **Stable landscapes** — infrastructure changes rarely; customer onboarding is ~1/week
- **Multiple landscapes** — different teams own different landscapes with their own workspaces

---

## Two-Layer Architecture

### Layer 1: Landscape Infrastructure (shared, stable)

A **landscape** is a team-owned infrastructure domain. Each landscape has one or more **zones** (region-specific deployments). Zones contain shared resources that multiple customers use.

```
Company
├── Landscape Alpha (Team Alpha)
│   ├── eu-fr (zone)
│   │   └── AKS, VNet, ingress, monitoring, cert-manager
│   └── eu-nl (zone)
│       └── AKS, VNet, ingress, monitoring, cert-manager
├── Landscape Beta (Team Beta)
│   └── us-east (zone)
│       └── AKS, VNet, ingress, monitoring
```

Managed with today's strata deployment model — one deployment YAML per zone:

```yaml
kind: deployment
meta:
  name: landscape_alpha_eu_fr
spec:
  properties:
    landscape: alpha
    zone: eu-fr
  workspace: platform_infra
  environments: [production.yaml]
  stages:
    - name: infrastructure        # terraform: AKS, networking
    - name: configure             # ansible: cluster setup, cert-manager
    - name: platform_services     # helm: traefik, prometheus, loki
```

**Count:** ~2–6 deployments per landscape. Changes rarely. Standard strata workflow.

### Layer 2: Customer Slots (dedicated, per-customer)

A **customer slot** is a lightweight deployment of dedicated resources **into** an existing landscape zone. It doesn't provision its own AKS cluster or networking — it targets the shared infrastructure.

Each slot deploys:

- A Kubernetes namespace (isolation boundary)
- A Helm release (the customer's web app instance)
- Customer-specific configuration and secrets
- DNS record(s)
- Optionally: a dedicated database, storage account, or other per-customer resources

**Count:** ~100 customers × 4 environments = ~400 slots. These are generated from a registry, not hand-authored.

---

## New Concepts

### Customer Registry (`kind: customer-registry`)

Single source of truth for all customers. One file per landscape.

```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: customer-registry
meta:
  name: alpha_customers
  annotations:
    description: Customers managed by Landscape Alpha
spec:
  defaults:
    environments: [dev, test, acceptance, production]
    tier: standard
    app_chart: company-webapp
    app_chart_version: "3.2.1"

  tiers:
    standard:
      description: Standard customer tier
      vm_count: 1
      ha_enabled: false
      modules: [webapp, customer_db]
    enterprise:
      description: Enterprise customer tier — HA, monitoring, CDN
      vm_count: 3
      ha_enabled: true
      modules: [webapp, customer_db, monitoring, backup, cdn]
    starter:
      description: Lightweight onboarding tier
      vm_count: 1
      ha_enabled: false
      modules: [webapp]

  customers:
    - code: acme
      name: "Acme Corporation"
      zones: [eu-fr]
      tier: enterprise
      features:
        new_dashboard: true
      onboarded: 2025-03-15

    - code: contoso
      name: "Contoso Ltd"
      zones: [eu-fr, eu-nl]         # primary + DR
      tier: standard
      onboarded: 2026-01-10

    - code: globex
      name: "Globex Inc"
      zones: [eu-fr]
      tier: standard
      environments: [dev, production]  # override: only 2 envs
      onboarded: 2026-06-01
```

#### Validation Rules

- Customer codes are unique within a registry
- Customer zones must exist as zones in the owning landscape
- Customer zones must be valid for data residency constraints (cross-referenced with configuration provider regions)
- Tier must reference a defined tier in `spec.tiers`
- Environment names must match known environment templates

### Customer Slot (`kind: customer-slot`)

A lightweight deployment descriptor — generated from the registry, not hand-authored.

```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: customer-slot
meta:
  name: acme_production
  labels:
    customer: acme
    environment: production
    landscape: alpha
    zone: eu-fr
    tier: enterprise
spec:
  customer: acme
  landscape: landscape_alpha_eu_fr    # target landscape deployment
  environment: production
  tier: enterprise

  namespace: acme-prod                # kubernetes namespace

  modules:
    - name: webapp
      chart: company-webapp
      chart_version: "3.2.1"
      values:
        customerCode: acme
        tier: enterprise
        features:
          new_dashboard: true

    - name: customer_db
      type: terraform
      template: dedicated-postgres
      variables:
        db_name: acme_prod
        db_sku: GP_Gen5_4             # enterprise tier sizing

  dns:
    - name: acme.platform.company.com
      type: CNAME
      target: eu-fr.platform.company.com

  secrets:
    - ACME_DB_PASSWORD
    - ACME_API_KEY
    - ACME_SMTP_KEY
```

#### Relationship to Landscape

The slot references a landscape deployment by name. At build/deploy time, strata resolves the landscape's outputs (AKS endpoint, resource group, ingress IP, etc.) and injects them as inputs to the slot's modules. This is a **cross-deployment reference** — the slot depends on the landscape existing and being deployed.

```
landscape_alpha_eu_fr.outputs:
  aks_cluster_name: "alpha-eu-fr-aks"
  aks_resource_group: "rg-alpha-eu-fr"
  ingress_ip: "20.1.2.3"
  acr_login_server: "alphaeufrregistry.azurecr.io"
      │
      ▼
customer-slot modules receive these as variables
```

---

## Data Residency and Region Enforcement

### Configuration-Level Constraints

```yaml
# In configuration.yaml
spec:
  data_residency:
    jurisdictions:
      - name: eu
        description: "European Union — GDPR"
        allowed_regions: [eu-fr, eu-nl, eu-de]
      - name: us
        description: "United States"
        allowed_regions: [us-east, us-west]
      - name: global
        description: "No restrictions"
        allowed_regions: []          # empty = all regions allowed
```

### Customer-Level Enforcement

Each customer's `zones` field in the registry constrains where their data can be deployed. At build time:

1. Resolve customer zones → provider regions
2. Validate each region is in the customer's allowed set
3. **Hard error** if a zone maps to a prohibited region — build fails, not a warning
4. SBOM and deployment manifest record the data residency jurisdiction for audit

### Audit Trail

The deployment manifest (existing `kind: deployment-manifest`) already captures what was deployed where. With customer slots, it additionally records:

- Customer code and name
- Jurisdiction and allowed zones
- Actual zone deployed to
- Tier and feature flags active at deploy time

---

## CLI Commands

### `strata customer` Command Group

```
strata customer list                              # list all customers from registry
strata customer show --code acme                  # show details for one customer
strata customer status                            # deployment status across all customers
strata customer status --landscape alpha           # filter by landscape
strata customer status --env production            # filter by environment
```

### Onboarding

```
strata customer onboard --code newcorp --name "New Corp" \
  --zones eu-fr --tier standard
```

Steps:
1. Validate zones against landscape and data residency constraints
2. Add entry to customer registry
3. Generate slot descriptors for all environments
4. Create secret placeholders (team fills in values)
5. Optionally run `strata build` + `strata deploy` for the dev environment

### Lifecycle

```
strata customer upgrade --chart-version "3.3.0"              # upgrade all customers
strata customer upgrade --code acme --chart-version "3.3.0"  # upgrade one
strata customer upgrade --env dev --chart-version "3.3.0"    # upgrade all dev envs

strata customer offboard --code oldcorp --confirm            # remove customer
strata customer migrate --code contoso --from eu-fr --to eu-nl  # region migration
```

### Slot Generation

```
strata customer generate                          # generate all slot descriptors
strata customer generate --code acme              # generate for one customer
strata customer generate --env production         # generate all production slots
```

Generated slot files go into `build/customers/` — they are outputs, not hand-edited source files.

### Status Dashboard

```
strata customer status --landscape alpha

LANDSCAPE: alpha
ZONE: eu-fr (AKS: healthy, nodes: 12/12)

CODE       TIER         ZONE    DEV    TEST   ACC    PROD   APP       DRIFT
acme       enterprise   eu-fr   ✅     ✅     ✅     ✅     3.2.1     none
contoso    standard     eu-fr   ✅     ✅     ✅     ✅     3.2.1     none
widgetco   standard     eu-fr   ✅     ✅     ⚠️     ✅     3.2.0     acc: 1 ver behind
newcorp    standard     eu-fr   ✅     🔄     —      —      3.2.1     onboarding
```

### Bulk Operations

```
strata customer build --all                        # build all 400 slots
strata customer build --code acme                  # build 4 slots for acme
strata customer build --env production             # build 100 production slots
strata customer deploy --code acme --env prod      # deploy one specific slot
```

---

## Directory Structure

```
config/
├── customers/
│   ├── alpha-registry.yaml          # customer registry for landscape alpha
│   └── beta-registry.yaml           # customer registry for landscape beta
├── environments/
│   ├── dev.yaml                     # shared environment templates
│   ├── test.yaml
│   ├── acceptance.yaml
│   └── production.yaml
├── workspaces/
│   └── platform_infra.yaml          # shared infrastructure workspace
├── slot-templates/                  # templates for customer slot generation
│   ├── standard.yaml
│   ├── enterprise.yaml
│   └── starter.yaml
└── providers/
    ├── eu-fr.yaml
    ├── eu-nl.yaml
    └── us-east.yaml

build/
├── landscape-alpha-eu-fr/           # landscape build output
│   ├── platform.json
│   └── sbom.json
├── customers/                       # generated customer slots
│   ├── acme-dev/
│   ├── acme-test/
│   ├── acme-acceptance/
│   ├── acme-production/
│   ├── contoso-dev/
│   │   ...
│   └── globex-production/
```

---

## Scale Characteristics

| Dimension                  | Count          | Management                           |
| -------------------------- | -------------- | ------------------------------------ |
| Landscapes                 | 2–5            | Manual, per team                     |
| Zones per landscape        | 1–3            | Manual, stable                       |
| Landscape deployments      | ~6–15          | Standard strata workflow             |
| Customer registry entries  | ~100           | Single YAML file per landscape       |
| Customer slots (generated) | ~400           | Generated from registry + templates  |
| Onboarding rate            | ~1/week        | Guided CLI flow                      |
| App upgrade frequency      | Weekly–monthly | Bulk CLI command                     |
| Infrastructure changes     | Rare (monthly) | Standard deployment, careful rollout |

---

## Implementation Phases

### Phase 1: Customer Registry Model + Validation

- New `customer-registry` kind — model, service, validation
- `strata customer list` / `strata customer show`
- Zone validation against configuration provider regions
- No generation, no slots — just the registry as the source of truth

### Phase 2: Slot Generation

- New `customer-slot` kind — model
- Slot template system — parameterized YAML templates per tier
- `strata customer generate` — produces slot descriptors from registry × environments × templates
- Generated files are build outputs, stored in `build/customers/`

### Phase 3: Cross-Deployment References

- Landscape outputs → slot inputs wiring
- `strata customer build` — builds slots using landscape outputs as context
- `strata customer deploy` — deploys slots into existing landscape infrastructure

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

## Open Questions

1. **Slot vs. deployment** — Should `customer-slot` be a new kind, or a specialized subtype of `deployment` with a `mode: slot` flag? A new kind is cleaner but means new builders/services. A deployment subtype reuses existing infrastructure.

2. **Cross-deployment references** — How do landscape outputs flow into slots? Options:
   - File-based: landscape writes outputs to a known path, slot reads them
   - Registry-based: a landscape service exposes outputs that slots query
   - Explicit wiring: slot YAML declares `inputs.aks_endpoint: landscape_alpha_eu_fr.outputs.aks_cluster_name`

3. **Secret management at scale** — 100 customers × ~5 secrets × 4 environments = ~2000 secrets. Where do they live? Azure Key Vault per landscape? Per customer? A central vault with path-based isolation?

4. **Rollout ordering** — When upgrading the app for all customers, what's the rollout strategy? All dev first → all test → all acc → all prod? Or customer-by-customer through all environments? Configurable?

5. **Drift detection** — How does `strata customer status` know a slot is "behind"? Compare generated slot chart version against what's actually deployed? Requires querying live cluster state.

6. **Multi-zone customers** — Contoso has `zones: [eu-fr, eu-nl]`. Does that mean primary + DR replica? Active-active? How is the relationship between zones modeled for a single customer?

7. **Landscape team boundaries** — Can a customer span multiple landscapes (different teams)? Or is a customer always owned by exactly one landscape/team?
