# Provider Model — v2 Design Decisions

- Status: implemented
- Date: 2026-09-20
- Revised: 2026-09-21 — removed `ProviderReferencesModel`/`spec.references`
  per [ADR-0002](0002-requirement-interface-injection-grant-lessons-from-v1.md)'s
  conclusion that Requirement should not exist as a schema field (scoping is
  derivable via `Interface ∩ Environment`; typo-catching is a direct Phase 2
  check against `Environment`). See Decision 6 below, updated accordingly.
- Revised: 2026-09-22 — reviewed `ProviderPropertiesModel` against what real
  cloud provisioners (AWS/azurerm/google/kamatera) actually need at the
  provider-instance level. Dropped `version` (duplicated `ProviderConfig.spec.version`,
  a type-level concept), renamed `location`→`display_name` (was ambiguous
  against `region`), and moved the "group of regions sharing a compliance/
  deployment boundary" concept to an optional `geography` tag on
  `ProviderConfig.spec.regions` entries rather than a new per-instance field.
  See Decision 7 below.
- Revised: 2026-09-22 — replaced the loose `regions: list[str | dict[str, Any]]`
  shape with a real `ProviderConfigRegionModel` (`name` + optional `geography`/
  `description`), and corrected Decision 7's naming call: a real production
  strata v1 config repo (`cfg-int-deployment`) already uses **`zone`**, not
  `geography`, for exactly this "group of regions sharing a deployment/data-
  residency boundary" concept (`config/zones.yaml`, `Tenant.spec.zones`) —
  the DNS-zone collision flagged in Decision 7 hasn't been a problem in
  practice there. `geography` is kept as the *field name* for now (matches
  Azure's own vocabulary and avoids a second, different meaning of "zone"
  inside `ProviderConfig` specifically), but future work introducing a v2
  `Zone`/tenant-boundary kind should reuse `region.geography` as its region
  membership source rather than inventing a separate mapping. See Decision 8.
- Related: [ADR-0001](0001-v1-schema-analysis-findings-for-v2.md) (v1 schema
  analysis), [ADR-0002](0002-requirement-interface-injection-grant-lessons-from-v1.md)
  (references/injection lessons)

## Context and Problem Statement

`ProviderModel` (`src/strata/models/provider_model.py`, `auth_models.py`,
`common_models.py`) is the first v2 model, ported from v1 and then reviewed
field-by-field against v1's actual schema and real usage (production YAML
fixtures, service/builder code). This ADR records the decisions made during that
build, several of which deliberately diverge from a literal v1 port.

## Decisions

### 1. `apiVersion` bumped to `v2`

`PlatformVersion` now has `v2` / `v2_omp` (`strata.huybrechts.xyz/v2`), not `v1`.
Chosen over keeping `v1` because v2 is a from-scratch schema redesign informed by
[ADR-0001](0001-v1-schema-analysis-findings-for-v2.md)'s findings — existing v1
YAML documents are not expected to validate as-is against v2 models, so reusing
the `v1` apiVersion string would be misleading.

### 2. `PlatformKind` trimmed to only what's implemented

Unlike v1's full enum (18 kinds), v2's `PlatformKind` currently only has
`PROVIDER`. New members are added one at a time as each kind's model is actually
built, rather than pre-populating the full v1 list up front.

### 3. `AuthenticationModel` — dropped dead fields, added missing validation

- **Dropped** `env_vars: list[str]` and `env_var: str` (kept `description`).
  Verified by grep that no v1 code anywhere reads `.env_vars`/`.env_var`/
  `.description` on an `AuthenticationModel` — they were purely aspirational
  documentation fields with no runtime consumer.
- **Added** a `model_validator(mode="after")` (`validate_method_matches_populated_config`)
  requiring that the field matching `method` (e.g. `oauth2` when
  `method="oauth2"`) is populated, and rejecting any other method's field being
  set at the same time. v1 has no equivalent — `method` and the method-specific
  fields could silently disagree in v1's schema. This is a v2-only improvement,
  not a port.

### 4. `CommonLifecycleModel` — corrected to match v1's real design

An earlier v2 draft invented a fixed-fields lifecycle model
(`setup`/`validate`/`plan`/`apply`/`output`/`destroy`) without checking v1's
actual implementation. Once checked against v1's real `common_models.py` and a
production provider YAML fixture, the actual design is an **open, arbitrary-keyed
map**, not a fixed set of phases:

