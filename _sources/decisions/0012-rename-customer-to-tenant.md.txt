# Rename CustomerModel to TenantModel

- Status: proposed
- Date: 2026-06-23
- Issue: naming collision between "customer" layer and CustomerModel entity

## Context and Problem Statement

Strata has a `CustomerModel` (defined in `customers/<code>.yaml`) that represents an
organizational entity owning deployments. It carries zone constraints, properties, and
metadata. Deployments reference it via `spec.customer: <code>`, which triggers file
resolution, zone-alignment validation, and artifact embedding.

Separately, the layering system (`configuration.spec.layering[]`) allows any layer name
— including `customer`. Many DevOps teams naturally use "customer" as a layer key in
`deployment.spec.layers`. This creates naming confusion:

- **`spec.layers.customer`** — a path segment key for artifact structure. Generic, could
  be any string. Part of the layering abstraction.
- **`spec.customer`** — a pointer to a `CustomerModel` file. Triggers validation logic.
  Structural field with side effects.
- **`CustomerModel`** — the entity definition (`customers/acme.yaml`). Zones, properties,
  constraints.

The collision causes confusion:

1. Users ask: "Is `spec.layers.customer` the same as `spec.customer`?" (No.)
2. Users with a "customer" layer but no customer files wonder where the files should be.
3. Users who don't call their entity "customer" (internal teams, projects, self-hosted)
   find the naming misleading.

Real-world scenarios that expose the problem:

- **Haven (personal project):** The owner IS the only "customer" but still uses zone
  constraints. Calling it "customer" feels wrong — it's just "me."
- **OMP:** Both internal teams and external customers exist. "Customer" is too narrow —
  internal teams need the same zone constraints and deployment ownership but aren't
  customers.

## Decision Drivers

- Pre-v1 — breaking schema changes are still acceptable
- The naming collision causes confusion for new users and AI agents
- The concept is generic (any entity that owns deployments) not specific (only customers)
- `match_labels` in the promotion system matches `meta.labels` — no dependency on the
  entity name. Rename is safe for the promotion design.
- Layering is generic by design — entity names should not collide with layer keys

## Considered Options

### Option A — Rename to `tenant` (recommended)

Rename the full concept:

- `CustomerModel` → `TenantModel`
- `CustomerService` → `TenantService`
- `PlatformCustomerModel` → `PlatformTenantModel`
- `deployment.spec.customer` → `deployment.spec.tenant`
- Directory: `customers/` → `tenants/`
- File pattern: `customers/<code>.yaml` → `tenants/<code>.yaml`
- YAML kind: `kind: customer` → `kind: tenant`
- Builder references: `platform_customer` → `platform_tenant`
- CLI output fields: update display labels

**Pro:** "Tenant" is platform-engineering standard. Neutral — works for external
customers, internal teams, projects, or even a single owner.

**Pro:** No collision with the layering system. Users can still name a layer "customer"
without confusion about the entity concept.

**Pro:** Widely understood across cloud platforms (Azure tenants, multi-tenant
architectures).

**Con:** Less business-friendly than "customer" for teams that only have external
customers.

### Option B — Rename to `account`

**Pro:** Familiar from AWS/cloud contexts.
**Con:** Implies cloud provider account. Confusing if you have real cloud accounts
alongside strata accounts.

### Option C — Rename to `party`

**Pro:** Fully generic (legal/business term for any entity).
**Con:** Nobody will intuitively guess what `spec.party` means. Too abstract.

### Option D — Rename to `owner`

**Pro:** Describes the relationship accurately.
**Con:** Conflicts with git/repo ownership concepts. "Who owns this deployment" is
ambiguous — the team? the customer? the platform?

### Option E — Rename to `subscriber`

**Pro:** Clear relationship to the platform.
**Con:** Implies SaaS billing model. Too niche.

### Option F — Keep `customer` (status quo)

Document the distinction between layer keys and CustomerModel clearly.

**Pro:** Zero breaking changes. Already implemented.
**Con:** Naming collision persists. Forces documentation burden on every new user.
Doesn't fit non-customer use cases (internal teams, self-hosted).

## Decision Outcome

**Option A — Rename to `tenant`.**

The concept is "an entity that owns/receives deployments and has zone constraints."
That's a tenant. The rename eliminates the naming collision with the generic layering
system and fits all real-world usage patterns.

## Impact Assessment

### Files requiring changes

| Area                | Scope                                                               |
| ------------------- | ------------------------------------------------------------------- |
| Models              | `customer_model.py` → `tenant_model.py`, class renames              |
| Services            | `customer_service.py` → `tenant_service.py`, method renames         |
| Builders            | `platform_builder.py`, `terraform_builder.py`, `ansible_builder.py` |
| Platform artifact   | `platform_artifact_model.py` field renames                          |
| Deployment model    | `spec.customer` → `spec.tenant`                                     |
| Deployment service  | Validation logic (file path, zone checks)                           |
| CLI commands        | Display labels, help text                                           |
| Directory structure | `customers/` → `tenants/` in config repos                           |
| YAML kind           | `kind: customer` → `kind: tenant`                                   |
| Tests               | All customer-related test files and fixtures                        |
| Docs                | Config docs, guides, ADRs referencing customer                      |

### Migration path

Direct rename — no deprecation period, no backwards compatibility aliases.
This is a breaking change documented in CHANGELOG.md. Users update their YAML files:

1. Rename `customers/` directory → `tenants/`
2. Change `kind: customer` → `kind: tenant` in tenant YAML files
3. Change `spec.customer: <code>` → `spec.tenant: <code>` in deployment files

## More Information

- [ADR 0011](0011-promotion-strategies-for-version-progression.md) — promotion system
  (uses `match_labels` against `meta.labels`, unaffected by this rename)
- [Deployment Configuration](../config/deployment.md) — current `spec.customer` docs
- [At Scale Guide](../guides/at-scale.md) — customer model section (will need updating)
