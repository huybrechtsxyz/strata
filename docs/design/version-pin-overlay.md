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

## Implementation Plan

Decided order: shared infra first (nothing to regress yet), then the
lowest-risk consolidation (`artifacts`, one already-tested call site),
then the two genuinely new overlays, then an end-to-end proof. Each phase
leaves the full check suite green (`mypy src` / `ruff check --fix src
tests` / `lint-imports` / `pytest -q`) before the next starts — no phase
hands off a red suite.

- [x] **Phase 1 — Shared infrastructure, no behavior change yet.** Done
  2026-10-01.
  - `value_controller.py`: added `resolve_version(context, deployment) ->
    VersionModel | None`, same shape as `resolve_tenant()` immediately
    above it (silently `None` when `deployment.spec.version` is unset or
    unresolvable) — no new imports needed, `VersionModel`/`PlatformKind`
    were already imported in that module.
  - `version_service.py`: fixed `resolve()`'s stale docstring (`'tools'`
    -> `'artifacts'`).
  - `version_pins.py`: added `log_pin_applied(category, name, declared,
    pinned, version_name)`, using `strata.logging.config.get_logger(__name__)`
    (the real existing convention, matching `deployment_resolution.py`'s
    own `log.debug(...)` pattern) — satisfies ADR-0019 decision 7's "every
    application must be logged" for whichever categories call it in later
    phases.
  - Tests: 3 new `resolve_version()` cases in `test_value_controller.py`
    (resolved, unset, unresolvable-reference) mirroring
    `resolve_tenant()`'s own 2 existing tests; 2 new `log_pin_applied()`
    cases in `test_version_pins.py` (structured-fields assertion via the
    same `configure_logging(json_output=True, stream=...)` capture
    pattern `test_config.py` already uses, plus a `declared=None` case).
  - **Found and fixed one unrelated, pre-existing dev-environment issue
    while verifying**: `site-packages` had a stale
    `xyz_strata-2.0.0a1.dist-info`/`__editable__...pth` pair left over
    from the alpha2 version bump (a different stale-metadata shape than
    the `release-strategy.md` changelog's earlier `.egg-info` finding —
    same root cause class, a second occurrence) — caused
    `test_module_execution_runs_this_strata` to fail (subprocess reported
    `2.0.0a1`, in-process `get_version()` correctly reported `2.0.0a2`).
    Fixed via `uv pip install -e . --reinstall-package xyz-strata`.
  - Full check suite green: mypy (121 files), ruff, import-linter (1
    kept, 0 broken), pytest (1672 passed, same 1 known pre-existing
    unrelated failure as every prior run this session).

- [x] **Phase 2 — Consolidate `artifacts` onto the shared method.** Done
  2026-10-01.
  - `value_controller.py`'s `resolve_artifact_field()`: replaced
    `(version.spec.pins.artifacts or {}).get(artifact_name)` with
    `VersionService.from_model(version).resolve("artifacts", artifact_name)`
    — `from_model()` (not `data=...`) since the `VersionModel` is already
    resolved/validated off the index, matching the same pattern
    `semantic_checks.py` already uses for `WorkspaceService.from_model()`.
    Calls `log_pin_applied("artifacts", artifact_name, declared_image_tag,
    pin.version, version.meta.name)` when a pin is found.
  - Updated `test_resolve_artifact_field_image_tag_uses_version_pin_when_present`
    (unchanged assertions — confirms the refactor didn't change the
    resolved value) and added
    `test_resolve_artifact_field_image_tag_pin_application_is_logged` (new,
    asserts the structured log entry) in `test_value_controller.py`.
  - Full check suite green: mypy (121 files), ruff, import-linter (1
    kept, 0 broken), pytest (1673 passed, same 1 known pre-existing
    unrelated failure).

