# Resource Model — v2 Design Decisions

- Status: partially-implemented — model and service ported; ADR-0001's
  `subcategory` discrepancy intentionally left unresolved (see Remaining Work)
- Date: 2026-09-20
- Revised: 2026-09-21 — removed `ResourceReferencesModel`/`spec.references`
  per [ADR-0002](0002-requirement-interface-injection-grant-lessons-from-v1.md)'s
  conclusion that Requirement should not exist as a schema field (same
  removal already applied to `ProviderModel` — see ADR-0003 Decision 6).
- Related: [ADR-0001](0001-v1-schema-analysis-findings-for-v2.md) (v1 schema
  analysis — flags `subcategory` as Resource-only), [ADR-0002](0002-requirement-interface-injection-grant-lessons-from-v1.md)
  (references/injection lessons), [ADR-0003](0003-provider-model-design-decisions.md)
  (Provider model decisions — same `properties`/`configuration`/`custom` pattern)

## Context and Problem Statement

`ResourceModel` (`src/strata/models/resource_model.py`,
`src/strata/services/resource_service.py`) is the second v2 kind, ported from
v1 field-by-field the same way `ProviderModel` was. This ADR records the
decisions made porting it, and confirms it can be built standalone: v1's
`resource_model.py` imports nothing from other kind models (`provider_model.py`,
`network_model.py`, etc.) — relationships to other kinds are expressed as
name-string references (`provider_type`) or capability declarations
(`ResourceDependencyModel.category`/`subcategory`), resolved later at the
service/build layer, not as embedded Pydantic models.

## Decisions

### 1. Ported (near-)verbatim

`ResourceDependencyModel`, `ResourceVolumesModel`, `ResourceDiskModel`,
`ResourceStorageModel`, `ResourcePropertiesModel`, `ResourceReferencesModel`,
`ResourceSpecModel`, `ResourceMetaModel`, `ResourceModel` — all ported with the
same v2-wide mechanical changes as Provider (built-in generics, `X | None`,
`apiVersion` → v2). No structural changes; v1's disk/volume/mount-path/label
validators are copied as-is (they're pure Phase-1 string/structural
validation, no external dependency).

### 2. Dropped a redundant validator

v1's `ResourceSpecModel.validate_configuration_schema()` only checked
`isinstance(self.configuration, dict)` — redundant, since Pydantic already
enforces `configuration: dict[str, Any] | None` at the type level. Not ported;
no behavior change (Pydantic already rejects a non-dict value with a clearer
error).

### 3. `configuration` confirms the Provider `configuration`/`custom` split (ADR-0003)

Unlike v1's `ProviderModel` (which only had `properties`/`custom`), v1's
`ResourceModel` **already has** a third `configuration: dict[str, Any]` field —
and, unlike Provider's dead `custom`, Resource's `configuration` **is** wired
through: `ResourceService._validate_dynamic()` cross-checks it against a
schema declared in `ConfigurationModel.spec.providers[type].resources[resource_type].configuration`
(regex-pattern-per-field, required/optional, `additional_configurations` gate).
This confirms the three-way `properties`/`configuration`/`custom` split adopted
for Provider in ADR-0003 matches a real, working v1 precedent — Resource is the
kind that pattern was modeled on.

### 4. Phase 2 dynamic validation ported to `ResourceService`

`_validate_dynamic()` ported from v1's `ResourceService`, cross-checking
(when a `ConfigurationModel` is supplied):
- `provider_type` exists in `configuration.spec.providers`
- `resource_type` exists in that provider's `resources` list (unless
  `additional_resources: true`)
- Every `spec.configuration` field matches its declared pattern, respects
  `required`, and is rejected if undeclared and `additional_configurations` is
  false

This is the same registry (`ConfigurationProviderModel`) `ProviderService`
already cross-checks against — no new configuration concept was needed.

### 5. `ResourceReferencesModel`/`spec.references` removed (2026-09-21)

Initially ported verbatim from v1, identical shape to `ProviderReferencesModel`
(`variables`/`secrets`/`features` key-name lists) — confirmed as the exact
DRY duplication flagged when the same field was first questioned on
`ProviderModel`. Removed for the same reason recorded in
[ADR-0002](0002-requirement-interface-injection-grant-lessons-from-v1.md)
and [ADR-0003](0003-provider-model-design-decisions.md) Decision 6: scoping
is derivable (`Injection = Interface ∩ Environment`, once the provisioner/
build layer exists), and typo-catching for Value bindings is a direct Phase 2
check against `Environment`, not a hand-authored list.

## Consequences

- Good: Resource's real, working `configuration` field validates the
  `properties`/`configuration`/`custom` design established for Provider.
- Good: `ResourceService`'s Phase 2 validation reuses `ConfigurationModel`
  with no changes needed to that model.
- Good: Resource has zero coupling to any other kind (not even by deferred
  reference) — it is fully standalone, matching v1's own module boundaries.
- Good: `spec.references` is gone — same benefit recorded for Provider
  (ADR-0003): one less hand-maintained field to drift out of sync.

## Remaining Work

- `ResourcePropertiesModel.subcategory` remains unresolved (ADR-0001
  discrepancy 5) — tracked centrally in
  [docs/design/v1-schema-parity-tracking.md](../design/v1-schema-parity-tracking.md),
  not duplicated here.
