# Provider Model — v2 Design Decisions

- Status: implemented
- Date: 2026-09-20
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

## Remaining Work

None for the current fields — this ADR is `implemented` for what exists today
(the Provider/Auth models). Follow-up, when the builder/service layer is
designed: confirm `configuration` and `custom` are each explicitly consumed (or
consciously left inert with that decision recorded), rather than silently
dropped the way v1's `custom` was.
