# Deployment-Tier Resource Sizing (per-deployment SKU selection) — Design

- Status: **deferred — question captured, no decision, no implementation**
- Last updated: 2026-09-25

## Overview

Open question, parked deliberately: **how does the same resource get a
different SKU/size in different deployments?**

The concrete future shape (not yet real anywhere): a customer is assigned a
*deploy tier* — e.g. `basic` / `standard` / `advanced` / `enterprise` — and
that tier changes the SKU of *some* (not all) resources: storage account
tier, VM size, database SKU, node count, and so on. Everything else about
the workspace stays identical.

This is **not in scope for the current build pipeline work**, and nothing
here should block it:

- `Environment.spec.overrides` — the v1 subtree that would have been the
  obvious home for this — was deliberately **not ported** to v2 (0/26 real
  usage; see [ADR-0019](../decisions/0019-version-pinning.md) and the
  `environment` row in [v2-schema-overview.md](v2-schema-overview.md)).
  The one exception is `overrides.properties`, which
  [build-time-value-categories.md](build-time-value-categories.md) Q3 folds
  into the `properties` merge chain — and `properties` is a *different
  channel* from resource `configuration` (see below), so it does not answer
  this question.
- `cfg-deployment` does **not** have a deploy-tier concept today. There
  is no real consumer, so there is no evidence to ground a schema decision
  against — and "no real usage found" is exactly the criterion this repo has
  used to defer features elsewhere.

This doc exists so the question is written down with its real constraints,
rather than being answered implicitly (and probably wrongly) as a side
effect of some other change.

## Why this is a genuinely open question

Where a SKU actually lives today, end to end:

- `ResourceSpecModel.configuration: dict[str, Any]` — the free-form
  per-resource-type config bag. The real example in the repo is exactly a
  SKU field: `config/resources/compute.yaml`'s `storage-account` declares
  `configuration.account_tier: Standard`.
- `WorkspaceResourceModel.configuration` — "workspace-specific
  configuration overrides (merged with resource file configuration)"
  (`workspace_model.py`).
- `_merge_resource_entry()` (`terraform_projection.py`) merges exactly those
  two, workspace-wins:
  `{**resource_spec.configuration, **workspace_resource.configuration}` —
  a **shallow, two-layer** merge — and emits it into
  `resources_by_category`, which `planned_files()` unrolls into
  `resx_<resource_type>.auto.tfvars.json`.

Three consequences that constrain any future answer:

1. **There is no deployment- or environment-level layer in that merge at
   all.** The only override point is the workspace instance, and a workspace
   is not per-deployment. Whatever the answer turns out to be, it is a *new*
   layer, not a wiring-up of something that already exists.
2. **`properties`/`custom` cannot be the vehicle as-is.** They do have a
   real per-deployment merge chain (workspace → environment(+
   `overrides.properties`) → deployment's own, per
   [build-time-value-categories.md](build-time-value-categories.md) Q3), but
   `terraform_projection.py`'s module docstring is explicit that
   `resources_by_category` reads **only** `configuration` — `properties` and
   `custom` are separate channels on purpose. A tier value can reach
   Terraform through `properties`; it cannot reach a *specific resource's*
   `configuration` block through it.
3. **`configuration` is schema-validated, so its key space is not free.**
   `ResourceService` validates `spec.configuration` field-by-field against
   `ConfigurationModel.spec.providers[type].resources[resource_type].configuration`
   (regex per field, required/optional, `additional_configurations` gate —
   [ADR-0004](../decisions/0004-resource-model-design-decisions.md) D3/D4).
   Any design that nests tier names *inside* `configuration`
   (`configuration.basic.account_tier: ...`) breaks that validator or
   forces it to grow a special case.

## Candidate approaches (none chosen)

