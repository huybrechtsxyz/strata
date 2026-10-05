# Version Pin Overlay — Wiring the Remaining 3 of 4 Pin Categories

- Status: implemented
- Last updated: 2026-10-01

## Overview

[ADR-0019](../decisions/0019-version-pinning.md) built `kind: version` as
"the single file an operator edits to perform an upgrade" — a pin only
takes effect once something actually *applies* it (the "overlay"). Today
only 1 of the 4 pin categories (`artifacts`) is wired to anything; the
other 3 (`images`, `charts`, `remotes`) are schema-validated and
existence-checked (`check_version_pins()`, Phase 2) but have **zero real
effect** — changing one of these pins today changes nothing about what
`strata build run` actually produces. `VersionService.resolve()`, the
method the model's own docstring calls "the single lookup the overlay
uses," is dead code — grepped every call site in `src/`, it is never
invoked outside its own definition.

This doc designs wiring the remaining three categories, and consolidating
`artifacts` (currently a separate, duplicate lookup in `value_controller.py`)
onto the same shared `VersionService.resolve()` path, so there is one
overlay mechanism, not two.

## Current Design (evidence, as of 2026-10-01)

| Category    | Target field                    | Consumer site (where the field is actually read)                                                         | Overlay wired?                                                                                                         |
| ----------- | ------------------------------- | -------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------- |
| `artifacts` | `ArtifactSpecModel.image_tag`   | `value_controller.resolve_artifact_field()`                                                              | **Yes** — but via a hand-rolled `(version.spec.pins.artifacts or {}).get(name)` lookup, not `VersionService.resolve()` |
| `images`    | `ModuleServiceModel.image`      | `compose.py`'s service-building (`prepare_namespace()`, build-time)                                      | No                                                                                                                     |
| `charts`    | `SourceModel.chart_version`     | `helm.py`'s `_render_meta()` (build-time, writes `meta.yaml`'s `chartVersion`)                           | No                                                                                                                     |
| `remotes`   | `SolutionRemoteModel.reference` | `remote_resolution.resolve_remote()`, called from `source_sync.py` (both provisioner and module sources) | No                                                                                                                     |

**Confirmed by reading the real call chains, not assumed:**

