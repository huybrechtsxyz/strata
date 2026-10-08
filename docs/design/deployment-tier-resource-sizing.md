# Deployment-Tier Resource Sizing (per-deployment SKU selection)

- Status: current
- Last updated: 2026-10-08
- Related: [ADR-0034](../decisions/0034-deployment-tier-resource-sizing-opaque.md)
  (the decision this doc describes the shape of), [build-time-value-categories.md](build-time-value-categories.md)
  (the `properties`/`custom` per-deployment merge chain this reuses
  unchanged, Q3), [composite-variable-fragments.md](../how-to/composite-variable-fragments.md)
  (the general how-to for composing one `properties` value from several
  files — the same mechanism, a different worked example)

## Overview

Some resources — storage account tier, VM size, database SKU, node count —
need to vary by a customer's assigned deploy tier (e.g.
`basic`/`standard`/`advanced`/`enterprise`), while everything else about
the workspace stays identical. Strata adds **no schema, no new merge
layer, and no fixed tier vocabulary** for this ([ADR-0034](../decisions/0034-deployment-tier-resource-sizing-opaque.md)):
a tier is a plain, opaque `properties` value, and the tier→SKU mapping
lives entirely in the consuming Terraform module's own HCL.

## Current Design

### The value is just another `properties` key

No dedicated field, no `configuration_by_tier`, no tier-specific merge
layer — `deploy_tier` (any name a workspace chooses) flows through the
same real, already-existing `properties` deep-merge chain described in
[build-time-value-categories.md](build-time-value-categories.md) Q3:
Workspace's own `properties` → each reachable Environment's own
`properties`, in order → the Deployment's own `properties` (a Tenant's own
`environments` are folded in ahead of the Deployment's, per
`reachable_environments()`).

Already proven working, not hypothetical — this repo's own `config/`
dogfooding solution declares it today:

```yaml
# config/customers/c0062.yaml
spec:
  properties:
    deploy_tier: standard
```

```yaml
# config/environments/prd.yaml
spec:
  properties:
    deploy_tier: production
```

`properties` is a `FLAT_CATEGORIES` member, so it reaches Terraform as
`TF_VAR_deploy_tier` with zero additional strata-side work.

### The tier→SKU mapping lives in the Terraform module

Strata never validates or knows a tier's vocabulary, or which resources a
tier affects — that's the module's own concern:

```hcl
variable "deploy_tier" {
  type = string
}

locals {
  sku_by_tier = {
    basic      = { account_tier = "Standard", vm_size = "Standard_B2s" }
    standard   = { account_tier = "Standard", vm_size = "Standard_D2s_v5" }
    advanced   = { account_tier = "Premium",  vm_size = "Standard_D4s_v5" }
    enterprise = { account_tier = "Premium",  vm_size = "Standard_D8s_v5" }
  }
  sku = local.sku_by_tier[var.deploy_tier]
}

resource "azurerm_storage_account" "this" {
  account_tier = local.sku.account_tier
  # ...
}
```

### Resource `configuration` is untouched

A tier value never reaches `resources_by_category`/
`resx_<type>.auto.tfvars.json` — `ResourceSpecModel.configuration`/
`WorkspaceResourceModel.configuration`'s own two-layer merge
(`_merge_resource_entry()`) is unaffected by this design entirely. The
tier value only ever reaches the module as a separate `properties`-sourced
variable; the module's own code is what connects it to a resource's real
configuration.

## Deliberately Out of Scope

- A strata-visible tier→SKU mapping (needed for e.g. a future cost
  estimation feature spanning tiers) — the mapping lives inside the
  Terraform module today, invisible to `strata validate`/any cost
  estimator. Revisit only if a real feature needs it exposed.
- A schema-validated, tier-specific `configuration` override layer
  (considered and rejected as the default answer in
  [ADR-0034](../decisions/0034-deployment-tier-resource-sizing-opaque.md)
  — remains available to reconsider if a real need for strata-visible
  tier-specific `configuration` appears).

## History

- The real constraint this design had to fit: resource SKUs live only in
  `configuration`, which schema-validates per resource type (ADR-0004
  D3/D4) and merges from exactly two layers (resource document +
  workspace instance) with no deployment-level layer at all —
  `properties`/`custom`, which *do* have a per-deployment merge chain,
  are deliberately never read into `resources_by_category`
  (`terraform_projection.py`'s own module docstring). Any design nesting
  a tier axis inside `configuration` itself would have broken that
  validator or forced it to grow a tier-shaped special case.
- Originally parked (2026-09-25) pending either a real consumer
  introducing a deploy-tier concept, or a strata feature needing the
  tier→SKU mapping itself (cost estimation being the obvious trigger) —
  deliberately not decided up front without one of those.
- Decided (2026-10-08): resolved without waiting on either trigger, once
  it was noticed this repo's own `config/` dogfooding solution already
  demonstrated the opaque-`properties`-value approach working end to end
  (`deploy_tier` on both a Tenant and an Environment document) — sufficient
  evidence on its own that the mechanism works, independent of whether any
  external consumer has adopted it yet. A fixed tier vocabulary or a new
  merge layer would have forced every workspace into a specific shape
  whether or not it needed tiering at all; the chosen approach keeps
  strata opaque to it entirely, matching ADR-0025's existing principle.
