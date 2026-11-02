# Flatten Provider/Resource/Module's Typed `properties`; Remove Configuration's Unused Dict Fields — Design

- Status: current
- Last updated: 2026-10-13

## Overview

`properties` used to mean two unrelated things depending on which kind you
were reading. On Workspace/Environment/Deployment/Tenant it's a free-form
`dict[str, Any]`, deep-merged across documents — values that *drive*
strata's own composition. On Provider/Resource/Module it was instead a
*typed*, nested sub-model (`ProviderPropertiesModel`/`ResourcePropertiesModel`/
`ModulePropertiesModel`) holding each kind's own core, defining attributes
— not free-form, not merged, and not the same concept at all. They only
happened to share a name.

Separately, `ConfigurationSpecModel` carried the same three field names
(`properties`/`configuration`/`custom`) with zero real consumers anywhere
— inherited unexamined from the Provider/Resource three-way split rather
than a deliberate placement decision for `Configuration` itself.

## Current Design

`properties` now exists on exactly one group of kinds: the ones that
actually merge it (Workspace, Environment, Deployment, Tenant). Every
other kind gets `configuration` + `custom` and nothing else — no
exceptions, no kind wraps its own core fields in a typed `properties`
sub-model anymore. Provider/Resource/Module's former wrapper fields now
sit directly on `spec`, matching the convention every other kind
(DNS/Network/Firewall/Namespace/Integration/etc.) already followed:

```yaml
# before
spec:
  properties:
    type: azure
    region: westeurope

# after
spec:
  type: azure
  region: westeurope
```

Resource and Module followed the identical pattern (`provider_type`/
`resource_type`/`unit_cost`/`category`/`subcategory` for Resource;
`mounts`/`checks`/`endpoints` for Module). `ConfigurationSpecModel` lost
`properties`/`configuration`/`custom` outright, with no replacement —
there was no naming collision to fix on `Configuration` itself, just dead
schema surface. See [docs/config/provider.md](../config/provider.md),
[resource.md](../config/resource.md), [module.md](../config/module.md),
[configuration.md](../config/configuration.md) for each kind's full,
current schema.

No Terraform-facing output shape changed: every payload builder in
`terraform_projection.py` already produced a flat dict (`{type, region,
...}`); flattening only moved where the *source* field lives on the
Pydantic model.

## Related Decisions