- [x] **Phase 3 — Wire the `remotes` overlay.** Done 2026-10-01.
  - `build_controller.py`: resolves `version = resolve_version(context,
    deployment)` alongside `tenant`. Extracted the overlay itself into a
    standalone `_apply_remote_version_pins(remotes, version) ->
    dict[str, SolutionRemoteModel]` (not left inline) — returns the same
    `remotes` object unchanged when `version` is `None` or nothing
    matches (avoids an unconditional copy on the common no-pin path),
    otherwise a new dict with only the pinned entries replaced
    (`model_copy(update={"reference": pin.version})`), logging each via
    `log_pin_applied()`. Called once, right after `remotes` is built; the
    overridden dict flows unchanged into every existing call site
    (`sync_source()`, `build_workload_modules()`) — no signature changes
    needed anywhere else.
  - Tests: 4 new pure unit tests for `_apply_remote_version_pins()` in
    `test_build_controller.py` (no version, no matching pin, overrides
    correctly + leaves other remotes/the original dict untouched, logs
    the application) plus 2 end-to-end `build_run()` tests exercising a
    real `fetch: strata` git remote (faking `git clone`'s filesystem
    side effect via a `remote_resolution.run_command` monkeypatch, same
    technique `test_remote_resolution.py` already established) —
    confirms the pinned ref's checkout path
    (`layout.remote_checkout_path()`) is the one actually materialised,
    and that an un-pinned deployment still uses the manifest's own
    declared reference.
  - Full check suite green: mypy (121 files), ruff, import-linter (1
    kept, 0 broken), pytest (1679 passed, same 1 known pre-existing
    unrelated failure).

- [x] **Phase 4 — Wire the `images`/`charts` overlay.** Done 2026-10-01.
  - `workload_controller.py`: added `_apply_version_pins(module:
    ModuleModel, version: VersionModel | None) -> ModuleModel` — returns
    `module` unchanged when `version` is `None` or neither category has a
    matching pin. Also deliberately conservative beyond the original
    design sketch, found necessary while implementing: `charts` only
    overrides `spec.source.chart_version` when the module is already
    chart-based (`chart_name` set, mirroring `check_version_pins()`'s own
    `pin_not_applicable` case); `images` only overrides a service's
    `.image` when that service already declares one (a service may use
    `.artifact` instead, mutually exclusive with `.image` — a pin changes
    a version, never which field is in use). Calls `log_pin_applied()`
    per application.
  - `build_workload_modules()`: new keyword parameter `version:
    VersionModel | None = None`; calls `_apply_version_pins()` right
    after `resolve_module()`, before the module is wrapped into a
    `ResolvedModule`.
  - `build_controller.py`: passes the already-resolved `version` (from
    Phase 3) through to `build_workload_modules()`.
  - Tests: 7 new pure unit tests for `_apply_version_pins()` in
    `test_workload_controller.py` (no version, no match, chart override +
    skip-when-not-chart-based, image override + untouched sibling +
    skip-when-artifact-based, logging both categories at once) plus 2
    end-to-end `build_workload_modules()` tests confirming the pinned
    value reaches the real rendered `meta.yaml`/`docker-compose.yml` —
    simpler than originally planned (testing through
    `build_workload_modules()` directly rather than separately through
    `test_integrations_compose.py`/`test_integrations_helm.py`, since
    neither integration needed any change at all — the overlay happens
    entirely before either one ever sees the module).
  - Full check suite green: mypy (121 files), ruff, import-linter, pytest
    (1688 passed, same 1 known pre-existing unrelated failure).