| # | Approach | Sketch | Main trade-off |
| --- | --- | --- | --- |
| A | **Tier is a value, mapping lives in Terraform** | Deployment declares its tier once (a `properties` key or a `variable`); the `.tf` module does `lookup(var.sku_by_tier, var.properties.deploy_tier)` | Zero strata schema change; strata stays dumb about SKUs. But the tier→SKU table lives in user `.tf` code, invisible to `strata validate`, `strata cost`, and any non-Terraform integration |
| B | **Tier-keyed variants inside the resource document** | `spec.configuration_by_tier: {basic: {...}, enterprise: {...}}`, selected at build time | Declarative and visible to strata. But it's a new top-level field (not `configuration`, per constraint 3), and every resource document grows a tier axis whether or not it varies |
| C | **One resource document per tier** | `storage-account-basic`, `storage-account-enterprise`; the deployment selects which document the workspace resource binds to | No schema change at all — uses document identity, which already works. But N×M document explosion and heavy duplication of everything that *doesn't* vary |
| D | **A real per-deployment `configuration` override layer** | `DeploymentModel.spec` (or a revived `Environment.spec.overrides`) gains `resources[name].configuration`, merged as a third layer in `_merge_resource_entry()` | Most general and the closest to what users would guess. Also the biggest: a new merge layer, new precedence rules, deep-vs-shallow question, and it partially un-defers the `overrides` subtree v2 deliberately dropped |
| E | **Value tokens inside `configuration`** | `account_tier: "${var:storage_tier}"`, resolved per deployment by Phase 3's `resolve_expr_tokens()` | Reuses a mechanism already planned ([ADR-0002](../decisions/0002-requirement-interface-injection-grant-lessons-from-v1.md)); per-deployment variation comes free from the environment values that already vary per deployment. But it's per-key, not per-tier — "tier" becomes an implicit convention across N variable declarations rather than one named concept, and `resolve_expr_tokens()` does not exist yet |

Rough current leaning, to be re-argued when this is picked up, not treated
as decided: **A or E for the first real consumer** (neither needs a schema
change, and both can be delivered without touching the resource merge), with
**D** as the answer only if a real consumer shows that the tier→SKU mapping
genuinely has to be visible to strata itself (cost estimation is the most
likely forcing function — a tier table hidden in `.tf` code can't be costed).

## Triggers to un-defer

Pick this up when **any one** of these becomes true — not before:

1. `cfg-deployment` (or another real consumer) actually introduces a
   deploy-tier / customer-tier concept, giving a concrete schema to ground
   against.
2. A strata feature needs the tier→SKU mapping itself — cost estimation over
   tiers being the obvious one, since approach A deliberately hides the
   mapping from strata.
3. The `Environment.spec.overrides` subtree is revisited for any other
   reason, at which point D should be evaluated in the same pass rather than
   separately.

## Related Decisions

- [ADR-0004](../decisions/0004-resource-model-design-decisions.md) — resource
  model; D3/D4 define the `properties`/`configuration`/`custom` split and the
  `configuration` schema validation that constrains approach B.
- [ADR-0019](../decisions/0019-version-pinning.md) — records that
  `Environment.spec.overrides` was not ported (0/26 real usage).
- [ADR-0023](../decisions/0023-build-output-rendering.md) — the
  `resources_by_category` projection and `resx_<type>.auto.tfvars.json`
  output this would have to feed.
- [ADR-0024](../decisions/0024-tenant-defaults-merge.md) — the deployment
  merge (`merge_deployment_specs()`) that approach D would extend.
- [build-time-value-categories.md](build-time-value-categories.md) — the
  `properties`/`custom` per-deployment merge chain (Q3), and why it is a
  separate channel from resource `configuration`.
- [v2-schema-overview.md](v2-schema-overview.md) — `environment` row: the
  `overrides`/`lifecycle`/`promotion` exclusions.

## Remaining Work / Open Questions

Nothing. Deliberately no work item — this doc's purpose is to hold the
question, its constraints, and the candidate answers until one of the
triggers above fires.

## Changelog

- 2026-09-25: Created. Captured the per-deployment SKU / deploy-tier question
  raised while resolving [build-time-value-categories.md](build-time-value-categories.md)'s
  `properties`/`custom` merge chain, after confirming that (a) resource SKUs
  live only in `configuration`, (b) `configuration` is merged from exactly
  two layers (resource document + workspace instance) with no deployment
  layer, and (c) `properties`/`custom` — which *do* have a per-deployment
  chain — are never read into `resources_by_category`. Listed five candidate
  approaches and three un-defer triggers; chose none.
