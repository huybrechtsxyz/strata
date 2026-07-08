# Tenant Configuration

A tenant represents a **single customer or client** that gets its own isolated instance of your platform. The tenant file captures identity, data-residency constraints, and the default configuration layers applied to every deployment scoped to that tenant.

## Conceptual Model

| Field                         | Purpose                                                                 |
| ----------------------------- | ----------------------------------------------------------------------- |
| **Identity** (`code`, `name`) | Who this tenant is                                                      |
| **Zones**                     | Where they are allowed to deploy                                        |
| **Environments**              | Base value layers applied before deployment environments                |
| **Properties / Custom**       | Deployment defaults the tenant contributes (deployment overrides)       |
| **Configuration**             | Metadata passed verbatim into Terraform/Ansible via `strata_tenant`     |
| **References**                | Variables, secrets, and feature flags this tenant's deployments require |

**Deployment = Tenant defaults + Deployment overrides**

## Schema

```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: tenant
meta:
  name: <code>              # Required: matches spec.code and the filename
  annotations:
    description: <text>
  labels:
    tier: standard
spec:
  # --- Identity ---
  code: <code>              # Required: must match meta.name
  name: <display name>      # Required: human-readable

  # --- Data residency ---
  zones:                    # Required: at least one zone
    - <zone_name>           # Must match a name in configuration.spec.zones

  # --- Onboarding ---
  onboarded: <YYYY-MM-DD>   # Optional: informational only

  # --- Base environment layers ---
  environments:             # Optional
    - <path>                # Applied BEFORE each deployment's environments

  # --- Deployment defaults (merged into every linked deployment) ---
  properties: {}            # Optional: base layer for deployment spec.properties
  custom: {}                # Optional: base layer for deployment spec.custom

  # --- Terraform/Ansible metadata ---
  configuration: {}         # Optional: emitted inside strata_tenant tfvar object

  # --- Runtime references ---
  references:               # Optional
    variables: []
    secrets: []
    features: []
```

## Field Reference

### `spec.code` and `meta.name`

Both must be identical — validated at Phase 2. Convention is a short lowercase slug matching the filename:

```
tenants/acme.yaml  →  meta.name: acme  →  spec.code: acme
```

### `spec.zones`

Restricts where this tenant's deployments may provision resources. Each zone must exist in `configuration.spec.zones`. At build time, every provider's resolved zone is checked against this list.

```yaml
zones:
  - eu-west    # only EU-West providers allowed
```

### `spec.environments`

**Base environment files merged *before* each deployment's own environments.** Deployment values take precedence over these.

Use this to inject tenant-wide defaults — shared variables, feature flags, secrets for a tier — once, without repeating them in every deployment file.

```yaml
environments:
  - environments/tiers/enterprise.yaml   # shared enterprise tier defaults
```

Layering order (lowest → highest precedence):

```
tenant.spec.environments[0..N]
  └─ deployment.spec.environments[0..N]  ← wins
```

### `spec.properties`

Tenant-wide deployment properties, merged as a **base layer** into `spec.properties` of every deployment that references this tenant. Deployment properties win on any overlapping key.

```yaml
properties:
  tier: enterprise
  billing: monthly
```

If the deployment also sets `tier: standard`, the effective value is `standard`.

### `spec.custom`

Same base-layer merge as `properties`, but for `spec.custom`.

```yaml
custom:
  owner: Platform Team
  costcenter: INFRA-001
```

### `spec.configuration`

Arbitrary key/value metadata emitted **verbatim** inside the `strata_tenant` Terraform variable object. This is not merged into deployment properties — it is tenant identity data consumed directly by Terraform or Ansible modules.

```yaml
configuration:
  crm_id: "42"
  invoice_prefix: "ACM"
  support_level: enterprise
```

In Terraform:

```hcl
var.strata_tenant.configuration["crm_id"]     # → "42"
var.strata_tenant.configuration["support_level"]  # → "enterprise"
```

### `spec.references`

Declares which variables, secrets, and feature flags this tenant's deployments require. Values are resolved from the environment stack at build time — not stored here.

```yaml
references:
  variables:
    - company_domain
  secrets:
    - api_key
  features:
    - audit_logging
```

## Properties / Custom vs Configuration

| Field           | Merged into deployment?                  | Where it surfaces                                                   |
| --------------- | ---------------------------------------- | ------------------------------------------------------------------- |
| `properties`    | ✅ Yes — base layer, deployment overrides | `platform.spec.properties` → Terraform `workspace.auto.tfvars.json` |
| `custom`        | ✅ Yes — base layer, deployment overrides | `platform.spec.custom`                                              |
| `configuration` | ❌ No — passed through unchanged          | `strata_tenant.configuration` inside `tenant.auto.tfvars.json`      |

## Validation

Phase 1 (structural):
- `spec.code` must match `meta.name` pattern `^[a-z0-9][a-z0-9_-]*$`
- `spec.zones` must not be empty and must not contain duplicates

Phase 2 (dynamic, requires `--deep` or active configuration):
- `spec.code` must equal `meta.name`
- Each zone in `spec.zones` must exist in `configuration.spec.zones`
- Each path in `spec.environments` must resolve on disk

## Zone Policy

At deploy time, the `TenantZonePolicy` verifies that every Terraform resource being provisioned resides in a region that belongs to one of the tenant's declared zones. Resources in out-of-zone regions cause the policy to reject the deployment.

## Example

```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: tenant
meta:
  name: acme
  annotations:
    description: ACME Corporation — enterprise tier
  labels:
    tier: enterprise
spec:
  code: acme
  name: ACME Corporation
  zones:
    - eu-west
  onboarded: 2025-01-15

  environments:
    - environments/tiers/enterprise.yaml

  properties:
    tier: enterprise
    billing: monthly

  custom:
    owner: Platform Team
    costcenter: INFRA-001

  configuration:
    crm_id: "2001"
    invoice_prefix: ACM
    support_level: enterprise

  references:
    variables:
      - company_domain
    secrets:
      - api_key
    features:
      - audit_logging
      - sso_enabled
```

## CLI

```bash
# Validate a tenant file
strata validate tenants/acme.yaml

# Deep-validate (checks zones against configuration and file paths on disk)
strata validate tenants/acme.yaml --deep

# Scaffold a new tenant from the template
strata new tenant
```