- [x] **Phase 5 — End-to-end proof and documentation.** Done 2026-10-01.
  - `test_build_controller.py::test_build_run_applies_remotes_charts_and_images_pins_simultaneously`:
    one `Version` document pinning all 3 newly-wired categories at once,
    exercised through a real `build_run()` against a solution combining
    the Phase 3 remotes fixture (`_terraform_solution_with_remote`) with
    a namespace carrying both a chart-based (helm) and an image-based
    (compose) module — confirms the materialised remote checkout, the
    rendered Helm `meta.yaml`, and the rendered `docker-compose.yml` all
    reflect their pins simultaneously, with no ordering/interaction bugs
    between the three overlay call sites. Passed first try.
  - This doc's `Status` updated to `implemented`.
  - [ADR-0019](../decisions/0019-version-pinning.md)'s `- Status:` line
    updated to `implemented` (body left otherwise untouched, per that
    doc's own immutability convention).
  - [v2-schema-overview.md](v2-schema-overview.md)'s `version` row updated
    to reflect all 4 categories now being overlaid.
  - Full check suite green: mypy (121 files), ruff, import-linter, pytest
    (1689 passed, same 1 known pre-existing unrelated failure).

## Related Decisions

- [ADR-0019](../decisions/0019-version-pinning.md) — the original design;
  this doc implements the "pin overlay... not wired" item its own Status
  line has flagged since 2026-09-22.

## Remaining Work / Open Questions

**Cross-checked against ADR-0019's own `## Remaining Work` list directly
(2026-10-01), item by item, since that list predates this doc:**

- ✅ *"Reject a `remotes` pin naming an unknown remote or one with
  `fetch: external`"* — already done, `check_version_pins()` (Phase 2)
  already reports this. Nothing further needed.
- ✅ *"Ref-keyed remote checkout layout, before any fetch code is
  written"* — already done, `layout.remote_checkout_path()` already keys
  by `reference`, confirmed by reading it directly. The overlay design
  above (override `.reference` before any checkout-path call) relies on
  this already being true — it is.
- ⚠️ *"Wire the pin overlay... with the logging required by decision
  7"* — the overlay itself is this doc's main design; the **logging**
  half was initially missed and is now folded in above (item 4).
- ❌ **`spec.hash` tamper verification** — *not* part of this design.
  Verifying the version document itself hasn't silently drifted since it
  was "locked" is an orthogonal safety net (detects a hand-edit or bad
  merge to the pins file), not part of making a pin have effect. A pin
  with no `hash` set works today and would continue to after this design
  ships; `hash` checking is a separate follow-up, deliberately not bundled
  in here.
- ❌ **`ProvisionerModel.version` preflight assertion** — *not* part of
  this design either. That's verifying an installed tool binary's real
  version against a declared assertion (exe presence/env var/endpoint
  reachability) — a different kind of "version" entirely (tool version,
  not a pinned target), unrelated to `kind: version`'s pin overlay.

**So: once implemented, this design gives all 4 pin categories real
effect and satisfies decision 7's logging requirement — but does not by
itself close every item ADR-0019 ever listed.** `spec.hash` verification
and the provisioner preflight assertion remain open, separate follow-ups
if they're ever picked up.

Other open items from the original design pass:

- ✅ **Severity/behavior when a pin's target resolves but the override
  itself is nonsensical** — decided and implemented in Phase 4 (not left
  open): hard-skip defensively rather than apply unconditionally. A
  `charts` pin only applies when `source.chart_name` is set; an `images`
  pin only applies when that service's `.image` is already set (not
  `.artifact`). Chosen over "trust Phase 2 already caught it" because
  `check_version_pins()`'s `images` category check only confirms a
  *service* with that name exists somewhere — it does not check whether
  that service is `.image`-based, unlike the `charts` check (which does
  check `chart_name`) — so Phase 2 alone was not actually sufficient
  cover for the `images` case specifically.
- **Not in scope here**: the full `tenant_zone` policy plan-time check
  (`docs/design/tenant-zone-policy.md`) and `promotions`/other policy types
  (`docs/design/gap_fit_v1.md` gap #7) — unrelated gaps, raised in the same
  conversation but not part of this design.

## Changelog

- 2026-10-01: Created. Captures the evidence that `images`/`charts`/
  `remotes` pin overlays are completely unwired (`VersionService.resolve()`
  is dead code) while `artifacts` is wired via a separate, duplicate
  lookup, and designs a single-overlay-point-per-category approach that
  needs zero changes to `compose.py`/`helm.py` themselves.
- 2026-10-01: Cross-checked against ADR-0019's own `## Remaining Work` list
  directly, per the question "does this design give the full version file
  effect?" Found two items already resolved (ref-keyed remote checkout,
  `fetch: external` rejection — both confirmed by reading the real code)
  and one genuinely missing from the first pass: decision 7's mandatory
  per-application logging requirement, now folded in as design item 4.
  Explicitly scoped out `spec.hash` tamper verification and
  `ProvisionerModel.version` preflight as separate, unrelated follow-ups —
  this design makes pins *have effect*; it does not verify the pins file
  itself hasn't drifted, nor assert installed tool versions.