- [ADR-0037](../decisions/0037-flatten-provider-resource-module-properties.md) —
  the Provider/Resource/Module flatten decision (naming collision, the
  flatten-vs-wrapper fork, Module's weaker-but-still-flatten reasoning).
- [ADR-0038](../decisions/0038-remove-configuration-unused-dict-fields.md) —
  the Configuration field-removal decision (dead-code removal, a
  genuinely separate decision with unrelated reasoning).
- [ADR-0002](../decisions/0002-requirement-interface-injection-grant-lessons-from-v1.md) —
  establishes "Value binding"/`${var:}` as this codebase's own
  vocabulary; the reason a renamed wrapper couldn't be called `values`.
- [ADR-0003](../decisions/0003-provider-model-design-decisions.md) /
  [ADR-0004](../decisions/0004-resource-model-design-decisions.md) /
  [ADR-0009](../decisions/0009-module-model-design-decisions.md) —
  introduced the `properties`/`configuration`/`custom` three-way split
  these kinds still have; this design doesn't reverse that split, only
  where `properties`'s own children live.
- [ADR-0017](../decisions/0017-tags-labels-configuration-custom-placement.md) —
  the per-kind `configuration`/`custom` placement decision; its own
  enumeration of which kinds deliberately gained them is what confirmed
  `Configuration` was never one of them.
- [ADR-0024](../decisions/0024-tenant-defaults-merge.md) — the Environment-
  based mechanism that already serves the "values meant to reach the
  whole system" need Configuration's now-removed fields looked like they
  were for.
- [ADR-0035](../decisions/0035-unified-terraform-projection.md) — the
  payload builders (`terraform_projection.py`) this design's flattened
  fields feed into, unchanged in shape.

## History

- Checked every other kind before treating the wrapped shape as
  "just how v2 works": 8 kinds (DNS, Network, Firewall, Namespace,
  Integration, and Environment/Deployment/Tenant's non-`properties`
  fields) already put their own core fields directly on `spec`, no
  wrapper at all. Provider/Resource/Module were the only 3 outliers —
  the minority pattern, not an established convention being changed on
  a whim.
- Rejected renaming the wrapper instead of flattening it. `values` was
  considered and rejected outright: it collides with this codebase's own
  established "Value binding" vocabulary (ADR-0002), reintroducing the
  exact class of confusion this change exists to remove, just under a
  new name. `definition`/`attributes` would have worked (both unused
  elsewhere) but still add a nesting level the other 8 kinds don't have,
  for no functional reason once flattening was on the table.
- Rejected a universal `spec.attributes` wrapper applied to every kind,
  for maximum uniformity. The kinds with no naming problem today have no
  payoff from retrofitting, and DNS/Network/Firewall's own core fields
  are themselves lists of structured sub-models (`zones`, `networks`,
  `deny`/`allow`), not flat scalars — wrapping them adds a layer without
  clarifying anything, since there's only ever the one field under it.
- Rejected re-adding a free-form `properties` to Provider/Resource/Module
  after flattening. It would have been functionally identical to
  `custom` on these non-merging kinds — the exact redundancy already
  used to deny `properties` to Namespace/DNS/Network/Firewall/
  Integration. Applying that rule consistently meant Provider/Resource/
  Module don't get it back either: `properties` now exists only on the
  kinds that actually merge it, full stop.
- Module's inclusion rests on weaker evidence than Provider's/Resource's:
  its `properties` was optional (not required), and its children had no
  *cross-document* consumer the way Provider's `type`/Resource's
  `provider_type` get real registry cross-checks
  (`provider_service.py`/`resource_service.py`/`semantic_checks.py`).
  Flattened anyway — still one of the 3 outliers, and flattening
  surfaced a genuine, independently-confirmed `type` field-name
  situation (Module's own deployer-tool `type` vs. Provider's cloud-
  provider `type` — different models, no real clash). The original
  "zero consumers" finding was itself wrong: `terraform_projection.py`'s
  `_strata_modules()` does consume `mounts`/`checks`/`endpoints` — a
  literal dotted-token grep (`module.spec.properties.mounts` as one
  token) missed the real code's two-step access (`properties = spec.
  properties`, then `.mounts` on a separate line). The corrected
  asymmetry still held, just smaller than first measured: that's a
  same-document passthrough projection, not a cross-document registry
  check.
- Configuration's `properties`/`configuration`/`custom` were never a
  deliberate placement decision for that kind — confirmed via ADR-0017's
  own enumeration of every kind that deliberately gained `configuration`/
  `custom` (Configuration isn't on that list), and via a direct grep
  finding zero real consumers anywhere in `src/`. Removed outright rather
  than flattened: there was no naming collision on `Configuration` itself
  to fix, and the "values meant to reach the whole system" need they
  looked like they were for is already met, better, by referencing a
  shared Environment document (ADR-0024's existing merge chain).
- Recurring lesson across the implementation: a green full test-suite run
  does not by itself prove a test still exercises its original intent
  once the schema shape it targets changes — `pytest.raises
  (ValidationError)` is satisfied by *any* validation failure, including
  a newly-introduced, unrelated one (an unknown `properties` field after
  flattening, instead of the mount-validator rule a test actually meant
  to exercise). Hit in three different forms during this change: a
  YAML-colon-style fixture search missing Python dict-bracket-syntax
  fixtures, a literal dotted-token consumer-grep missing a two-step
  attribute access, and a plain leftover fixture only caught by
  re-sweeping before the next phase started.
- Found, but deliberately left untouched as a separate concern: while
  verifying a citation during design review, `tenant.spec.custom` turned
  out to be consumed twice at build time — once directly into
  `strata.tenant.custom`, and again merged as a base layer into the
  deployment's own `custom` — undocumented anywhere as intentional. Not
  part of this change; needs its own follow-up.
