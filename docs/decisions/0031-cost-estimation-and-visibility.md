# Cost estimation and visibility

- Status: partial
- Date: 2026-07-11

## Context and Problem Statement

Strata validates and deploys infrastructure but provides no visibility into cost implications. Teams rely on external tools (Azure Cost Explorer, Infracost, spreadsheets) to estimate costs — or worse, they don't estimate at all and get surprises.

**The core insight:** Users don't think in cloud pricing terms. They don't know (or care) that an Azure SQL Database costs "$0.2529/vCore-hour plus $0.115/GB-month for storage plus $0.1448/vCore-hour for licensing." They think: *"My database is about 50GB and moderately busy."*

**What we need:**
- Cost estimation that requires **zero cloud pricing knowledge** from the user
- Users express resource needs in **human terms** (small/medium/large, quiet/busy)
- The system translates human language → cloud SKUs → money
- Different environments can have different "sizing profiles" (dev = cheap, prod = reliable)
- Community can share and improve dimension definitions over time

## Considered Options

- **Option A**: User manually enters `unit_cost` per resource (current field)
- **Option B**: Infracost integration — parse `terraform plan -json`, auto-lookup prices
- **Option C**: Scenario-based model — dimensions + scenarios + pricing engine

### Option B Detail — Infracost

[Infracost](https://www.infracost.io) is the de-facto standard for pre-deploy cost
estimation in the IaC ecosystem. Used by Env0, Spacelift, and Scalr.

**How it works:**
- Parses `terraform plan -json` output
- Maps each resource type to cloud pricing using a bundled price database scraped from:
  - AWS: Bulk Pricing API
  - Azure: Retail Prices API (`prices.azure.com`)
  - GCP: Cloud Billing Catalog API
- Returns monthly cost estimate per resource + before/after diff

**Pricing:** Apache 2.0. OSS binary ships with bundled pricing database — no API key
required for basic estimation. Free tier: 1,000 runs/month on hosted API.

**Why Option B was not chosen:** Infracost requires users to think in Terraform resource
terms. It also requires the Terraform plan to already exist. Strata's scenario-based model
(Option C) lets users estimate costs before writing any Terraform, using human terms.
Infracost could still be used as the **pricing engine backend** for Option C's price
lookup layer — the two are not mutually exclusive.

## Decision Outcome

Chosen: **Option C — Scenario-based cost model** with community dimensions.

The system has three layers:

1. **Dimensions** — Define the **metrics** (cost factors) per resource type, with possible **measures** (shipped with strata + community + custom)
2. **Scenarios** — Assign **measures** to those metrics per workspace (user-defined, named, reusable)
3. **Pricing Engine** — Translates measures → cloud SKUs → API lookup → money (automatic)

### Consequences

- **Good**: Users express needs in human terms, not cloud pricing terms
- **Good**: Zero cloud pricing knowledge required from users
- **Good**: Scenarios are reusable — "startup" works for all resources at once
- **Good**: Compare costs across scenarios instantly (dev vs staging vs prod)
- **Good**: Community can share dimension packs for common resource types
- **Good**: Users choose their detail level (use defaults OR override per-resource)
- **Good**: AI agent can interview users and generate scenarios
- **Bad**: Requires a mapping layer (dimensions) to be maintained
- **Bad**: Accuracy depends on how well dimensions map to real SKUs
- **Bad**: Community dimensions need governance (versioning, quality)

---

## Terminology

| Term               | What it is                                                                     | Example                                                             | Who defines it                             |
| ------------------ | ------------------------------------------------------------------------------ | ------------------------------------------------------------------- | ------------------------------------------ |
| **Metric**         | A named cost factor — the *thing* that drives cost for a resource              | `data_volume`, `traffic`, `availability`                            | Dimension file (strata / community / user) |
| **Measure**        | The user's estimate for a metric — their *guess* at the value                  | `small`, `busy`, `high`                                             | Scenario file (user)                       |
| **Dimension file** | Declares which metrics exist for a resource type, with their possible measures | "An Azure SQL DB has 3 metrics: data_volume, traffic, availability" | Shipped / community / custom               |
| **Scenario**       | A named bundle of measures — one measure per metric, reusable                  | "startup" = all metrics set to small/quiet/dev                      | User per workspace                         |

**How it flows:**

```
Dimension file defines METRICS for a resource type
  → "A SQL Database has these metrics: data_volume, traffic, availability"
  → Each metric has possible MEASURES: small / medium / large / massive

Scenario assigns MEASURES to those metrics
  → "startup": data_volume=small, traffic=quiet, availability=dev
  → "enterprise": data_volume=large, traffic=busy, availability=high

Pricing engine reads the measures → resolves cloud parameters → calculates cost
```

**The user's mental model:**

```
"I have a database"                    ← resource (already defined)
"It holds about 50GB of data"          ← measure for the 'data_volume' metric
"It's moderately busy"                 ← measure for the 'traffic' metric
"It needs to be highly available"      ← measure for the 'availability' metric
```

The user doesn't think "I'm setting metrics and measures." They think "I'm describing my database." The terminology exists so the system has clear names for the parts.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│  DIMENSION FILES (per resource type — define available METRICS)      │
│  Sources: strata built-in │ community packs │ user custom           │
│                                                                     │
│  "A SQL Database has these metrics:"                                │
│    • data_volume:  tiny / small / medium / large / massive          │
│    • traffic:      quiet / moderate / busy / extreme                │
│    • availability: dev / standard / high / critical                 │
│                                                                     │
├─────────────────────────────────────────────────────────────────────┤
│  SCENARIOS (per workspace — assign MEASURES to metrics)             │
│  User-defined, named, reusable                                      │
│                                                                     │
│  "startup":     data_volume=small, traffic=quiet, availability=dev  │
│  "enterprise":  data_volume=large, traffic=busy, availability=high  │
│                                                                     │
├─────────────────────────────────────────────────────────────────────┤
│  ENVIRONMENT → SCENARIO binding                                     │
│                                                                     │
│  dev → startup │ staging → growth │ prod → enterprise               │
│                                                                     │
├─────────────────────────────────────────────────────────────────────┤
│  PRICING ENGINE (automatic — translates measures → money)           │
│                                                                     │
│  measure → cloud parameters → Azure/AWS API → €/month              │
│                                                                     │
├─────────────────────────────────────────────────────────────────────┤
│  AI AGENT (optional — helps create scenarios interactively)         │
│                                                                     │
│  "Describe your workload" → suggested measures → cost comparison    │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Detailed Design

### 1. Dimensions — "What Metrics Exist for This Resource Type"

A dimension file declares the **metrics** (cost factors) relevant to a resource type, and for each metric defines the **measures** (possible values) the user can choose from. Each measure maps to concrete cloud parameters (SKU, size, tier) that the pricing engine uses.

**Users choose their detail level.** A dimension file can have 2 metrics or 10 — the user assigns measures only to the metrics they care about, and everything else gets sensible defaults.

#### Dimension File Structure

```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: cost-dimensions
meta:
  name: azurerm_mssql_database
  labels:
    version: "1.2.0"
    provider: azure
    service_family: databases
  annotations:
    description: "Cost metrics for Azure SQL Database"
    maintainer: "strata-community"

spec:
  resource_type: azurerm_mssql_database
  display_name: "Azure SQL Database"
  
  # Metrics — each is a named cost factor with possible measures
  metrics:
    - key: data_volume
      question: "How much data will this database hold?"
      default: small          # measure used when user doesn't specify
      measures:
        tiny:
          label: "Minimal (< 5 GB)"
          description: "Dev databases, prototypes"
          params: { storage_gb: 5 }
        small:
          label: "Small (5–50 GB)"
          description: "Small applications, internal tools"
          params: { storage_gb: 32 }
        medium:
          label: "Medium (50–200 GB)"
          description: "Standard SaaS workloads"
          params: { storage_gb: 100 }
        large:
          label: "Large (200 GB – 1 TB)"
          description: "Data-heavy applications"
          params: { storage_gb: 500 }
        massive:
          label: "Massive (1+ TB)"
          description: "Data warehousing, analytics"
          params: { storage_gb: 2000 }

    - key: traffic
      question: "How busy is the database workload?"
      default: moderate
      measures:
        quiet:
          label: "Low — occasional queries"
          description: "Internal tools, dev environments"
          params: { sku: "GP_S_Gen5_2", tier: "serverless" }
        moderate:
          label: "Moderate — regular application traffic"
          description: "Standard web applications"
          params: { sku: "GP_Gen5_4" }
        busy:
          label: "High — heavy transactional workloads"
          description: "E-commerce, high-traffic SaaS"
          params: { sku: "GP_Gen5_8" }
        extreme:
          label: "Extreme — mission-critical performance"
          description: "Financial systems, real-time analytics"
          params: { sku: "BC_Gen5_8" }

    - key: availability
      question: "How critical is uptime for this database?"
      default: standard
      measures:
        dev:
          label: "Dev/test — downtime acceptable"
          params: { zone_redundant: false, geo_replication: false }
        standard:
          label: "Production — standard SLA"
          params: { zone_redundant: false, geo_replication: false }
        high:
          label: "High availability — zone redundant"
          params: { zone_redundant: true, geo_replication: false }
        critical:
          label: "Mission-critical — geo-replicated"
          params: { zone_redundant: true, geo_replication: true }

  # Pricing rules — how to translate params → API queries
  pricing:
    provider: azure
    service_name: "SQL Database"
    sku_param: "sku"
    region_source: provider   # read from strata provider config
    
    # Cost multipliers for options that affect base price
    multipliers:
      zone_redundant: 1.25    # +25% on compute
      geo_replication: 2.0    # doubles the bill (full replica)
    
    # Additional meters beyond compute
    additional_meters:
      - name: storage
        unit: "GB/month"
        quantity_param: "storage_gb"
      - name: license
        type: "included"      # or "base_price" for BYOL
```

#### Custom Dimensions (User-Defined)

Users can add their own metrics for resource types strata doesn't cover, or override/extend existing ones:

```yaml
# config/dimensions/custom_kafka_cluster.yaml
apiVersion: strata.huybrechts.xyz/v1
kind: cost-dimensions
meta:
  name: custom_kafka_cluster
  labels:
    version: "1.0.0"
    custom: true
  annotations:
    description: "Our internal Kafka sizing metrics"

spec:
  resource_type: azurerm_eventhub_namespace   # or any resource type
  display_name: "Kafka / Event Hub Cluster"
  
  metrics:
    - key: throughput
      question: "Expected message throughput?"
      default: moderate
      measures:
        low:
          label: "< 1 MB/s"
          params: { sku: "Basic", throughput_units: 1 }
        moderate:
          label: "1–20 MB/s"
          params: { sku: "Standard", throughput_units: 4 }
        high:
          label: "20–100 MB/s"
          params: { sku: "Premium", throughput_units: 8 }
        extreme:
          label: "100+ MB/s"
          params: { sku: "Dedicated", capacity_units: 2 }

    - key: retention
      question: "How long to keep messages?"
      default: standard
      measures:
        short:
          label: "1 day"
          params: { retention_days: 1 }
        standard:
          label: "7 days"
          params: { retention_days: 7 }
        long:
          label: "30 days (compliance)"
          params: { retention_days: 30, storage_gb: 500 }
        archive:
          label: "90+ days (capture to storage)"
          params: { retention_days: 90, storage_gb: 2000, capture_enabled: true }
```

#### Metric Granularity — User Controls Detail Level

A dimension file can range from **simple** (2 metrics, get 80% accuracy) to **detailed** (10 metrics, get 95% accuracy). The user decides how much effort to invest:

**Minimal (2 metrics — good enough for rough estimates):**
```yaml
metrics:
  - key: size
    question: "How big is this resource?"
    measures: { small, medium, large, xlarge }
  - key: availability
    question: "How critical?"
    measures: { dev, production, high-availability }
```

**Detailed (8 metrics — precise estimates):**
```yaml
metrics:
  - key: compute
  - key: memory
  - key: storage_type
  - key: storage_size
  - key: iops
  - key: network_egress
  - key: backup_retention
  - key: replication
```

**The user assigns measures only to metrics they care about.** Unassigned metrics use their `default` measure.

---

### 2. Scenarios — "Named Bundles of Measures"

A scenario assigns a **measure** to each **metric**, optionally with per-resource overrides. It's the user's estimate of their workload characteristics — expressed once, applied everywhere.

```yaml
# config/scenarios/startup.yaml
apiVersion: strata.huybrechts.xyz/v1
kind: cost-scenario
meta:
  name: startup
  labels:
    version: "1.0.0"
  annotations:
    description: "Early-stage startup — cost-optimized, minimal redundancy"

spec:
  # Default measures for ALL metrics across ALL resources
  measures:
    data_volume: small
    traffic: quiet
    availability: dev
    compute: light
    storage: minimal
    network: minimal
    throughput: low        # for kafka/event resources
    retention: short

  # Per-resource overrides (only where this resource differs from defaults)
  overrides:
    main-db:
      data_volume: medium     # "our main DB is bigger than typical for a startup"
    
    cache:
      traffic: moderate       # "cache is hit more often"
```

```yaml
# config/scenarios/enterprise.yaml
apiVersion: strata.huybrechts.xyz/v1
kind: cost-scenario
meta:
  name: enterprise
  annotations:
    description: "Enterprise production — reliability over cost"

spec:
  measures:
    data_volume: large
    traffic: busy
    availability: high
    compute: heavy
    storage: large
    network: heavy

  overrides:
    main-db:
      availability: critical    # DB is most critical piece
      traffic: extreme
    cache:
      compute: memory           # cache needs RAM, not CPU
      traffic: extreme
    monitoring:
      data_volume: massive      # logs and metrics accumulate
      availability: standard    # monitoring doesn't need HA itself
```

```yaml
# config/scenarios/peak-season.yaml
apiVersion: strata.huybrechts.xyz/v1
kind: cost-scenario
meta:
  name: peak-season
  annotations:
    description: "Black Friday / holiday peak — max capacity"

spec:
  extends: enterprise     # inherit all enterprise settings, override specific ones
  
  overrides:
    web-servers:
      compute: heavy
      count_multiplier: 3   # triple the VMs during peak
    cache:
      compute: memory
      traffic: extreme
```

---

### 3. Environment → Scenario Binding

Scenarios attach to environments (the natural place — "dev is small, prod is big"):

```yaml
# environments/dev.yaml
apiVersion: strata.huybrechts.xyz/v1
kind: environment
meta:
  name: dev
spec:
  scenario: startup
  variables: { ... }
```

```yaml
# environments/prd.yaml
kind: environment
meta:
  name: prd
spec:
  scenario: enterprise
  variables: { ... }
```

A deployment inherits the scenario from its environment. Can also be overridden:

```yaml
# deploy/deploy-prd.yaml
kind: deployment
meta:
  name: production
spec:
  environment: prd           # → scenario: enterprise (inherited)
  # scenario: peak-season   # ← optional override for this specific deploy
```

---

### 4. Community Dimensions (Shared via Git)

Strata already has `strata repo add/remove/sync` for managing external repositories. Community dimensions use the same mechanism:

```bash
# Add the official community dimensions pack
strata repo add --name strata-dimensions --url https://github.com/huybrechtsxyz/strata-dimensions.git

# Sync to get latest
strata repo sync
```

#### Community Repository Structure

```
strata-dimensions/
├── azure/
│   ├── compute/
│   │   ├── azurerm_linux_virtual_machine.yaml
│   │   ├── azurerm_windows_virtual_machine.yaml
│   │   └── azurerm_virtual_machine_scale_set.yaml
│   ├── databases/
│   │   ├── azurerm_mssql_database.yaml
│   │   ├── azurerm_cosmosdb_account.yaml
│   │   ├── azurerm_postgresql_flexible_server.yaml
│   │   └── azurerm_redis_cache.yaml
│   ├── containers/
│   │   ├── azurerm_kubernetes_cluster.yaml
│   │   ├── azurerm_container_app.yaml
│   │   └── azurerm_container_group.yaml
│   ├── networking/
│   │   ├── azurerm_application_gateway.yaml
│   │   ├── azurerm_nat_gateway.yaml
│   │   └── azurerm_firewall.yaml
│   ├── storage/
│   │   └── azurerm_storage_account.yaml
│   └── integration/
│       ├── azurerm_api_management.yaml
│       ├── azurerm_eventhub_namespace.yaml
│       └── azurerm_servicebus_namespace.yaml
├── aws/
│   ├── compute/
│   │   ├── aws_instance.yaml
│   │   └── aws_autoscaling_group.yaml
│   ├── databases/
│   │   ├── aws_db_instance.yaml
│   │   └── aws_dynamodb_table.yaml
│   └── ...
├── gcp/
│   └── ...
└── meta/
    ├── README.md
    ├── CONTRIBUTING.md
    └── schema.yaml          # validation schema for dimension files
```

#### Resolution Order (Custom Overrides Community)

When looking up dimensions for a resource type:

```
1. config/dimensions/{resource_type}.yaml       ← workspace-local custom (wins)
2. @{enterprise-store}/dimensions/...           ← enterprise store (see ADR-0035)
3. @{community-store}/dimensions/...            ← community store
4. src/strata/pricing/dimensions/built-in/      ← strata built-in (fallback)
5. (no dimensions found → use unit_cost field if present → "unknown" if not)
```

> **Enterprise stores** (ADR-0035) allow organizations to maintain private dimension
> packs shared across all their workspaces. Platform teams curate sizing standards;
> project teams consume them. The resolution order ensures local overrides always win.

#### Community Governance

```yaml
# Each community dimension file includes quality metadata
meta:
  labels:
    version: "1.2.0"           # semver — breaking changes = major bump
    provider: azure
    service_family: databases
    accuracy: "high"           # high | medium | low
    last_verified: "2026-06"   # when pricing was last validated
    terraform_resources:       # which TF resource types this covers
      - azurerm_mssql_database
      - azurerm_mssql_managed_instance
```

Community can contribute via PR to the dimensions repo. Strata validates:
- Schema compliance (valid dimension structure)
- No duplicate resource types (or explicit override declaration)
- Version bumps on changes
- Pricing accuracy tests (optional CI that spot-checks API responses)

---

### 5. Pricing Engine — Translates Measures → Money

```python
class CostEngine:
    """Translates scenario measures + dimension metrics → cloud pricing → monthly cost."""
    
    def calculate(
        self,
        resources: List[ResourceModel],
        scenario: CostScenarioModel,
        dimensions_registry: DimensionsRegistry,
    ) -> DeploymentCostEstimate:
        """
        For each resource:
        1. Load metrics for its resource_type (from dimension file)
        2. Resolve measures from scenario (defaults + overrides)
        3. Map measures → cloud parameters (SKU, size, region)
        4. Query pricing API (Azure Retail Prices, AWS Pricing, etc.)
        5. Apply multipliers (HA, geo-replication, etc.)
        6. Sum per-resource → per-stage → deployment total
        """
        
    def compare_scenarios(
        self,
        resources: List[ResourceModel],
        scenarios: List[CostScenarioModel],
        dimensions_registry: DimensionsRegistry,
    ) -> ScenarioComparison:
        """Compare costs across multiple scenarios."""
```

#### Pricing Sources

| Source                    | Auth          | Latency | Used When                         |
| ------------------------- | ------------- | ------- | --------------------------------- |
| Azure Retail Prices API   | None (public) | ~1-3s   | Azure resources (primary)         |
| AWS Price List API        | AWS creds     | ~2-5s   | AWS resources                     |
| GCP Cloud Billing Catalog | GCP creds     | ~2-5s   | GCP resources                     |
| Local cache               | None          | Instant | Cached responses (refresh weekly) |
| Fallback: unit_cost field | None          | Instant | No dimensions available           |

#### Cache Strategy

Prices don't change hourly — they change monthly at most. Cache aggressively:

```
.strata/cache/
├── pricing/
│   ├── azure/
│   │   ├── Virtual_Machines_westeurope.json     # cached API responses
│   │   ├── SQL_Database_westeurope.json
│   │   └── _meta.json                          # { last_refresh: "2026-07-01" }
│   └── aws/
│       └── ...
```

`strata cost refresh` forces cache refresh. Default: auto-refresh if cache > 7 days old.

---

### 6. CLI Commands

```bash
# Show cost estimate for a deployment (uses environment's scenario)
strata cost show -f deploy/prod.yaml

# Compare all scenarios for a deployment
strata cost compare -f deploy/prod.yaml

# Show what dimensions exist for a resource type
strata cost dimensions --resource-type azurerm_mssql_database

# Show available scenarios in this workspace
strata cost scenarios

# Refresh pricing cache
strata cost refresh

# Export cost breakdown as CSV
strata cost export -f deploy/prod.yaml --format csv
```

**Example output — `strata cost compare`:**

```
📊 Cost Comparison: deploy-prd.yaml

Scenario        │ Monthly    │ Hourly  │ vs startup
────────────────┼────────────┼─────────┼────────────
startup         │     €420   │  €0.58  │ baseline
growth          │   €2,100   │  €2.88  │ +400%
enterprise      │   €8,900   │ €12.19  │ +2,019%
peak-season     │  €14,200   │ €19.45  │ +3,281%

Currently bound: prd → enterprise

Breakdown (enterprise):
  main-db         │  €1,902/mo  │ extreme traffic, critical HA
  web-servers (3) │  €2,760/mo  │ heavy compute, large storage
  cache           │  €1,400/mo  │ memory-optimized, extreme traffic
  aks-cluster (5) │  €2,100/mo  │ heavy compute, standard HA
  monitoring      │    €738/mo  │ massive data, standard HA
```

---

### 7. AI Agent Integration

An AI agent (VS Code chat participant or CLI interactive mode) can help users create scenarios by interviewing them:

```
User: @strata I need to estimate costs for our platform

AI:   I'll help you create a cost scenario. Let me ask a few questions
      about your workload:

      1. What kind of application is this?
         → "B2B SaaS for document management, ~200 companies"

      2. How much data overall?
         → "Each company has about 2GB of structured data + 10GB documents"

      3. How busy during peak hours?
         → "About 500 concurrent users at peak"

      4. How critical is uptime?
         → "Very — our SLA is 99.9%"

AI:   Based on that, I'd suggest these scenarios:

      📊 "normal-ops" (your day-to-day):
         • DB: medium data (400GB total), busy traffic, high availability
         • Storage: large (2TB for documents)
         • VMs: balanced compute × 3 (500 concurrent users)
         → Estimated: €4,200/month

      📊 "year-one" (starting with 50 companies):
         • DB: small, moderate traffic, standard
         • Storage: medium (500GB)
         • VMs: balanced × 2
         → Estimated: €1,600/month

      Want me to create these scenario files?
```

The agent can also:
- **Review vs actuals**: Compare estimated costs to real Azure/AWS billing
- **Suggest right-sizing**: "Your cache is sized 'extreme' but actual usage is 'moderate'"
- **Generate dimensions**: For custom resource types the community doesn't cover yet

---

### 8. Output: cost.json per Build

Generated during `strata build run`, stored alongside `platform.json`:

```
build/{deployment}-{version}/
├── platform.json
├── platform.yaml
├── sbom.json
├── cost.json               ← NEW
└── terraform/{provisioner}/
```

```json
{
  "apiVersion": "strata.huybrechts.xyz/v1",
  "kind": "cost-estimate",
  "meta": {
    "name": "production",
    "labels": {
      "version": "1.0.0",
      "environment": "prd",
      "scenario": "enterprise"
    }
  },
  "spec": {
    "calculated_at": "2026-07-11T14:30:00Z",
    "currency": "EUR",
    "scenario": "enterprise",
    "confidence": "high",
    "sources": ["azure_retail_prices", "cache"],
    
    "summary": {
      "monthly": 8900.00,
      "hourly": 12.19,
      "resource_count": 12,
      "dimensions_coverage": "92%"
    },
    
    "by_category": {
      "compute": 4860.00,
      "databases": 1902.00,
      "networking": 1400.00,
      "storage": 738.00
    },
    
    "by_stage": {
      "infrastructure": 7500.00,
      "configuration": 1400.00
    },
    
    "resources": [
      {
        "name": "main-db",
        "resource_type": "azurerm_mssql_database",
        "display_name": "Azure SQL Database",
        "measures": {
          "data_volume": "large",
          "traffic": "extreme",
          "availability": "critical"
        },
        "resolved_params": {
          "sku": "BC_Gen5_8",
          "storage_gb": 500,
          "zone_redundant": true,
          "geo_replication": true
        },
        "monthly": 1902.00,
        "confidence": "high",
        "source": "azure_retail_prices"
      }
    ],
    
    "comparison": {
      "startup": 420.00,
      "growth": 2100.00,
      "enterprise": 8900.00,
      "peak-season": 14200.00
    }
  }
}
```

---

### 9. Policy Integration

Cost policies work with the scenario model:

```yaml
# configuration spec
spec:
  policies:
    - name: prod_cost_gate
      type: cost_threshold
      phase: deploy
      config:
        max_monthly: 10000
        severity: deny
        
    - name: scenario_mismatch
      type: cost_scenario_check
      phase: build
      config:
        environment_pattern: "prd*"
        required_min_availability: standard   # prod must not use "dev" availability
        severity: deny
        
    - name: dev_cost_cap
      type: cost_threshold
      phase: build
      config:
        environment_pattern: "dev*"
        max_monthly: 500
        severity: warn
```

---

### 10. Extension Points

#### Custom Dimension Sources

Users register dimension sources in their workspace configuration:

```yaml
# configuration spec
spec:
  cost:
    dimension_sources:
      # Official community pack (auto-synced via strata repo)
      - source: "@strata-dimensions"
        priority: 100           # lower wins on conflict
      
      # Company-wide shared dimensions
      - source: "@company-infra/dimensions"
        priority: 50
      
      # Local workspace overrides (always highest priority)
      - source: "config/dimensions"
        priority: 10
```

#### Custom Pricing Providers

For private clouds or custom pricing:

```python
# .strata/pricing_providers/internal_cloud.py
class InternalCloudPricing(BasePricingProvider):
    """Custom pricing for our internal VMware/OpenStack cluster."""
    
    def get_price(self, resource_type: str, params: dict, region: str) -> float:
        # Internal pricing logic
        pass
```

#### Scenario Templates (Community)

The community dimensions repo can also ship scenario templates:

```yaml
# strata-dimensions/templates/scenarios/saas-b2b.yaml
apiVersion: strata.huybrechts.xyz/v1
kind: cost-scenario-template
meta:
  name: saas-b2b-growth
  annotations:
    description: "Template for B2B SaaS in growth phase (~100-1000 customers)"
    
spec:
  defaults:
    data_volume: medium
    traffic: moderate
    availability: standard
    compute: balanced
    
  # Guidance for the AI agent / user
  guidance:
    - "Adjust data_volume based on average customer data size × customer count"
    - "If you have heavy background processing, bump compute to 'heavy'"
    - "For SOC2/HIPAA compliance, set availability to 'high' minimum"
```

---

## Open Questions

1. **Where do scenarios live?** Environment-level (recommended) vs workspace-level vs deployment-level?
   - *Decision*: Environment binds to scenario. Deployment inherits. Can override.

2. **Dimension versioning**: When community updates dimensions, do existing scenarios break?
   - *Decision*: Dimensions use semver. New options are additive (non-breaking). Removing options = major bump.

3. **Accuracy target**: How accurate do estimates need to be?
   - *Decision*: ±20% for dimensions-based estimates. Show confidence indicator.

4. **Offline mode**: What if pricing APIs are unreachable?
   - *Decision*: Use cached prices (default 7-day TTL). Show "cached" indicator.

5. **Multi-currency**: Users want EUR, not just USD?
   - *Decision*: Azure API supports currency parameter. Store in workspace config.

6. **unit_cost field**: What happens to the existing field?
   - *Decision*: Kept as ultimate fallback. If dimensions AND API both unavailable, fall back to unit_cost. Deprecate over time.

---

## Implementation Roadmap

### Phase 1 — Dimensions + Scenarios (MVP)

- [ ] Define `cost-dimensions` YAML schema and model
- [ ] Define `cost-scenario` YAML schema and model  
- [ ] Add `scenario` field to environment model
- [ ] Build DimensionsRegistry (load from local + repos)
- [ ] Build CostEngine (scenario → params → price lookup)
- [ ] Azure Retail Prices API integration (no auth)
- [ ] Price cache layer
- [ ] `strata cost show` command
- [ ] `strata cost compare` command
- [ ] Generate `cost.json` during build
- [ ] Ship 10 built-in Azure dimension files (top resource types)
- [ ] Tests: calculation accuracy, scenario resolution, cache

### Phase 2 — Community + Tooling

- [ ] Create `strata-dimensions` community repository
- [ ] Ship 30+ Azure dimension files
- [ ] Ship 20+ AWS dimension files
- [ ] `strata cost dimensions` command (browse/search)
- [ ] `strata cost scenarios` command (list/detail)
- [ ] Scenario comparison in VS Code extension
- [ ] Cost breakdown in deployment manifest
- [ ] Cost policies (threshold, scenario-check)
- [ ] `strata new cost-dimensions` scaffolding command
- [ ] `strata new cost-scenario` scaffolding command

### Phase 3 — AI Agent + Advanced

- [ ] AI agent for scenario generation (VS Code chat)
- [ ] Scenario templates (community)
- [ ] AWS Pricing API integration
- [ ] GCP Billing Catalog integration
- [ ] Cost reconciliation (estimated vs actual)
- [ ] Right-sizing recommendations
- [ ] Cost history and trending
- [ ] Multi-tenant cost attribution and chargeback

---

## Success Criteria

- ✅ Users can estimate costs without knowing cloud pricing
- ✅ Scenario comparison shows cost differences across environments
- ✅ Zero extra YAML needed per resource (dimensions handle it)
- ✅ Community dimensions cover top 30 resource types per cloud
- ✅ Estimates within ±20% of actual costs
- ✅ Cost file generated per build (auditable)
- ✅ AI agent can generate scenarios from workload descriptions
- ✅ Custom dimensions supported for internal/private resources
- ✅ Works offline (cached prices + local dimensions)