- 2026-10-01: Added `## Implementation Plan` (Phase 1 shared infrastructure
  through Phase 5 end-to-end proof + docs), grounded in the real existing
  conventions this phase reads directly: `resolve_tenant()`'s shape for
  the new `resolve_version()`, `deployment_resolution.py`'s real
  `get_logger(__name__)`/`log.debug(...)` pattern for the logging helper.
  No code implemented yet — planning only.
- 2026-10-01: **Phase 1 implemented.** `resolve_version()`, `log_pin_applied()`,
  and the `VersionService.resolve()` docstring fix all landed exactly as
  planned, plus 5 new tests. Also fixed an unrelated stale-dist-info dev
  environment issue found while verifying (see Phase 1's own entry above
  for detail). Full check suite green: mypy (121 files), ruff,
  import-linter, pytest (1672 passed, 1 known pre-existing unrelated
  failure).
- 2026-10-01: **Phase 2 implemented.** `resolve_artifact_field()`
  consolidated onto `VersionService.from_model(version).resolve(...)` —
  the duplicate hand-rolled lookup is gone, `artifacts` now shares the
  exact same resolution path the other 3 categories will use in Phases
  3-4. Full check suite green: mypy (121 files), ruff, import-linter,
  pytest (1673 passed, 1 known pre-existing unrelated failure).
- 2026-10-01: **Phase 3 implemented.** `remotes` is the first of the
  three previously-dead categories to actually take effect — a real
  `build run` now materialises a `fetch: strata` git remote at its
  pinned ref instead of the manifest's own declared one, proven
  end-to-end (not just unit-tested) via a faked `git clone`. Extracted
  `_apply_remote_version_pins()` as a standalone function rather than
  inlining the overlay in `build_run()`, matching Phase 4's planned
  `_apply_version_pins()` shape for consistency. Full check suite green:
  mypy (121 files), ruff, import-linter, pytest (1679 passed, 1 known
  pre-existing unrelated failure).
- 2026-10-01: **Phase 4 implemented — all 4 pin categories now have real
  effect.** `images`/`charts` wired via `workload_controller._apply_version_pins()`,
  called from `build_workload_modules()` before a module ever reaches
  `compose.py`/`helm.py` — neither integration needed any change.
  Resolved the previously-open "nonsensical override" question along the
  way (see Remaining Work above) rather than leaving it open: both
  categories hard-skip defensively instead of applying unconditionally,
  since `check_version_pins()`'s own `images` check turned out not to be
  sufficient cover by itself. 9 new tests (7 unit, 2 end-to-end through
  `build_workload_modules()` directly — simpler than the original plan's
  separate `compose.py`/`helm.py` integration tests, since neither needed
  touching). Full check suite green: mypy (121 files), ruff,
  import-linter, pytest (1688 passed, 1 known pre-existing unrelated
  failure).
- 2026-10-01: **Phase 5 implemented — design closed out.** One new
  end-to-end test proves all 3 newly-wired categories (`remotes`,
  `charts`, `images`) apply correctly in the same `build run` when pinned
  simultaneously by a single `Version` document — no interaction bugs
  between the three overlay call sites (`build_controller.py`'s remotes
  overlay, `workload_controller.py`'s charts/images overlay). Updated this
  doc's own `Status` to `implemented`, ADR-0019's `Status` line to
  `implemented`, and `v2-schema-overview.md`'s `version` row to drop the
  "designed but not wired" caveat. Full check suite green: mypy (121
  files), ruff, import-linter, pytest (1689 passed, 1 known pre-existing
  unrelated failure). All 5 phases of this design are now complete.