```python
class ScriptPathModel(PlatformBaseModel):
    file: str                # extension-validated: .sh .bash .py .ps1 .js .mjs .go
    scope: PlatformKind
    priority: int = 100      # 0-9999, lower runs first
    target: str | None
    description: str | None

class ScriptsModel(PlatformBaseModel):
    description: str | None
    scripts: list[str | ScriptPathModel] | None

class CommonLifecyclePhaseModel(ScriptsModel): ...

class CommonLifecycleModel(RootModel[dict[str, CommonLifecyclePhaseModel]]):
    """Phase names follow pattern: {command}_{action}_{suffix}"""
```

Real phase names observed in v1 production data: `deploy_check`,
`deploy_plan_before`, `deploy_provision`, `deploy_initialize`,
`deploy_configure`, `deploy_output`, `deploy_health`, `deploy_destroy_before`,
`deploy_destroy_after`. v2 ported this design (including `SCRIPT_EXTENSIONS`)
verbatim rather than the invented fixed-fields version.

### 5. `properties` / `custom` / `configuration` — three-way split

v1 only had `properties` (strata-validated: `type`, `region`, `location`,
`organization`, `version`) and `custom` (`dict[str, Any]`, intended for
automation metadata). Checking v1's actual build pipeline
(`PlatformProviderModel.from_provider_model()` in `platform_artifact_model.py`)
found that **`custom` and `default_tags` are validated on parse but then silently
dropped** — never copied into the platform artifact, never reaching any builder,
despite a real production fixture populating `custom` with `costcenter`/
`billing_account`/`support_tier`. Same class of defect as the dead
`env_vars`/`env_var` fields (Decision 3).

v1 also has **no field at all** for raw, provisioner-specific passthrough
configuration (e.g. a Terraform provider block's `skip_provider_registration`,
`partner_id`) — `properties` is schema-validated and narrow by design, so there
was nowhere for such values to go.

v2 adds a third field, `configuration: dict[str, Any] | None`, for exactly this
purpose, distinct from the other two:

| Field | Validated by strata? | Purpose | Consumer |
|---|---|---|---|
| `properties` | Yes (`ProviderPropertiesModel`) | Common, cross-provisioner fields (type, region, …) | builders (once built) |
| `configuration` | No — raw passthrough | Provisioner-specific extras not worth modeling | builders (once built) — **must be explicitly wired**, not automatic |
| `custom` | No — free-form | Automation/bookkeeping metadata (cost center, etc.) | external tooling/docs only — **not** guaranteed to reach any builder |

`custom` is kept (not dropped) per decision, but its docstring now explicitly
warns that it is inert until a builder/service layer explicitly consumes it —
learning from v1 where this was never done despite the field looking functional.

### 6. `ProviderReferencesModel`/`spec.references` removed (2026-09-21)

Initially ported verbatim from v1 (`variables`/`secrets`/`features` key-name
lists). Revisited in [ADR-0002](0002-requirement-interface-injection-grant-lessons-from-v1.md)
after working through the "Requirement" concept in isolation: both of its v1
jobs turned out to be better solved without a schema field at all — scoping
is derivable (`Injection = Interface ∩ Environment`, once the provisioner/
build layer exists), and typo-catching for Value bindings is a direct Phase 2
cross-check against `Environment`, not an internal-consistency check against
a hand-authored list. Removed `ProviderReferencesModel` entirely and the
`references` field from `ProviderSpecModel`. See ADR-0002 for the full
reasoning; not re-derived here.

### 7. `ProviderPropertiesModel` naming pass — `display_name`, dropped `version`, `geography` tag (2026-09-22)

Revisited `type`/`region`/`location`/`organization`/`version` against what
AWS/azurerm/google/kamatera actually take as provider-instance config, and
against a request to model "a group of regions we must not deploy across"
(a compliance/data-residency boundary, e.g. Azure's real "geography" concept
— a market grouping multiple regions — or AWS's `aws`/`aws-cn`/`aws-us-gov`
partitions).