- `images`/`charts` are both applied at **build time only**.
  `helm.py`'s `_resolve_chart()` (the deploy-time half) reads chart
  coordinates back **from the already-written `meta.yaml`**, never
  re-derives them from a live `ModuleModel` — so overlaying `chart_version`
  once, at build time, is sufficient; deploy time inherits it for free.
  Compose's `prepare_namespace()` is build-time for the same reason
  (`compose.py`'s own module docstring).
- `remotes` is consumed by **both** the provisioner loop
  (`build_controller.py`) and the workload pipeline
  (`workload_controller.build_workload_modules()`) — both already receive
  the *same* `remotes: dict[str, SolutionRemoteModel]` parameter, built
  once in `build_run()`.
- `VersionService.resolve()`'s own docstring lists categories as
  `'images', 'charts', 'remotes', 'tools'` — **`'tools'` is stale/wrong**;
  `VersionPinsModel`'s real `PIN_CATEGORIES` is `("images", "charts",
  "remotes", "artifacts")` (`pins.tools` was deliberately never ported,
  per `version_model.py`'s own module docstring). Needs fixing regardless
  of which option below is chosen.

## Design

### Principle: one overlay point per category, at the layer that already resolves it

Matches the existing "controller resolves, integration renders" split
(`workload_controller.py`'s own docstring, `ADR-0021` D2) — no
`VersionModel`/pin awareness is added to `compose.py`/`helm.py` themselves;
both integrations keep reading `ModuleModel`/`SourceModel` exactly as they
do today. The overlay is applied **before** those models reach the
integration layer.

1. **`remotes`** — overlay once, in `build_controller.build_run()`, where
   the `remotes: dict[str, SolutionRemoteModel]` dict is first assembled.
   For each remote with a matching `pins.remotes[name]` pin (and only
   where `fetch: strata` — `check_version_pins()` already rejects
   `fetch: external` pins, so this overlay can trust every pin reaching it
   is valid), build a copy with `.reference` replaced by the pin's
   `version`. The overridden dict then flows unchanged through every
   existing call site (`source_sync.py`, the provisioner loop,
   `build_workload_modules()`) — **zero new parameters**, since `remotes`
   is already threaded everywhere it's needed.

2. **`images`/`charts`** — overlay once, in
   `workload_controller.build_workload_modules()`, right after
   `resolve_module()` returns the real `ModuleModel` and before it is
   wrapped into a `ResolvedModule`/handed to `prepare_namespace()`:
   - `charts`: if `pins.charts[module.meta.name]` exists, build a copy of
     `module` with `spec.source.chart_version` replaced by the pin's
     `version`.
   - `images`: if any `pins.images[service.name]` exists for a service in
     `module.spec.services`, build a copy of `module` with that service's
     `.image` replaced by the pin's `version`. (Walks every service, not
     just the first — a module can declare several services, each
     independently pinnable, same key space `pins.images` already
     documents: "Key = ModuleServiceModel.name".)
   - Both reuse the same resolved `VersionModel` (looked up once per
     `build_workload_modules()` call, same place `remotes` is already a
     parameter) — a single helper, e.g. `_apply_version_pins(module,
     version) -> ModuleModel`, keeps the two category-specific branches
     next to each other instead of duplicating the "is there a pin, if so
     copy-and-override" shape twice.
   - `compose.py`/`helm.py` need **no changes** — they already read
     `module.spec.services[].image`/`module.spec.source.chart_version`
     directly; the overlay happens on the copy passed in, not on their
     own logic.

3. **`artifacts`** — consolidate onto `VersionService.resolve("artifacts",
   artifact_name)` instead of `value_controller.py`'s own
   `(version.spec.pins.artifacts or {}).get(...)` line. Same outcome,
   removes the one duplicate lookup implementation. Low risk: one call
   site, already has test coverage (`artifact-references.md`'s existing
   tests) to catch a regression.

4. **Logging every application (ADR-0019 decision 7 — explicitly "not
   optional")** — not in my first pass of this design, added after
   re-reading the ADR's own text directly: *"Every application must be
   logged — target category, name, the declared value, the pin value, and
   the document it came from — and a pin that matches nothing must be
   reported."* The "matches nothing" half is already satisfied by
   `check_version_pins()`'s existing warning. The "every application
   logged" half is not — needs a single shared helper (e.g.
   `_log_pin_applied(category, name, declared, pinned, version_doc)`,
   called from all three overlay points above) emitting through this
   repo's existing `structlog` logging, not a new mechanism.

### Where `VersionModel` comes from

Already resolvable everywhere it's needed: `deployment.spec.version` is a
`References(PlatformKind.VERSION)` field, resolved the same way `tenant`
already is (`value_controller.resolve_tenant()`'s precedent) — a new
`resolve_version(context, deployment) -> VersionModel | None` alongside the
existing `resolve_tenant()`/`resolve_artifact()` (same module, same
"silently return None when unset or unresolved" convention). `build_run()`
resolves it once, passes it to `build_workload_modules()` (new parameter,
`version: VersionModel | None = None`) and uses it to build the overridden
`remotes` dict before the provisioner loop starts.

### Fix alongside this (small, unrelated to the design itself)

`VersionService.resolve()`'s docstring `category` list — change `'tools'`
to `'artifacts'`.

## Related Decisions

- [ADR-0019](../decisions/0019-version-pinning.md) — the original design;
  this doc implements the "pin overlay... not wired" item its own Status
  line has flagged since 2026-09-22.


## History

- `spec.hash` tamper verification and a `ProvisionerModel.version` preflight assertion are deliberately out of scope - this design makes pins *have effect*; it does not verify the pins file itself hasn't drifted, nor assert installed tool versions. Separate follow-ups if ever picked up.
- `images`/`charts` overlays hard-skip defensively rather than apply unconditionally when the override doesn't structurally match the target (e.g. an `images` pin naming a non-`.image`-based service) - `check_version_pins()`'s own category check wasn't sufficient cover by itself to rule this out.
- `remotes`/`images`/`charts` overlays needed zero changes to `compose.py`/`helm.py` themselves - applied one layer up, before a module/remote ever reaches those integrations.
- Not in scope here: the full `tenant_zone` plan-time policy check and `promotions`/other policy types - unrelated gaps raised in the same conversation.
