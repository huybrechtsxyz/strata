# Retire `providerconfig`'s embedded configuration-schema sub-feature

- Status: accepted
- Date: 2026-10-15
- Related: [ADR-0040](0040-attributeconfig-standalone-kind.md) (the
  replacement this retirement depends on), [ADR-0014](0014-provider-topology-config-standalone-kinds.md)
  (origin of the sub-feature this retires), [docs/design/dynamic-configuration-validation.md](../design/dynamic-configuration-validation.md)

## Context and Problem Statement

`providerconfig.spec.resources[].configuration` (plus
`ProviderConfigSchemaField` and `.additional_configurations`) was a narrow,
bespoke shape check scoped to exactly one blob: `resource.spec.
configuration`, for one `(provider_type, resource_type)` pair at a time — a
regex pattern and a required flag per field, with an allow/deny switch for
unlisted fields. [ADR-0040](0040-attributeconfig-standalone-kind.md)
introduces `attributeconfig`, a standalone kind that covers this exact
concern — and every other passthrough blob on every other kind — with
strictly more power (real JSON Schema types, nesting, cross-field
`if`/`then`). Once that exists, keeping `providerconfig`'s own embedded
version too means a resource-configuration schema has two unrelated places
it could be declared, with two separate diagnostic vocabularies, for the
exact same concern.

## Considered Options

- **Keep both indefinitely**, as two parallel front-ends for the same
  concern. Rejected — pure overlap, with no capability
  `providerconfig`'s version has that `attributeconfig` lacks; itself a
  smaller-scale repeat of the exact embedded-registry anti-pattern
  [ADR-0011](0011-topology-and-provisioning-decoupling.md)/ADR-0014
  already argued against one layer up (an embedded list inside a shared
  document, instead of its own standalone, independently-reviewable
  document).
- **Keep `providerconfig`'s sub-feature as the only mechanism**, and don't
  let `attributeconfig` cover the `configuration` block at all. Rejected
  — `attributeconfig`'s whole value (a standard schema language, push
  enforcement, cross-field checks) is why this retirement is possible;
  narrowing it back out would just reintroduce the capability gap
  ADR-0040 exists to close, for no benefit.
- **Retire `providerconfig`'s embedded schema sub-feature outright**,
  leaving its resource-type/region name-registry role (a different,
  non-overlapping concern) untouched. Chosen.

## Decision Outcome

Chosen: delete `ProviderConfigSchemaField`,
`ProviderConfigResourceModel.configuration`/`.additional_configurations`,
and `ResourceService._validate_configuration_schema()` outright — along
with its four diagnostic codes (`configuration_field_not_allowed`,
`configuration_pattern_mismatch`, `configuration_field_missing`,
`invalid_schema_pattern`). No deprecation window: strata-v2 is pre-release
(`2.0.0a23`), nothing shipped stable depends on `providerconfig`'s schema
shape, and a repo-wide grep confirmed exactly one real `providerconfig`
document exists anywhere (this repo's own dogfooding fixture), with no
other declared `configuration:` schema to carry forward.

A resource-configuration schema is now an ordinary `attributeconfig`
document, keyed by a `{spec.provider_type, spec.resource_type}` selector —
exactly the lookup `providerconfig`'s old mechanism used internally, with
no capability lost. `providerconfig`'s resource-type/region name-registry
role (`ProviderConfigResourceModel.name`, `additional_regions`/
`additional_resources`) is untouched and stays permanently — a genuinely
different, non-overlapping concern (an enum/allow-list check on a
*structured* field, never in `attributeconfig`'s own `properties`/
`custom`/`configuration`-blob scope).

## Consequences

- Good: one single front-end, and one diagnostic vocabulary, for every
  passthrough-blob shape concern — not two unrelated ones for the same
  kind of problem.
- Good: this repo's own real `providerconfig` document
  (`config/registries/azure.yaml`) shrank to a bare name registry; the
  retired schemas moved to a parallel `attributeconfig` file that states
  up front what it checks and against which documents, rather than being
  buried inside a provider type's registry entry.
- Bad: a one-time migration cost for anyone who had already authored a
  `providerconfig.spec.resources[].configuration` schema — confirmed via
  grep that this repo itself had exactly one such case, already migrated
  as part of this change.
- Neutral: deleting outright (rather than a deprecation window) was only
  possible because of this repository's explicit, confirmed "backward
  compatibility does not matter pre-1.0" policy — a project with real
  external consumers depending on the old shape would need a migration
  path instead.