- **`location` renamed to `display_name`.** It was never anything but a
  human-readable label (docstring already said "documentation... not
  required for validation"), but naming it `location` made it read as a
  second geographic concept alongside `region`, which it never was.
- **`version` removed.** It duplicated `ProviderConfigSpecModel.version`.
  A provisioner version constraint (Terraform's `required_providers`) is
  inherently a **type-level** constraint — one provider source can't be
  pinned to two different versions across instances in the same plan — so
  having it on the per-instance `Provider` document too was a dead field
  with no real override use case (unused by any test or consumer) and a
  "which one wins" ambiguity if it were ever populated differently on both.
  `ProviderConfig.spec.version` is now the sole source of truth.
- **No new `geography`/`zone` field added to `ProviderPropertiesModel`.**
  "Zone" was rejected as a name — it already means two other things in this
  schema (`DnsZoneModel`, and v1's unported `ConfigurationModel.zones`).
  "Geography" (Azure's real term for this exact concept) was chosen instead,
  but implemented as an **optional tag on `ProviderConfigSpecModel.regions`
  entries**, not a new per-instance `Provider` field — e.g.
  `regions: [{name: eu-west-1, geography: europe}, ...]`. This is already
  representable today with zero schema change, since `regions` entries are
  `Union[str, dict[str, Any]]`. Geography membership is a property of the
  *region* (declared once, in the type registry), not of each `Provider`
  instance — duplicating it onto every `Provider` document risked the same
  "validates fine, silently wrong" drift this project's ADRs have repeatedly
  flagged elsewhere (`AuthenticationModel.method`, ADR-0071's
  Provisioner `backend`/`properties`). Any future "don't span >1 geography"
  check derives geography per-provider by looking its `region` up in
  `ProviderConfig.spec.regions`, rather than trusting a hand-declared field.

### 8. `ProviderConfigSpecModel.regions` — `ProviderConfigRegionModel` instead of a loose dict (2026-09-22)

Decision 7 proposed the `geography` tag as `{name: eu-west-1, geography: europe}`
representable via the existing `Union[str, dict[str, Any]]` shape with zero
schema change. In practice an untyped `dict[str, Any]` gives no validation of
`name`'s presence/format and no discoverability (a schema consumer/IDE can't
see `geography` exists at all). Replaced with a real
`ProviderConfigRegionModel(PlatformBaseModel)`:

```python
class ProviderConfigRegionModel(PlatformBaseModel):
    name: PlatformName
    geography: str | None = None
    description: str | None = None
```

`regions` is now `list[Union[str, ProviderConfigRegionModel]] | None` — a bare
name string is still accepted for the common case where no geography grouping
is needed (matches every existing test fixture), and the structured form is
used only once a region needs a `geography` tag. The uniqueness validator
and `ProviderService.validate_against_provider_config()` were updated to read
`region.name` off the model instead of dict-`.get()`.

## Consequences

- Good: `AuthenticationModel` can no longer represent an internally
  contradictory state (`method` disagreeing with which config is populated).
- Good: `CommonLifecycleModel` matches real-world v1 YAML shape, so v1 lifecycle
  data is representable in v2 without transformation.
- Good: `configuration` gives provisioner-specific escape-hatch values a home
  that isn't accidentally schema-validated or accidentally dead.
- Bad / risk: `configuration` and `custom` are both inert until v2 has a
  builder/service layer — must remember to actually wire them through when that
  layer is built, or `custom` will repeat its v1 fate.
- Good: `spec.references` is gone — one less field to keep in sync with the
  environment/Terraform interface by hand, and one less place a
  Value-binding typo could go undetected against the wrong source of truth.
- Good: `display_name`/`region`/`geography` no longer overload each other —
  `region` is the validated identifier, `geography` is its derived boundary
  group (from `ProviderConfig`, not duplicated per-instance), `display_name`
  is purely cosmetic.
- Good: one less duplicated source of truth — `version` now lives only on
  `ProviderConfig.spec.version`.
- Good: `ProviderConfigRegionModel` gives `geography` real schema validation
  and discoverability instead of an untyped passthrough dict.
- Neutral / follow-up: the actual "reject a Topology/deployment that spans
  more than one geography" check is not implemented yet — this ADR only
  establishes where the `geography` tag lives and that it's derived, not the
  validator that consumes it.

## Remaining Work

None for the current fields — this ADR is `implemented` for what exists today
(the Provider/Auth models). Follow-up, when the builder/service layer is
designed: confirm `configuration` and `custom` are each explicitly consumed (or
consciously left inert with that decision recorded), rather than silently
dropped the way v1's `custom` was. Also follow-up: build the actual
cross-geography boundary check once a Topology/deployment-planning layer
exists (Decision 7) — today `geography` is documented convention on
`ProviderConfig.spec.regions` only, with no enforcement yet.
