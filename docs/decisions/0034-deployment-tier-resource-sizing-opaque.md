# Deployment-Tier Resource Sizing Stays Opaque to Strata

- Status: accepted
- Date: 2026-10-08
- Related: [ADR-0004](0004-resource-model-design-decisions.md) (resource
  model; D3/D4 define the `properties`/`configuration`/`custom` split and
  the `configuration` schema validation this decision deliberately does
  not touch), [ADR-0019](0019-version-pinning.md) (records that
  `Environment.spec.overrides` was not ported — the subtree that would
  have been the obvious home for a tier-override layer), [ADR-0025](0025-strata-supplies-input-not-source-rewriting.md)
  (strata supplies input rather than rewriting sources — this decision is
  the same principle applied to resource sizing specifically),
  [docs/design/build-time-value-categories.md](../design/build-time-value-categories.md)
  (the `properties`/`custom` per-deployment merge chain this decision
  reuses unchanged), [docs/design/deployment-tier-resource-sizing.md](../design/deployment-tier-resource-sizing.md)
  (the design doc this decision is recorded for)

## Context and Problem Statement

A deployment needs some resources — storage account tier, VM size,
database SKU, node count — to vary by a customer's assigned deploy tier
(e.g. `basic`/`standard`/`advanced`/`enterprise`), while everything else
about the workspace stays identical. Resource SKUs live in
`ResourceSpecModel.configuration`/`WorkspaceResourceModel.configuration`,
merged by `_merge_resource_entry()` from exactly two layers (resource
document + workspace instance) — there is no deployment- or
environment-level layer in that merge at all, and `configuration` is
schema-validated per resource type (ADR-0004 D3/D4), so its key space is
not free to nest a tier axis into without special-casing the validator.

## Considered Options

- **Tier-keyed variants inside the resource document**
  (`spec.configuration_by_tier: {basic: {...}, enterprise: {...}}`,
  selected at build time). Rejected — a new top-level field outside
  `configuration` (required by the schema-validation constraint above),
  and every resource document would carry a tier axis whether or not it
  actually varies by tier.
- **One resource document per tier** (`storage-account-basic`,
  `storage-account-enterprise`, selected by which document the workspace
  resource binds to). Rejected — no schema change, but an N×M document
  explosion duplicating everything that does *not* vary by tier.
- **A real per-deployment `configuration` override layer**
  (`DeploymentModel.spec.resources[name].configuration`, merged as a
  third layer in `_merge_resource_entry()`). Rejected as the default
  answer — the most general option, but also the most invasive: a new
  merge layer, new precedence rules, a deep-vs-shallow merge question,
  and it partially un-defers the `overrides` subtree ADR-0019 already
  decided not to port, for a need with no real consumer evidence behind
  it yet.
- **Value tokens inside `configuration`** (`account_tier:
  "${var:storage_tier}"`, resolved per deployment by a future
  `resolve_expr_tokens()`). Rejected as the primary mechanism — per-key,
  not per-tier (a "tier" would only exist as an implicit convention
  across N separate variable declarations, not one named concept), and
  the token-resolution mechanism it depends on does not exist yet.
- **Tier is a plain, opaque value; the tier→SKU mapping lives entirely in
  the consuming Terraform module** (chosen) — a deployment declares its
  tier once as a `properties` value (e.g. `deploy_tier: standard`),
  already flowing through the existing, real, per-deployment `properties`
  deep-merge chain (Workspace → each reachable Environment → Deployment —
  [build-time-value-categories.md](../design/build-time-value-categories.md)).
  The `.tf` module does its own `lookup(var.sku_by_tier, var.properties.
  deploy_tier)` (or equivalent) wherever it needs a concrete SKU.

## Decision Outcome

Chosen: **no new strata schema, no new merge layer, no fixed tier
vocabulary.** `deploy_tier` (or whatever name a given workspace chooses)
is just another `properties` key, carried through the merge chain that
already exists for exactly this purpose — zero implementation needed, and
already proven working end to end in this repo's own `config/` dogfooding
solution (`customers/c0062.yaml`'s `properties.deploy_tier: standard`,
`environments/prd.yaml`'s `properties.deploy_tier: production`). The
tier→SKU table, and the decision of which resources a tier actually
affects, stay entirely inside the Terraform module's own code — strata
never validates or knows the tier vocabulary, matching ADR-0025's
"strata supplies input, it does not own the shape of what's built with
it" principle.

Resource `configuration` itself is untouched by this decision — a tier
value never reaches `resources_by_category`/`resx_<type>.auto.tfvars.json`
directly; it only ever reaches the module as a plain `TF_VAR_deploy_tier`
(or similar) value the module's own HCL chooses what to do with.

## Consequences

- Good: zero schema change, zero new merge layer — the mechanism already
  existed and is already proven in real use (`config/`'s own example).
- Good: no workspace is forced into a tier vocabulary or a dedicated field
  it doesn't need — a workspace with no tiering concept carries no extra
  shape at all.
- Good: onboarding a new tier, or changing which resources a tier affects,
  is a pure Terraform-module change — no strata release involved.
- Bad: the tier→SKU mapping is invisible to strata itself — `strata
  validate` cannot catch an unknown tier name, and no strata-side cost
  estimation over tiers is possible without the module exposing that
  mapping back out somehow. Accepted: no real consumer need for either
  has been evidenced yet; revisit only if one appears (the clearest
  trigger would be a future cost-estimation feature that needs the
  tier→SKU table to be strata-visible, not module-internal).
- Neutral: `properties` stays the one and only channel for this — a
  future resource-`configuration` merge layer (the rejected option above)
  remains available to reconsider later if a real need for strata-visible,
  schema-validated tier-specific `configuration` actually shows up, but
  is not built speculatively now.
