# Flatten Provider/Resource/Module's Typed `properties` Onto `spec`

- Status: accepted
- Date: 2026-10-13
- Related: [ADR-0002](0002-requirement-interface-injection-grant-lessons-from-v1.md)
  (establishes "Value binding"/`${var:}` as this codebase's own vocabulary —
  why a renamed wrapper couldn't be called `values`), [ADR-0003](0003-provider-model-design-decisions.md)
  (Decision 5 — introduced the `properties`/`configuration`/`custom`
  three-way split this ADR does not reverse, only refines the shape of),
  [ADR-0004](0004-resource-model-design-decisions.md) (adopted the same
  split for Resource), [ADR-0009](0009-module-model-design-decisions.md)
  (adopted it for Module), [ADR-0017](0017-tags-labels-configuration-custom-placement.md)
  (the table decision that `properties` is "typed model, strata-validated"
  — still true after this change, only *where* those typed fields live
  changes), [flatten-provider-resource-module-properties.md](../design/flatten-provider-resource-module-properties.md)
  (the work doc this decision was designed in, with its full field-by-field
  evidence and phased Implementation Plan)

## Context and Problem Statement

`properties` means two unrelated things depending on which kind is being
read. On Workspace/Environment/Deployment/Tenant it is a free-form
`dict[str, Any]` merged across documents (the deployment-composition
mechanism). On Provider/Resource/Module it is instead a typed, nested
sub-model (`ProviderPropertiesModel`/`ResourcePropertiesModel`/
`ModulePropertiesModel`) holding each kind's own core, defining attributes
(`type`/`region` for Provider; `provider_type`/`resource_type` for
Resource — the field Terraform-payload building groups resources by;
`mounts`/`checks`/`endpoints` for Module) — not free-form, not merged, and
not the same concept as the first group at all.

Checked against every other kind before treating this as "just how v2
works": 8 kinds that aren't part of the merge chain (DNS, Network,
Firewall, Namespace, Workspace's own non-merge fields, Integration, plus
Environment/Deployment/Tenant's non-`properties` fields) already put their
own core fields directly on `spec`, with no wrapper at all. Provider,
Resource, and Module are the only 3 kinds that wrap their core fields
under a sub-model — and that sub-model happens to reuse the word
`properties`, which is also the unrelated merge-dict's name on four other
kinds. This is the minority pattern creating the naming collision, not an
established convention being changed on a whim.

## Considered Options

- **Rename the wrapper, keep the nesting** (`spec.definition.type`/
  `spec.attributes.type`). Removes the naming collision without changing
  structure. `values` was considered and rejected outright — it collides
  with this codebase's own established "Value binding" vocabulary
  (ADR-0002: `${var:}`/`${secret:}`/`${feature:}`, `ValueResolution`,
  `resolve_values()`) — reusing that word for an unrelated concept
  reintroduces the exact class of confusion this decision exists to
  remove, just under a new name. `definition`/`attributes` would have
  worked (both unused elsewhere in the schema) but still add a nesting
  level that 8 other kinds don't have, for no functional reason once
  flattening is on the table.
- **Universal `spec.attributes` wrapper, applied to every kind** —
  retrofit DNS/Network/Firewall/etc. to match, for maximum uniformity.
  Rejected: touches every kind's schema and every existing document for
  kinds that have zero naming problem today, for a uniformity payoff
  only. DNS/Network/Firewall's own core fields are themselves lists of
  structured sub-models (`zones`, `networks`, `deny`/`allow`), not flat
  scalars — wrapping e.g. `NetworkSpecModel.networks` as
  `NetworkSpecModel.attributes.networks` adds a layer without clarifying
  anything, since there's only ever the one field under it.
- **Flatten only the 3 outliers onto their own `spec`, matching the other
  8 kinds' existing convention; do not re-add a free-form `properties`
  afterward** (chosen).

## Decision Outcome

Chosen: **flatten, no wrapper, for anyone.**

- `ProviderPropertiesModel`'s `type`/`region`/`display_name` move directly
  onto `ProviderSpecModel`.
- `ResourcePropertiesModel`'s `provider_type`/`resource_type`/`unit_cost`/
  `category`/`subcategory` move directly onto `ResourceSpecModel`.
- `ModulePropertiesModel`'s `mounts`/`checks`/`endpoints` move directly
  onto `ModuleSpecModel`.
- None of the three gets a replacement free-form `properties` field back.
  `configuration` + `custom` (already present on all three, already
  plain `dict[str, Any] | None`) already cover the "passthrough data with
  no further strata validation" need — a new `properties` would be
  functionally identical to `custom` on these non-merging kinds, the
  exact redundancy already used to deny `properties` to Namespace/DNS/
  Network/Firewall/Integration. After this change, `properties` exists
  **only** on the four kinds that actually merge it (Workspace/
  Environment/Deployment/Tenant) — one exception-free rule, not three
  special cases.

**Module is included despite weaker supporting evidence than Provider/
Resource, and that asymmetry is recorded here rather than left implicit.**
Provider's `type`/`region` and Resource's `provider_type`/`resource_type`
are required fields, directly cross-checked against a real
`ProviderConfig` registry document elsewhere in the codebase (service-
layer Phase 2 checks). Module's `properties` is optional, and its
children (`mounts`/`checks`/`endpoints`) have no *cross-document*
consumer — no registry lookup, no Phase 2 cross-check against another
kind. **Correction (2026-10-13, caught during implementation): they are
not entirely unconsumed** — `terraform_projection.py`'s `_strata_modules()`
does project them into the Terraform-facing `strata.modules` payload; the
original design-time grep (`module.spec.properties.(mounts|checks|
endpoints)` as one literal token) missed this because the real code reads
`spec.properties` into a local first, then `.mounts`/`.checks`/`.endpoints`
on separate lines — a narrower grep than Provider/Resource's actual
consumers needed. The corrected comparison still holds: that's a single,
same-document passthrough projection, not a cross-document registry
check the way Provider/Resource's `type`/`provider_type` get — the
asymmetry is real, just smaller than first measured. Module is flattened
anyway because: (a) it is still one of the 3 outliers against 8 already-flat
kinds, so leaving it nested while Provider/Resource flatten recreates the
inconsistency one kind later; (b) flattening surfaces a genuine,
independently-found `type` field-name situation — Module already has its own
unrelated top-level `type` (deployer tool: helm/compose/argocd) distinct from
Provider's `type` (cloud provider) — confirmed to be a non-issue since
they are different models entirely, not assumed safe by analogy; (c)
nothing found argues the conclusion is wrong, only that the supporting
evidence is thinner — not a reason to treat Module differently from
Provider/Resource, just a reason to not claim identical justification.

## Consequences

- **Real, breaking schema change.** Every existing document using the
  nested `spec.properties.type`/`spec.properties.provider_type`/etc. path
  must be rewritten to the flattened path — this repo's own `config/`
  dogfooding solution included, and any real consumer repo.
- **Non-trivial source-code migration, not just document rewriting.**
  Confirmed real consumers of the nested path beyond the 3 model files:
  `provider_service.py`, `resource_service.py`, `semantic_checks.py`,
  `provider_config_model.py`'s docstring, `terraform_projection.py`, plus
  several `docs/config/*.md` reference docs and model/service/integration
  tests. All tracked phase-by-phase in
  [flatten-provider-resource-module-properties.md](../design/flatten-provider-resource-module-properties.md)'s
  Implementation Plan.
- **No change to the Terraform-facing output shape.** The payload
  builders in `terraform_projection.py` already read
  `spec.properties.type`/`.region`/etc. and produce an output dict that's
  already flat — flattening only moves where the *source* field lives on
  the Pydantic model, not the category/shape/`TF_VAR_` name downstream.
- **`properties` becomes an exception-free rule going forward**: it
  exists only on Workspace/Environment/Deployment/Tenant (the merge-chain
  kinds); every other kind gets `configuration` + `custom` and nothing
  else, with no special case to remember per kind.
