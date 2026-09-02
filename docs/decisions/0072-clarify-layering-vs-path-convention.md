# Clarifying `spec.layers`, `spec.layering(s)`, and `spec.paths`-Overlap, Confusion, and the Missing Link

- Status: partially-implemented — schema merge, resolution, and migration complete; agreement-enforcement policy not started
- Date: 2026-09-01
- Related: [ADR 0042-Deep Validation and Layer Consistency](./0042-deep-validation-layer-consistency.md), [ADR 0052-Path Convention Validation](./0052-path-convention-validation.md)

## Context and Problem Statement

Strata ships three separate mechanisms that all talk about "the position of a
deployment file in a directory hierarchy," and they are easy to conflate because
their YAML shapes look similar (`scope` + a hierarchy of names). In practice they
solve three different problems, use two different matching engines, and-critically
— none of them derive one from another the way a reasonable reader would expect.

This ADR exists to record, in one place:

1. What each of the three mechanisms actually does today (with code references).
2. A concrete real-world configuration that demonstrates the confusion.
3. What is *missing*-the gap between what a DevOps author reasonably expects and
   what the system currently provides.

No code changes are proposed to be *decided* here yet-see
[Considered Options](#considered-options) and
[Remaining Work](#remaining-work).

### The three mechanisms

|                                                  | Lives on               | Type             | Purpose                                                                                                 | Match engine                                                        | Precedence                                   |
| ------------------------------------------------ | ---------------------- | ---------------- | ------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------- | -------------------------------------------- |
| `deployment.spec.layers`                         | **deployment** file    | `Dict[str, str]` | The actual hand-authored **values** for a hierarchy (e.g. `{hub: "hub1", ring: "prd"}`)                 | none-plain dict                                                     | n/a                                          |
| `configuration.spec.layering` / `spec.layerings` | **configuration** file | schema + scope   | Validates `spec.layers` values (required/pattern/default) and builds the **build/artifact output path** | `fnmatch` on `scope`, **first match wins**                          | exactly one scheme selected                  |
| `configuration.spec.paths`                       | **configuration** file | convention rules | Validates the **on-disk repository layout** (and can optionally drive tenant-file resolution)           | `fnmatch` on `scope` (pre-filter) + true segment-match on `pattern` | **all matching conventions apply**-no winner |

### How `spec.layers` works

[`DeploymentSpecModel.layers`](../../src/strata/models/deployment_model.py#L420) is a
plain `Optional[Dict[str, str]]`. It carries no matching logic of its own-it is
simply read by the layering/artifact-path code described below. There is no
mechanism anywhere that populates this dict automatically; every deployment file
must declare its own layer values by hand, e.g.:

```yaml
spec:
  layers:
    hub: hub1
    spoke: spoke1
    customer: cust1
    ring: prd
    environment: eu1
```

### How `spec.layering` / `spec.layerings` works

- [`ConfigurationLayerModel`](../../src/strata/models/configuration_model.py#L180) defines
  a single layer's schema: `name`, `required`, `pattern`, `default`.
- [`ScopedLayeringModel`](../../src/strata/models/configuration_model.py#L206) wraps an
  ordered list of those layers behind a `scope` glob, so different subtrees of the
  repository can use different layer schemas.
- [`resolve_layering_scheme()`](../../src/strata/utils/layering.py#L20) matches the
  **deployment file's own relative path** against every scheme's `scope` in
  declaration order using plain `fnmatch.fnmatch()`, and returns the **first** match.
  This is a `fnmatch` string match-`*` and `**` are not path-segment-aware; both are
  ordinary shell-glob wildcards that can cross `/` freely.
- Once a scheme is selected, [`DeploymentService._validate_deployment_layers()`](../../src/strata/services/deployment_service.py#L377)
  validates `deployment.spec.layers` values against that scheme's `required`/`pattern`/
  `default` rules-it never reads or parses the deployment file's path for values.
- [`DeploymentService.get_artifact_path()`](../../src/strata/services/deployment_service.py#L540)
  and [`compute_artifact_path()`](../../src/strata/utils/layering.py#L54) build the
  build-output path by joining `spec.layers` values (in schema order) with `/` —
  again, purely from the hand-authored dict, never from the file's own location.

### How `spec.paths` works

- [`PathConventionModel`](../../src/strata/models/configuration_model.py#L256) declares
  a `scope` glob (candidate pre-filter, `fnmatch`) plus a `pattern` with `{segment}`
  placeholders.
- [`match_pattern()`](../../src/strata/utils/path_convention.py#L27) is **genuinely
  path-segment-aware**: it splits both the pattern and the actual path into real path
  parts and matches them positionally-each `{segment}` captures exactly one part,
  never crossing `/`. This is the correct tool for "does this file live where the
  hierarchy says it should," and it really does extract values (`captures: Dict[str,
  str]`) from the path.
- However, `pattern` matching is a **prefix** match, not an exact-depth match —
  "trailing path parts after the pattern are ignored," so a shallower pattern still
  matches deeper files (see `match_pattern()` docstring). Depth precision therefore
  still depends on how specifically each `pattern`/`scope` is written.
- [`evaluate_conventions()`](../../src/strata/utils/path_convention.py#L156) evaluates
  **every** convention whose `scope` and `pattern` both match-there is no
  first-match-wins precedence here, unlike `layerings`. Captured segment values are
  only used for that convention's own `rules:` (membership against
  `spec.<field>[*].<attr>`, or file-existence checks)-they are **not** written back
  anywhere else.
- The one exception: [`resolves: tenant`](../../src/strata/models/configuration_model.py#L300)
  lets a single, specially-flagged convention's `{code}` capture drive **tenant file
  resolution** (`resolve_tenant_file_path()`), proving the capture mechanism *can* be
  wired into real behavior elsewhere in the system-it just hasn't been generalized
  beyond that one case.

## Where the confusion comes from-a concrete example

A configuration author wrote the following, modeling a hub → spoke → customer → ring →
environment hierarchy as nested `layerings` schemes, ordered shallowest-scope-first:

```yaml
layerings:
  - name: landscape-scheme
    scope: "deploy/landscape/**"
    layers: [...]
  - name: hub-environment-scheme    # intended: 5 segments deep
    scope: "deploy/*/*/*/*/**"
    layers: [...]
  - name: hub-ring-scheme           # intended: 4 segments deep
    scope: "deploy/*/*/*/**"
    layers: [...]
  - name: hub-customer-scheme       # intended: 3 segments deep
    scope: "deploy/*/*/**"
    layers: [...]
  - name: hub-spoke-scheme          # intended: 2 segments deep
    scope: "deploy/*/**"
    layers: [...]
  - name: hub-scheme                # intended: 1 segment deep
    scope: "deploy/**"
    layers: [...]
```

The author's mental model was "each `*` reserves exactly one path segment, `**`
matches the rest, so each scope only matches files at its intended depth." That
mental model is correct for `spec.paths.pattern` (segment-aware), but **not** for
`spec.layerings.scope` (plain `fnmatch`). Verified directly against the actual
matcher:

```
PATH: deploy/hub1/spoke1/customer1/ring1/deploy.yaml   (intended: hub-ring-scheme, 4 deep)
  deploy/*/*/*/*/**   -> True   <- wrongly matches first (5-deep scheme)
  deploy/*/*/*/**     -> True   <- intended match, never reached
  ...
```

Because `resolve_layering_scheme()` takes the first match in declaration order, and
`fnmatch`'s `*`/`**` both happily cross `/`, every "deeper" scheme silently also
matches every shallower path, and the deepest-looking scheme wins regardless of the
file's actual depth (verified in the terminal session preceding this ADR-see the
`fnmatch` reproduction in this ADR's originating conversation).

This is not a bug in the sense of an implementation defect-`fnmatch` behaves exactly
as documented-but it **is** a design trap: `spec.layerings.scope` looks like it
should behave like `spec.paths.pattern`, and it does not.

## What is missing

1. **No path → `spec.layers` derivation, anywhere.** Even with a perfectly
   non-overlapping `layerings` configuration, an author must still hand-type every
   layer value into `deployment.spec.layers`, duplicating information already fully
   encoded in the file's own location.
2. **No drift detection between the two.** A file can live at
   `deploy/hub1/spoke1/cust1/prd/eu1/deploy.yaml` while declaring
   `spec.layers.hub: hub2` with zero validation error-the two representations are
   allowed to silently disagree.
3. **`spec.layerings.scope` is not segment-aware**, unlike `spec.paths.pattern`-this
   is the direct cause of the shadowing example above. Authors reasonably expect the
   two `scope`-shaped fields across these two mechanisms to behave the same way; they
   do not.
4. **`spec.paths.pattern` is a prefix match, not an exact-depth match.** Even the
   correct, segment-aware tool doesn't inherently guarantee "exactly N segments deep"
  -a shorter pattern still matches longer paths. Precise depth control still
   requires deliberate authoring (literal discriminating segments, or care with
   `scope`).
5. **The segment-capture mechanism that already exists (`spec.paths` + `match_pattern`)
   is not generalized.** `resolves: tenant` proves captured segments can drive real
   behavior; there is no equivalent for feeding captures into `deployment.spec.layers`
   or into `layerings`' artifact-path construction.

## Goal / Desired Outcome

Based on the discussion following this ADR's initial write-up, this is the direction
we are converging on — **goal and intent only, no design decided yet.** The mechanics
for getting here are still open; see [Considered Options](#considered-options) below.

1. **One way to config.** A single schema, declared once in configuration, describes
   the hierarchy (segment names, ordering, constraints) — not two dialects
   (`layering`/`layerings` and `paths`) that a reader has to reconcile by hand and
   keep in sync with each other.
2. **One way to deploy — with fallback.** `deployment.spec.layers` remains the one
   place a deployment states its values, but an explicit entry is no longer the only
   way to provide one: when a layer's value is omitted, it is derived from the
   deployment file's own location, using the segment captures the config-side schema
   already produces. An explicit `spec.layers` value always wins over a derived one.
3. **Policies that make it stick.** Declaring the schema is what turns the mechanism
   on at all — consistent with how every other policy in this codebase already
   behaves (`path_convention`, `checkov`, `opa`, etc. only apply if declared in
   `spec.policies`; no entry means no oversight, by design). Whether a derived value
   and an explicitly declared value are *required to agree* is a separate, opt-in
   dial, reusing the existing `enforcement: deny | warn | audit` convention rather
   than inventing a new toggle. No policy declared → no oversight at all, preserving
   today's behavior for anyone who doesn't want it.

## Impact Analysis — Where a Change Would Ripple

Purely descriptive — an inventory of every place that would need to change under
Option E (the chosen direction — see [Decision Outcome](#decision-outcome) and
[Unified Schema Design](#unified-schema-design)). This maps the blast radius so a
future implementer starts with the full picture instead of discovering pieces one
at a time.

### Models (schema)

- [`configuration_model.py`](../../src/strata/models/configuration_model.py) —
  `ConfigurationLayerModel` (L180), `ScopedLayeringModel` (L206),
  `PathConventionModel` (L256), `spec.layering` (L590), `spec.layerings` (L594),
  `spec.paths` (L601). Per [Unified Schema Design](#unified-schema-design),
  `PathConventionModel` gains an inline `segments: List[ConfigurationLayerModel]`
  field (full definitions, no shared catalog — one convention per hierarchy
  family), reusing `ConfigurationLayerModel`'s existing shape minus `required`
  (dropped — see
  [Decided: no existence-required segments](#decided-no-existence-required-segments--not-applicable-replaces-required)).
  Also gains a new `spec.custom: Optional[Dict[str, Any]]` field (generic escape
  hatch, same pattern as the existing `spec.configuration`/`spec.properties`) — see
  [Decided: add spec.custom and make resolve_spec_rule() dict-aware](#decided-add-speccustom-and-make-resolve_spec_rule-dict-aware).
- [`deployment_model.py`](../../src/strata/models/deployment_model.py#L420) —
  `spec.layers`; its own docstring literally says "keys must match
  `configuration.spec.layering[].name`", so the two models are already
  cross-referencing each other by convention, not by any enforced link. Per
  [Unified Schema Design](#unified-schema-design), `spec.layers` changes shape from
  a flat `Dict[str, str]` to a structured object with `follows: Optional[str]`
  (explicit, unambiguous reference to a `configuration.spec.paths` convention name,
  taking precedence over path-based auto-detection) and
  `segments: Optional[Dict[str, str]]` (the former flat dict, now nested). The
  field name itself is kept as `spec.layers` deliberately — see the naming note
  under [Unified Schema Design](#unified-schema-design) for why `spec.paths` and
  `spec.path` were both considered and rejected in favor of reusing this existing,
  already-familiar name.
- [`platform_artifact_model.py`](../../src/strata/models/platform_artifact_model.py#L653) —
  `PlatformSpecModel.deployment` (the **build-time serialized copy** of layer values)
  and [`.artifact_path`](../../src/strata/models/platform_artifact_model.py#L657) (the
  computed path) — this is the final output representation consumed downstream by
  sync/GitOps builders; its field descriptions also reference
  `configuration.spec.layering[].name` directly.

### Utils (matching / resolution engines)

- [`utils/layering.py`](../../src/strata/utils/layering.py) — `resolve_layering_scheme()`
  (`fnmatch`, first-match-wins), `compute_artifact_path()`.
- [`utils/path_convention.py`](../../src/strata/utils/path_convention.py) —
  `match_pattern()` (segment-aware), `evaluate_conventions()` (behavioral change
  required — see
  [`rules:` validates the resolved value](#rules-validates-the-resolved-value-not-just-the-path-capture):
  must check the Level 1 + Level 2 resolved value per segment, not only the raw
  `match_pattern()` capture, or explicitly-declared `spec.layers.segments` values
  and shallow deployments silently skip `rules:` validation entirely),
  `resolve_spec_rule()` (behavioral change required — dict-aware fallback at both
  the path-walk and final attribute-extraction steps, see
  [Decided: add spec.custom and make resolve_spec_rule() dict-aware](#decided-add-speccustom-and-make-resolve_spec_rule-dict-aware)),
  `evaluate_file_rule()`, `build_path_from_pattern()`,
  `find_tenant_path_pattern()`,
  `resolve_tenant_relative_path()`, `resolve_tenant_file_path()`.

### Services

- [`deployment_service.py`](../../src/strata/services/deployment_service.py) —
  `_validate_deployment_layers()` (L377), `get_artifact_path()` (L540), the
  `_validate_dynamic()` call site (L97-98), and the tenant-resolution call sites
  (L115-134) that already reuse the `spec.paths` capture mechanism via
  `resolves: tenant`.

### Controllers — duplicated logic found during this pass

- [`controllers/overlap_controller.py`](../../src/strata/controllers/overlap_controller.py#L145) —
  `_compute_artifact_path()`, explicitly documented in its own docstring as
  "Reproduce `DeploymentService.get_artifact_path()` from raw layer data." This is a
  **third independent reimplementation** of the same logic, reading raw YAML rather
  than the Pydantic model, used for fleet-wide artifact-path overlap detection. Any
  change to artifact-path semantics must be mirrored here too, or the overlap-checker
  silently diverges from what a real build would actually produce.
- [`controllers/promote_controller.py`](../../src/strata/controllers/promote_controller.py#L465) —
  `_scope_filter()` (L465) and
  [`_get_scope_selector()`](../../src/strata/controllers/promote_controller.py#L478)
  (L478) read `dep.spec.layers` directly to filter/select deployments for promotion.
  They don't call the layering-resolution utilities today, but their behavior would
  shift the moment previously-*absent* layer keys start being populated by
  derivation instead of staying absent.

### Builders

- [`builders/platform_builder.py`](../../src/strata/builders/platform_builder.py) —
  `get_artifact_path()` call site (L293-294), `convenience_layers` (L525),
  `deployment=deployment_model.spec.layers` (L575), and the `resolve_tenant_file_path()`
  call site (L489-491).

### Policy engine — only if Option C (new/extended policy type) is chosen

- [`validators/policies/policy_engine.py`](../../src/strata/validators/policies/policy_engine.py#L83) —
  builtin type registry.
- [`validators/policies/path_convention_policy.py`](../../src/strata/validators/policies/path_convention_policy.py) —
  existing pattern to mirror or extend.
- [`models/policy_model.py`](../../src/strata/models/policy_model.py#L27) — `type`
  field's description string, which enumerates valid policy type names.

### Tests

- `tests/strata/models/test_models_configuration.py` — `PathConventionModel` +
  `resolves` tests.
- `tests/strata/services/test_services_deployment.py` — `_validate_deployment_layers`,
  `get_artifact_path`, tenant resolution.
- `tests/strata/controllers/test_controllers_overlap.py` — the duplicated
  artifact-path logic in `overlap_controller.py`.
- `tests/strata/utils/test_utils_path_convention_tenant.py`
- `tests/strata/validators/test_path_convention_policy.py`
- `tests/strata/commands/test_commands_deploy.py` — `spec.layers` usage in manifest
  tests.

### Docs

- `docs/config/configuration.md` — "Layering — Artifact Path Hierarchies" and "Path
  Convention Policy" sections.
- `docs/GLOSSARY.md` — "Layering & Multi-Tenancy Concepts (ADR 0003)" section.
- [ADR 0042](./0042-deep-validation-layer-consistency.md) and
  [ADR 0052](./0052-path-convention-validation.md) — parent ADRs; would need a
  cross-reference (or "superseded by" note) once a follow-up decision is made here.
- [ADR 0041 — GitOps Controller Integration](./0041-gitops-controller-integration.md#L300) —
  references `spec.layers` resolved against configuration layering in a comparison
  table.
- [ADR 0012 — Rename Customer to Tenant](./0012-rename-customer-to-tenant.md) —
  historical references to the layering system's genericity; not functionally
  affected, but worth a cross-reference.

### Changelog

- `.github/CHANGELOG.md` / `.github/HISTORY.md` — no historical entries change; a new
  entry will be needed once an implementation lands.

### Explicitly NOT affected — a terminology collision worth flagging

"Layering" is also used, completely unrelated, for the environment/variable/secret
**composition merge order** (workspace → environment → deployment overlay precedence,
ADR 0024) — nothing to do with `spec.layering`/`spec.layerings`/`spec.paths`. These
mention "layering" in prose but are **not** part of this ADR's scope and must not be
touched by any follow-up work:

- `docs/config/deployment.md` (L136) — "environment files for variable/secret/feature
  data layering"
- `docs/guides/secrets-variables-features.md` — "Layering: Workspace → Environment →
  Deployment"
- `docs/guides/faq.md`, `docs/guides/deploying.md`, `docs/guides/multi-repo-setup.md`,
  `docs/guides/pattern-cross-env-changes.md` — same unrelated composition-order sense

This is the same category of naming collision this ADR already documents for the
`namespace`/`helm_namespaces`-style split found in prior work — worth being explicit
here so a future implementer doesn't waste time auditing the wrong "layering."

## Considered Options

- **A. Status quo-document only.** Keep `spec.layers` fully manual, keep
  `layerings` scope-selection `fnmatch`-based, keep `spec.paths` as the
  segment-aware (but prefix-matching) validation tool. Mitigate the confusion via
  documentation and by advising authors to order `layerings` schemes
  deepest/most-specific first, or to use distinguishing literal prefixes instead of
  relying on wildcard depth.
- **B. Generalize `resolves:` into a generic layer-capture mechanism.** Extend
  `PathConventionModel.resolves` beyond the single literal `"tenant"` value so a
  convention's captured segments can auto-populate matching-named entries in
  `deployment.spec.layers` (with any values explicitly declared in `spec.layers`
  still taking precedence as an override). This reuses the existing, proven
  `match_pattern()` capture path rather than inventing a new one.
- **C. Validation-only drift check (no auto-derivation).** Add a policy/validation
  rule that, when both a `layerings` scheme and a `spec.paths` convention match the
  same file and share segment names, the path-captured value must equal the declared
  `spec.layers` value-catching silent divergence without changing how values are
  supplied.
- **D. Make `spec.layerings.scope` segment-aware**, matching `spec.paths.pattern`'s
  semantics, so the two `scope`-shaped fields behave consistently across both
  mechanisms. Independent of B/C-addresses the *shadowing* confusion specifically,
  not the *duplication* gap.
- **E. Full merge — single unified schema, no backward compatibility.** Retire
  `spec.layering`/`spec.layerings` entirely and absorb their responsibilities into
  `spec.paths` (the mechanism with the correct, segment-aware matcher). One schema
  declares the hierarchy once: segment names, ordering, and per-segment constraints.
  `deployment.spec.layers` gains fallback derivation from that schema's path
  captures (subsuming B), and agreement enforcement between derived and explicit
  values becomes a policy dial on the same mechanism (subsuming C) rather than a
  bolt-on. `spec.layering`/`spec.layerings` are removed, not deprecated-and-kept —
  existing configurations using them will not continue to work unchanged and must
  be migrated by hand. This differs from every prior migration in this codebase
  (e.g. the `layering` → `layerings` migration in `docs/config/configuration.md`
  was additive and non-breaking); this one is explicitly not.

Options B, C, and D are not mutually exclusive and could be adopted together (B for
convenience, C as a safety net if B is deferred or only partially trusted, D to fix
the underlying matching-engine inconsistency regardless of which of B/C is chosen).
Option E supersedes A and D outright (there is only one schema left, so there is no
remaining `fnmatch` scope-selection step for D to fix), and subsumes B and C as
facets of the same unified mechanism rather than separate, independently-adoptable
options.

## Decision Outcome

Chosen: **Option E — full merge into a single unified schema, with no backward
compatibility.** `spec.layering`/`spec.layerings` and `spec.paths` are merged into
one mechanism; `spec.layers` becomes explicit-value-with-fallback as described in
[Goal / Desired Outcome](#goal--desired-outcome); agreement enforcement is a policy
dial on that same mechanism. Backward compatibility is explicitly out of scope —
users with existing `spec.layering`/`spec.layerings` configurations will need to
update them; there is no dual-support transition period like the earlier
`layering` → `layerings` migration.

The unified schema's design follows below.

## Unified Schema Design

`spec.layering`/`spec.layerings` are retired. `PathConventionModel` (`spec.paths`) is
extended to absorb their responsibility. Nothing changes about `scope` (fnmatch
pre-filter) or `rules`/`validate` (per-segment cross-checks) — both are unchanged
from today's `spec.paths`.

**Revision note:** an earlier draft of this section introduced a shared top-level
`spec.segments` catalog (segment definitions referenced by name from each
convention) and required exact-depth pattern matching with one convention per depth
tier. Both ideas are replaced below by a simpler model: each convention declares its
own full path layout inline, one convention per **hierarchy family** (not per
depth), and depth-flexibility moves to the deployment side instead.

### One convention per family — full layout declared inline, no shared catalog

Each `spec.paths` convention with `resolves: layers` is a complete, self-contained
declaration: its `pattern` (structural, segment-aware, as today) plus its own
`segments` list (inline `name`/`pattern`/`default` — no separate catalog, no
name-only references). A family's convention uses its **deepest** legitimate shape
as the pattern; shallower deployments within the same family are handled entirely
on the deploy side (see below), not by declaring more conventions.

The originating six-scheme example collapses to **two** conventions — one per
family, not one per depth:

```yaml
spec:
  paths:
    - name: landscape-scheme
      scope: "deploy/landscape/*"
      pattern: "deploy/landscape/{landscape}"
      resolves: layers
      segments:
        - name: landscape
          pattern: "^[a-z][a-z0-9-]*$"

    - name: hub-scheme
      scope: "deploy/hubs/*"
      pattern: "deploy/hubs/{hub}/{spoke}/{customer}/{ring}/{environment}"  # deepest shape for this family; literal "hubs/" prefix keeps this family structurally distinct from landscape-scheme
      resolves: layers
      segments:
        - name: hub
          pattern: "^[a-z][a-z0-9-]*$"
        - name: spoke
          pattern: "^[a-z][a-z0-9-]*$"
        - name: customer
          pattern: "^[a-z][a-z0-9]{4}$"
        - name: ring
          pattern: "^[a-z]{3}$"
        - name: environment
          pattern: "^[a-z0-9]{1,4}$"
          default: dev
      rules:
        customer: "customers/{customer}/tenant.yaml"
```

> **`scope` uses a single `*`, not `**`.** Verified directly: `fnmatch.translate()`
> compiles `"deploy/hubs/**"` and `"deploy/hubs/*"` to the exact same regex
> (`deploy/hubs/.*`). `fnmatch`'s `*` already matches any sequence of characters,
> including `/` — it has no path-aware "one segment" vs. "any depth" distinction
> the way `**` does in gitignore, bash `globstar`, or `pathlib.rglob`. A second `*`
> is therefore purely decorative here, not a stronger wildcard — using a single
> `*` is equivalent and more honest about what `scope` (an `fnmatch` pre-filter)
> actually does. This is unrelated to `pattern`, which has no `*`/`**` at all —
> it uses `{segment}` placeholders instead, matched by the separate,
> genuinely-segment-aware `match_pattern()`.

Note `required` is dropped from segment definitions in this design — see
[Decided: no existence-required segments](#decided-no-existence-required-segments--not-applicable-replaces-required)
below for why.

### Deploy side: `spec.layers.follows` + `spec.layers.segments` (reuses the existing field name)

A deployment declares which family it belongs to, and — only where the path itself
doesn't already say enough — the segment values that apply to it. Both live under
the existing `spec.layers` field, which changes shape from a flat `{name: value}`
dict to a structured object (`{follows, segments}`); consistent with this ADR's
already-accepted breaking change (no backward compatibility), and this is the
smallest naming change available since it introduces zero new vocabulary.

> **Naming note — rejected alternatives, and why.** Two earlier drafts of this
> section tried `spec.paths` (matching config's field name exactly) and then
> `spec.path` (singular, to signal the shape difference). Both were rejected:
> `spec.paths` on both sides would share a name but silently differ in shape (list
> vs. object) — exactly the trap this ADR exists to eliminate. `spec.path` vs.
> `spec.paths` is visually and textually too similar (one character apart) to
> reliably tell apart at a glance or avoid typoing. **`spec.layers` has no such
> risk** — it shares no characters or visual shape with `spec.paths`, so there is
> nothing to confuse it with, while still being immediately familiar since it's
> the field every existing strata deployment already uses today.

```yaml
# a hub-only deployment (shared infra, no customer/ring/environment yet)
spec:
  layers:
    follows: hub-scheme
    segments:
      hub: hub1
  # spoke/customer/ring/environment simply don't apply to this deployment

# a full customer-environment deployment — segments omitted entirely
spec:
  layers:
    follows: hub-scheme
  # every value auto-derived from this file's own path, which is 5 segments deep
```

If `spec.layers.segments` is omitted entirely, every value is auto-built from the
deployment file's own path against the `follows`-referenced convention's `pattern`.
If it is given, it supplies values directly (per name), and any name not present
there falls back to path-derivation, then `default` (see resolution order below) —
declaring `segments` is not all-or-nothing, individual keys are still optional.

### Resolution semantics

Two levels, same shape as before: first *which convention*, then *each segment's
value*.

#### Level 1 — which convention applies

1. **Explicit** — `deployment.spec.layers.follows` names a convention → use it
   directly. Validation error if the name doesn't exist in `configuration.spec.paths`,
   or exists but doesn't declare `resolves: layers`.
2. **Auto-detected ("find it")** — `spec.layers.follows` not set → match this file's
   path against every `resolves: layers` convention's `scope` + `pattern` (prefix
   match, same semantics as plain validation conventions — no exact-depth
   requirement anymore, since one convention now legitimately covers many real
   depths within its family). Use the one that matches. Hard validation error if
   more than one matches (ambiguous) — see uniqueness note below.
3. **None** — neither explicit nor auto-detected → no scheme applies;
   `spec.layers.segments` is unvalidated free-form data, exactly as today's graceful
   "no scope match" behavior.

#### Level 2 — each segment's value, once a convention is selected

For each segment name declared in the selected convention's `segments` list:

1. **Explicit** — `deployment.spec.layers.segments.<name>` is set → use it. Always
   wins.
2. **Derived** — the file's path matches the convention's `pattern` far enough to
   capture `<name>` → use the captured value. Because matching is prefix-based
   again, a shallower deployment's path simply doesn't reach `<name>`'s position —
   that's expected, not an error.
3. **Default** — the segment declares `default` → use it.
4. **Not applicable** — none of the above → `<name>` is treated as not part of
   this particular deployment's hierarchy (see below), not a validation failure.

### `rules:` validates the *resolved* value, not just the path capture

`rules:` (unchanged mechanism — membership against `spec.<field>[*].<attr>`, e.g.
`zone: spec.zones[*].name` against the real, existing
[`ConfigurationZoneModel`](../../src/strata/models/configuration_model.py#L498)/
`spec.zones` list; or file-existence, e.g. `customer:
"customers/{customer}/tenant.yaml"` to confirm a customer is an actually-onboarded
tenant) answers exactly this: "does this segment's value correspond to a known
zone/region/tenant/etc.," not just "does it match a regex shape."

**Confirmed: membership works against *any* list already loaded in the
configuration, not a fixed whitelist.** Traced
[`resolve_spec_rule()`](../../src/strata/utils/path_convention.py#L81) precisely —
it matches any string shaped `spec.<field_path>[*].<attr>` (regex
`^spec\.(.+)\[\*\]\.(.+)$`), then walks `field_path` via plain `getattr()` chains
against whatever the loaded `ConfigurationModel` actually has. There is no
hardcoded list of allowed field names anywhere; the only real constraints are (1)
the resolved object must be a Python `list`, and (2) each item must carry the
requested `attr`. Confirmed real, already-usable examples beyond zones, from
`ConfigurationSpecModel`'s actual fields:

```yaml
rules:
  zone: spec.zones[*].name              # ConfigurationZoneModel
  provider: spec.providers[*].name      # ConfigurationProviderModel
  topology: spec.topologies[*].type     # ConfigurationTopologyModel — attr is "type", not "name"
  remote: spec.remotes[*].name          # RemoteModel
  integration: spec.integrations[*].name  # IntegrationModel
  scheme: spec.paths[*].name            # even the path conventions' own names
```

Three caveats: `field_path` supports dotted nesting to *reach* a list (e.g.
`spec.foo.bar[*].name`), but only one `[*]` per rule — no list-of-lists
flattening. The attribute name after `[*].` must match whatever that particular
model actually calls it (`topologies` uses `type`, most others use `name`) — it
isn't blindly always `.name`. And **every intermediate step in the dotted path
must itself be a real Pydantic model attribute — never a freeform dict key.**
`getattr()` (what `resolve_spec_rule()` uses to walk the path) only resolves
object attributes, not dict keys — confirmed by simulating the exact walk against
a nested dict: it silently returns `None` the instant it hits one, which
`resolve_spec_rule()` then treats as "unresolvable, skip gracefully" — **no error
at all**, the rule just quietly validates nothing. This bites two real,
already-existing fields: `spec.configuration: Optional[Dict[str, Any]]` and
`spec.properties: Optional[Dict[str, Any]]` are genuine freeform dicts, so a rule
like `spec.configuration.someDict.Item1.listItems[*].field2` would compile and
run without complaint, and simply never constrain anything — indistinguishable
from a rule that was never written, unless someone happens to test the negative
case.

### Decided: add `spec.custom` and make `resolve_spec_rule()` dict-aware

Promoted from an earlier "noted, not required" side observation to part of the
solution: a new `configuration.spec.custom: Optional[Dict[str, Any]]` field is
added — a generic escape hatch, same pattern as the existing
`spec.configuration`/`spec.properties`, for structures that don't warrant a
dedicated typed model — and `resolve_spec_rule()` gains a dict-aware fallback so
`rules:` membership checks actually work against it instead of silently
resolving nothing.

Confirmed by simulation that a plain `Dict[str, Any]` field fails `rules:`
membership checks at **two** separate points, not one: the path-walk (`getattr()`
can't step into a dict key) and, even if that were bypassed, the final per-item
attribute extraction (`getattr(item, attr, None)`, since raw parsed YAML list
items are plain dicts too, not objects with real attributes). The fix touches
both:

```python
def _resolve_step(obj, name):
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)
```

used in place of the bare `getattr()` call at both the path-walk loop and the
final per-item value-extraction loop in `resolve_spec_rule()`. With this in
place, `spec.custom.<name>.list[*].field`-style rules work uniformly whether the
underlying data is typed Pydantic models or raw freeform dicts — closing the gap
for `spec.custom` specifically, and incidentally also fixing the same latent
limitation on the pre-existing `spec.configuration`/`spec.properties` fields.

**Gap found while checking this, now fixed by decision:** today's
[`evaluate_conventions()`](../../src/strata/utils/path_convention.py#L156) checks
`rules:` only against `captures` — the raw values `match_pattern()` extracts from
the file's *actual path*. It never sees `spec.layers.segments` (explicit values) at
all. Under the new model this silently breaks in two ways: (1) a shallow deployment
whose path doesn't reach a convention's full pattern depth gets `captures is None`
→ `rules:` is skipped entirely, so an explicitly-declared segment value (e.g. a
hub-only deployment's `hub: hub1`) never gets checked against anything; (2) even
when the path does capture a value, an explicit override (Level 2 step 1, which
always wins) would leave `rules:` still validating the now-irrelevant captured
value instead of the value actually in use.

**Decided:** `rules:` must validate the **resolved** value for each segment — the
outcome of the full Level 1 + Level 2 precedence above (explicit → derived →
default) — not the raw `match_pattern()` capture. This applies uniformly regardless
of how deep the deployment's own path goes, so "is this a known tenant/zone/etc."
checks apply consistently whether the value came from the path or was declared
directly in `spec.layers.segments`.

### Decided: no existence-required segments — "not applicable" replaces `required`

Resolved (DevOps-perspective input, second pass): the six-scheme originating
example exists precisely *because* different deployments in the same family have
different real depths on purpose — a shared-infra deployment genuinely has no
`customer`/`ring`/`environment`, not "a missing required value." A blanket
`required: true` per segment (as in the earlier draft, and as in today's
`ScopedLayeringModel.layers`) is incompatible with that reality once one
convention covers a whole family instead of one exact depth. So `required` is
dropped: a segment absent from both the explicit `spec.layers.segments` dict and the
deployment's own path is simply **not applicable** to that deployment — not an
error. `pattern` still validates any value that *does* get supplied, however it
was supplied. Artifact-path construction (below) reflects this directly: the path
is however many segments actually resolved, not padded out to the family's deepest
shape.

### Uniqueness: at most one auto-detected match per file

Only matters for Level 1's auto-detection step — irrelevant when `spec.layers.follows`
is set, since that's already unambiguous by construction. With one convention per
family (rather than one per depth), accidental overlap between families is avoided
by giving each family convention a **distinguishing literal prefix segment** in both
`scope` and `pattern` — not left to chance. This is exactly what the worked example
does: `landscape-scheme` lives under the literal prefix `deploy/landscape/**`, and
`hub-scheme` should do the same rather than starting its pattern directly with a
captured `{hub}` segment — e.g. `pattern: "deploy/hubs/{hub}/{spoke}/{customer}/{ring}/{environment}"`
(literal `hubs/` prefix) so it can never structurally match a `deploy/landscape/...`
path in the first place, regardless of scope. **Recommended practice: every
`resolves: layers` convention's pattern should start with a literal segment unique
to its family** — this is what actually keeps families apart, not an incidental
by-product of how the scopes happen to be written. If two conventions still both
match the same file despite this, that is a hard validation error naming both
convention names and the file, rather than a silent pick — the same "loud failure
over silent shadowing" principle as the original design.

### Artifact-path construction reflects actual, not maximal, depth

The build/artifact path is the join of however many segments actually resolved for
this specific deployment (in the convention's declared `segments` order), stopping
at the first segment that didn't resolve at all (Level 2 outcome 4, "not
applicable") — not the family's full/deepest shape. A hub-only deployment produces
a 1-segment artifact path; a full customer-environment deployment produces 5. Both
follow the same `hub-scheme` convention.

### Known gap: `rules:` can only reach the merged `ConfigurationModel` — never other kinds or external truth

Precision check on what `resolve_spec_rule()` actually receives: the
`configuration_model` it walks is `ConfigurationService.model` — and
`ConfigurationService` deep-merges **every loaded `kind: configuration` YAML
file** (via `ConfigurationLoader.merge_configs()`) into that one model before
validating it. So this is not "the single file currently being checked" — it is
the **full merged configuration** across all configuration-kind sources for the
active profile/workspace. Data declared in any `kind: configuration` file that
participates in that merge (`spec.zones`, `spec.environments`, `spec.providers`,
`spec.topologies`, the new `spec.custom`, etc.) is already reachable today,
config-file boundaries aside.

That merge is still bounded to documents of `kind: configuration`, though — it
never includes:

- **Other kinds entirely.** `kind: tenant` files are loaded by a completely
  separate `TenantService`/`TenantModel` (see
  [`tenant_service.py`](../../src/strata/services/tenant_service.py)) that never
  touches `ConfigurationService`. Tenant names (or anything else declared only in
  `kind: tenant`/`kind: environment`/`kind: deployment` documents) are structurally
  unreachable from `spec.*` rules, dict-aware or not — there is no code path that
  passes tenant data into `resolve_spec_rule()`.
- **External or code-level truth.** Real cloud-provider region catalogs, or
  constraints already enforced as a Python-side enum/`Literal` on a model field,
  exist nowhere in any loaded YAML at all. Duplicating them into `spec.custom`
  just to satisfy `rules:` would recreate the same "two sources of truth that can
  drift" problem `spec.custom` was meant to avoid for in-document data — it would
  make things worse, not better, since the "real" list (AWS/Azure's actual regions,
  or the code-level enum) lives entirely outside version-controlled config.

`spec.custom` and the `resolve_spec_rule()` dict-aware fix (above) only close the
reachability gap **within** the merged `ConfigurationModel`. They do not, and are
not intended to, extend `rules:` to reach other kinds or external sources — that
remains open (see below).

### Open questions not yet resolved by this design

- The exact policy type name/shape for agreement enforcement (Considered Option C,
  now a facet of E) is not designed here — only that it reuses `enforcement: deny |
  warn | audit`.
- Migration tooling (e.g. a `strata migrate` helper to convert existing
  `spec.layering`/`spec.layerings` into the new `spec.paths` shape automatically) is
  out of scope for this design pass — not yet decided whether one will exist.
- Whether/how `rules:` should ever reach data outside the merged
  `ConfigurationModel` — other kinds (e.g. `kind: tenant`) or external/code-level
  truth (cloud-provider region catalogs, existing `Literal`/enum constraints) — is
  a known gap, not designed here. See
  [Known gap: `rules:` can only reach the merged `ConfigurationModel`](#known-gap-rules-can-only-reach-the-merged-configurationmodel--never-other-kinds-or-external-truth).
  A future `tenant.*` rule prefix (reusing `TenantService`) or a static `enum:`
  rule kind are plausible directions, but neither is decided.

## Application Design

Concrete, file-and-signature-level design for implementing the
[Unified Schema Design](#unified-schema-design) above. Grounded in the actual current
code (read directly, not assumed) so an implementer starts from real signatures rather
than rediscovering them.

### One shared resolution function — the load-bearing design decision

Today's code already has a documented instance of the problem this must avoid:
[`utils/layering.py`](../../src/strata/utils/layering.py)'s own module docstring says
*"Both the deployment service and the overlap controller use these helpers so the
resolution logic is never duplicated"* — and yet
[`overlap_controller._compute_artifact_path()`](../../src/strata/controllers/overlap_controller.py#L145)
still reimplements the `spec.layering` (flat-scheme) branch inline rather than calling a
shared helper for it, only delegating to the shared helpers for the `spec.layerings`
(scoped) branch. Two branches, one delegated, one not — the exact "good intentions,
partial follow-through" failure mode. The new design must not repeat this: **one
function implements Level 1 + Level 2 resolution, full stop, and every caller —
validation, artifact-path construction, `rules:` checking, overlap/promote filtering —
calls it.**

Proposed home: `utils/path_convention.py` (already owns `match_pattern()`, the
segment-aware matcher this depends on) — not `utils/layering.py`, which is retired
entirely along with `spec.layering`/`spec.layerings`/`ScopedLayeringModel`.

```python
@dataclass
class LayerResolution:
    convention: Optional["PathConventionModel"]   # None = no convention applied (graceful no-op)
    values: Dict[str, str]                        # resolved segment values, in convention.segments order
    ambiguous_matches: List[str] = field(default_factory=list)  # >1 auto-match — hard error upstream


def resolve_layers(
    rel_path: str,
    layers: Optional["LayersModel"],           # deployment.spec.layers (follows + segments), or None
    conventions: List["PathConventionModel"],  # configuration.spec.paths entries with resolves == "layers"
) -> LayerResolution:
    """Level 1 (which convention) + Level 2 (each segment's value) — the ONLY place
    this precedence (explicit -> derived -> default -> not applicable) is implemented.
    """
```

Internally this reuses the existing `match_pattern()` for Level 2's path-derivation
step — no new matching engine, only new precedence logic wrapped around it.

### Decided: `resolve_layers()` always takes a typed `LayersModel` — only `overlap_controller.py` needs an adapter

Traced every real caller precisely rather than assuming a general "scan vs. loaded
model" split:

- **Loaded-model contexts** (`deployment_service.py`, `platform_builder.py`) already
  have a real `DeploymentModel` and `ConfigurationModel` in hand — call `resolve_layers()`
  directly with typed inputs, no complication.
- **`path_convention_policy.py` is NOT a raw-scan context.** `PolicyContext` already
  carries `deployment_service: Optional[DeploymentService]`
  ([`base_policy.py`](../../src/strata/validators/policies/base_policy.py#L26)) —
  it's evaluated per-file within an already-loaded-model flow, exactly like
  `deployment_service.py` itself. It calls `resolve_layers()` with a real typed
  `LayersModel` too. No special-casing needed here at all.
- **Only `overlap_controller.py` is a genuine raw-scan context** — it deliberately uses
  `yaml.safe_load()` + `spec.get("layers")`
  ([`overlap_controller.py`](../../src/strata/controllers/overlap_controller.py#L109-L121))
  to avoid full `DeploymentModel` validation across potentially thousands of manifests.
  But `layers` is a tiny sub-model (`follows` + `segments`), not the whole deployment —
  model-validation cost is proportional to what's validated. So
  `overlap_controller.py` constructs just `LayersModel(**raw_layers_dict)` from its
  already-extracted raw dict and passes that cheap, real typed instance into
  `resolve_layers()`. This preserves the fast-scan performance property (never
  validating the full deployment graph) without making the shared function itself
  dict-aware — that complexity would otherwise be paid by every caller, not just the
  one that needs it.

**Decided:** `resolve_layers()`'s `layers` parameter is `Optional["LayersModel"]` only
— never a raw dict. `overlap_controller.py` is the only call site that needs the
one-line `LayersModel(**raw_dict)` adapter; everyone else already has a typed instance.

### Models

- [`ConfigurationLayerModel`](../../src/strata/models/configuration_model.py#L180) —
  drop `required: bool` and its `validate_default_when_not_required` model validator
  (see [Decided: no existence-required segments](#decided-no-existence-required-segments--not-applicable-replaces-required)).
  Safe to modify in place rather than fork a new model: its only other consumer,

- [`ConfigurationLayerModel`](../../src/strata/models/configuration_model.py#L180) —
  drop `required: bool` and its `validate_default_when_not_required` model validator
  (see [Decided: no existence-required segments](#decided-no-existence-required-segments--not-applicable-replaces-required)).
  Safe to modify in place rather than fork a new model: its only other consumer,
  `ScopedLayeringModel.layers`, is retired in the same change, so nothing else depends
  on `required` surviving.
- [`ScopedLayeringModel`](../../src/strata/models/configuration_model.py#L206) — deleted
  entirely.
- [`PathConventionModel`](../../src/strata/models/configuration_model.py#L256) —
  `resolves: Optional[Literal["tenant"]]` becomes
  `Optional[Literal["tenant", "layers"]]`; add
  `segments: Optional[List[ConfigurationLayerModel]]` (only meaningful when
  `resolves == "layers"`; a model validator should enforce that combination, mirroring
  the existing `validate_segments_match_pattern` validator's style). **Review finding:**
  also needs a new "segment names unique within this convention" validator — the
  retired `ScopedLayeringModel` had exactly this
  (`validate_unique_layer_names_in_scheme`, checking `self.layers`), and nothing
  carries that check forward for the new inline `segments` list unless it's added
  explicitly. Note `rules` is already aliased to the YAML key `validate` with
  `populate_by_name=True` — both `rules:` and `validate:` are already accepted as
  YAML keys today; this is unrelated to this ADR's changes and needs no action, just
  worth knowing before assuming `rules:` is the only spelling.
- [`ConfigurationSpecModel`](../../src/strata/models/configuration_model.py#L586) —
  remove `layering` and `layerings` fields entirely (no deprecation period — see
  [Decision Outcome](#decision-outcome)), **and delete its
  `validate_unique_layer_names` model validator** (checks `layering`/`layerings`
  mutual exclusivity plus name uniqueness for both) — dead code the moment the two
  fields it reads no longer exist; easy to miss since it's a validator, not a field,
  so a search for the field names alone won't surface it. Add
  `custom: Optional[Dict[str, Any]]` (see
  [Decided: add spec.custom](#decided-add-speccustom-and-make-resolve_spec_rule-dict-aware)).
  **Naming collision to be aware of, not a conflict:**
  [`DeploymentSpecModel`](../../src/strata/models/deployment_model.py#L412) already has
  its own, pre-existing, unrelated `custom: Optional[Dict[str, Any]]` field — same name,
  different model, different purpose (deployment-side freeform properties vs.
  configuration-side freeform escape hatch). No code conflict since they're different
  Pydantic models, but worth a docstring note on both so nobody conflates them.
- [`DeploymentSpecModel.layers`](../../src/strata/models/deployment_model.py#L420) —
  changes from `Optional[Dict[str, str]]` to `Optional[LayersModel]`, a new small model:
  `follows: Optional[str]` + `segments: Optional[Dict[str, str]]` (see
  [Deploy side](#deploy-side-speclayersfollows--speclayerssegments-reuses-the-existing-field-name)).
  Update its docstring, which currently says "keys must match
  `configuration.spec.layering[].name`" — that field no longer exists.
- [`platform_artifact_model.py`](../../src/strata/models/platform_artifact_model.py#L653) —
  `PlatformSpecModel.deployment`'s field description also references
  `configuration.spec.layering[].name` and needs the same docstring update.

### Utils

- [`utils/layering.py`](../../src/strata/utils/layering.py) — deleted entirely
  (`resolve_layering_scheme()`, `compute_artifact_path()`). Both retired in favor of
  `resolve_layers()`.
- [`utils/path_convention.py`](../../src/strata/utils/path_convention.py) — add
  `resolve_layers()` (above). Modify
  [`evaluate_conventions()`](../../src/strata/utils/path_convention.py#L156): for
  conventions with `resolves == "layers"`, `rules:` must validate against the
  `LayerResolution.values` for that segment (the Level 1+2 resolved outcome), not the
  raw `match_pattern()` capture — see
  [`rules:` validates the resolved value](#rules-validates-the-resolved-value-not-just-the-path-capture).
  For every other convention (plain validation conventions, `resolves == "tenant"`, or no
  `resolves`), behavior is unchanged — raw captures, exactly as today. Also modify
  `resolve_spec_rule()` per the dict-aware fix already decided.

### Services

- [`deployment_service.py`](../../src/strata/services/deployment_service.py) —
  [`_validate_deployment_layers()`](../../src/strata/services/deployment_service.py#L377)
  rewritten: call `resolve_layers()`, then validate each resolved value against its
  segment's `pattern` (no more `required` check — "not applicable" replaces it, per the
  decision above). [`get_artifact_path()`](../../src/strata/services/deployment_service.py#L540)
  rewritten: call `resolve_layers()`, join `.values` in the convention's `segments`
  order, stopping at the first unresolved ("not applicable") segment — no more
  branching on `spec.layerings` vs. `spec.layering`, since only one mechanism exists
  now. Tenant-resolution call sites (`resolves: tenant`) are untouched — a separate
  `resolves` value, not merged with this.

### Controllers

- [`overlap_controller.py`](../../src/strata/controllers/overlap_controller.py#L145) —
  `_compute_artifact_path()` deleted; call `resolve_layers()` directly, constructing a
  `LayersModel(**raw_layers_dict)` adapter from its existing raw-YAML extraction (see
  [Decided: resolve_layers() always takes a typed LayersModel](#decided-resolve_layers-always-takes-a-typed-layersmodel--only-overlap_controllerpy-needs-an-adapter)).
- [`promote_controller.py`](../../src/strata/controllers/promote_controller.py#L465) —
  `_scope_filter()`/`_get_scope_selector()` currently read `dep.spec.layers` as a flat
  dict directly (`dict(dep.spec.layers)`), which breaks outright once `spec.layers` is
  a `LayersModel`. **Decided: fully resolved**, for consistency with every other
  consumer (`get_artifact_path()`, `rules:`) — a deployment that omits `segments`
  entirely and derives every value from its own path is just as real a member of a
  layer as one that declares it explicitly (Goal #2's "explicit always wins over
  derived" implies derived values are equally valid, not second-class). Both methods
  call `resolve_layers(rel_path, dep.spec.layers, conventions)` and use `.values`
  instead of `dict(dep.spec.layers)` — `_scope_filter()` checks `scope in resolution.values`,
  `_get_scope_selector()` iterates `resolution.values.values()`. Needs the deployment's
  `rel_path` and the `resolves: layers` conventions plumbed into this controller (not
  previously required, since it only ever read the flat dict directly).

### Builders

- [`builders/platform_builder.py`](../../src/strata/builders/platform_builder.py) —
  update the `get_artifact_path()` call site (L293-294), `convenience_layers` (L525),
  and `deployment=deployment_model.spec.layers` (L575) for the new `LayersModel` shape.
  `resolve_tenant_file_path()` call site (L489-491) is untouched.

### Rollout safety already built in — no migration tooling required for detection

Every strata model uses Pydantic `extra="forbid"` (see
[`.github/copilot-instructions.md`](../../.github/copilot-instructions.md)) — an existing
configuration file using `spec.layering`/`spec.layerings` fails **model validation
immediately** the moment those fields are removed from `ConfigurationSpecModel`, with a
clear "unknown field" error naming exactly the removed field. This is a loud, immediate
failure, not a silent behavior change — the "migration tooling" open question in
[Remaining Work](#remaining-work) is about *convenience* (auto-converting old configs),
not *detection* (already free from `extra="forbid"`).

## Implementation Plan

Ordered, dependency-sequenced phases for building [Application Design](#application-design).
Each phase is independently reviewable (models before code that reads them, code before
the tests/docs that describe it). Not a substitute for
[Remaining Work](#remaining-work) — the policy type and migration tooling listed there
stay deferred and are intentionally absent from this plan.

Both Phase 0 items from earlier design passes are now resolved (see
[Application Design](#application-design)) — the scan-context question
(`overlap_controller.py` uses a `LayersModel(**raw_dict)` adapter) and the
`promote_controller.py` behavior choice (fully resolved). No blocking decisions
remain before implementation starts.

### Phase 1 — Models (`src/strata/models/`)

1. `deployment_model.py` — add `LayersModel` (`follows`, `segments`); change
   `DeploymentSpecModel.layers` to `Optional[LayersModel]`; fix the docstring's
   `spec.layering[].name` reference.
2. `configuration_model.py` — drop `ConfigurationLayerModel.required` and its
   `validate_default_when_not_required` validator; delete `ScopedLayeringModel`
   entirely; extend `PathConventionModel.resolves` to
   `Literal["tenant", "layers"]`, add `segments`, add the resolves/segments
   combination validator and the new segment-name-uniqueness validator; remove
   `ConfigurationSpecModel.layering`/`.layerings` and delete
   `validate_unique_layer_names`; add `ConfigurationSpecModel.custom`.
3. `platform_artifact_model.py` — fix the `spec.layering[].name` docstring reference.
4. Update `tests/strata/models/test_models_configuration.py` for the new
   `PathConventionModel`/`ConfigurationSpecModel` shape in the same phase — a model
   change without its own tests updated isn't reviewable in isolation.

### Phase 2 — Utils (`src/strata/utils/`)

1. Delete `utils/layering.py`.
2. Add `LayerResolution` + `resolve_layers()` to `utils/path_convention.py`,
   implementing the Phase 0 scan-context decision.
3. Modify `resolve_spec_rule()` (dict-aware `_resolve_step()` fallback) and
   `evaluate_conventions()` (resolved-value validation for `resolves == "layers"`
   conventions only; unchanged for everything else).
4. Update/add tests: `test_utils_path_convention_tenant.py` plus new coverage for
   `resolve_layers()` (Level 1 ambiguous-match error, Level 2 explicit/derived/
   default/not-applicable) and the `resolve_spec_rule()` dict-aware fix.

### Phase 3 — Services (`src/strata/services/deployment_service.py`)

1. Rewrite `_validate_deployment_layers()` — call `resolve_layers()`, pattern-check
   resolved values, no more `required` check.
2. Rewrite `get_artifact_path()` — call `resolve_layers()`, join `.values` in
   `segments` order, stop at first not-applicable segment.
3. Update `test_services_deployment.py` accordingly.

### Phase 4 — Controllers (`src/strata/controllers/`)

1. `overlap_controller.py` — delete `_compute_artifact_path()`, call
   `resolve_layers()` per the Phase 0 scan-context decision.
2. `promote_controller.py` — update `_scope_filter()`/`_get_scope_selector()` to call
   `resolve_layers()` for the fully resolved view (decided — see
   [Application Design](#application-design)), plumbing in each deployment's
   `rel_path` and the `resolves: layers` conventions list.
3. Update `test_controllers_overlap.py` and any promote-controller tests touching
   `spec.layers`.

### Phase 5 — Builders (`src/strata/builders/platform_builder.py`)

1. Update the `get_artifact_path()` call site, `convenience_layers`, and
   `deployment=deployment_model.spec.layers` for the new `LayersModel` shape.
   `resolve_tenant_file_path()` call site is untouched.
2. Update any builder tests exercising `spec.layers`/artifact paths.

### Phase 6 — Docs and changelog

1. `docs/config/configuration.md` — rewrite "Layering — Artifact Path Hierarchies"
   and "Path Convention Policy" sections for the merged mechanism.
2. `docs/GLOSSARY.md` — update the "Layering & Multi-Tenancy Concepts (ADR 0003)"
   section.
3. [ADR 0042](./0042-deep-validation-layer-consistency.md) and
   [ADR 0052](./0052-path-convention-validation.md) — mark superseded or
   cross-reference per their own Remaining Work.
4. `.github/CHANGELOG.md`/`HISTORY.md` — breaking-change entry. Communication, not a
   safety net — `extra="forbid"` already guarantees detection (see above).

### Phase 7 — Full validation and status update

1. Full `scripts/Check.ps1` (lint + format + types) and full test suite.
2. Update this ADR's `- Status:` line from `proposed` to `implemented` (or
   `partially-implemented` if the policy-type/migration-tooling follow-up work in
   [Remaining Work](#remaining-work) hasn't landed yet).

## Remaining Work

<!-- Required while Status is proposed / in-progress / partially-implemented.
     Remove this section once Status becomes implemented. -->

**Shipped** (Phases 1-6 of the [Implementation Plan](#implementation-plan)): the merged
schema (`resolves: layers` + inline `segments`, `spec.layers.follows`/`segments`,
`required` dropped), the shared `resolve_layers()` with Level 1 + Level 2 precedence,
`rules:` validating the resolved value, `spec.custom` + dict-aware
`resolve_spec_rule()`, removal of `spec.layering`/`spec.layerings`/`ScopedLayeringModel`/
`utils/layering.py`, all call sites (`deployment_service`, `overlap_controller`,
`promote_controller`, `platform_builder`, `path_convention_policy`), migration of every
bundled example stack and scaffold template, and docs/changelog. Full `Check.ps1` green.

Still open:

- Design and implement the new (or extended) policy type for agreement enforcement,
  reusing `enforcement: deny | warn | audit`; decide its default enforcement level and
  whether it ships enabled-by-default or requires explicit opt-in like every other
  policy. *(Not designed — the work so far covers the schema and resolution
  precedence, not the enforcement policy. Nothing currently fails a build when an
  explicitly-declared segment value disagrees with the one its path implies; the
  explicit value simply wins.)*
- Decide whether migration tooling (e.g. a `strata migrate` helper to rewrite existing
  `spec.layering`/`spec.layerings` automatically) is worth building. Detection is
  already free — `extra: forbid` fails loudly on the removed field — so this is purely
  a convenience question.
- Revisit `PromoteController`'s deployment-path tracking if that code grows.
  `_load_registered_deployments()` records each deployment's `rel_path` in a
  `self._dep_rel_paths` dict keyed by `meta.name`, which `_resolve_dep_layers()`
  reads back — `DeploymentModel` carries no field for its own source path, and
  `resolve_layers()` needs one for Level 1 auto-detection and Level 2 derivation.
  Keying by `meta.name` is safe today (both registration paths derive the registry
  name from the file's own `meta.name`, and `SolutionController.add_deployment()`
  rejects duplicates), but it is an implicit invariant rather than an enforced one.
  A `(model, rel_path)` carrier type would remove the coupling if that method ever
  gains a second caller.
- Consider whether the one remaining silent case deserves a signal: a deployment
  that declares **no** `spec.layers` at all, in a workspace whose `resolves: layers`
  convention no longer matches it. Nothing claims to be in a hierarchy, so there is
  nothing to contradict — but it is still indistinguishable from a typo'd pattern.
  (The far more damaging variant — a deployment that *does* declare `spec.layers`
  while no convention claims it, which silently produced an empty artifact path —
  is now a reported error; see
  [`LayerResolution`'s three states](../../src/strata/utils/path_convention.py).)
- Make `PromoteController` load deployments *with* a configuration model, so
  ADR-0072 resolution errors become hard validation failures there instead of
  advisory messages. `_load_registered_deployments()` calls
  `DeploymentService.load()` with no configuration model, and
  `BaseService.validate()` only runs Phase 2 (where
  `_validate_deployment_layers()` reports these) when one is supplied — so an
  unknown `follows` name or an ambiguous convention match currently surfaces only
  as a message from `_resolve_dep_layers()`, while scope filtering silently falls
  back to explicit-only values. That fallback can change *which* deployments a
  scoped wave promotes, so it deserves a hard failure rather than a message.
