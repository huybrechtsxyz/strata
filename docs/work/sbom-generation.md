# SBOM Generation (CycloneDX) — v2 Design

- Status: Phase 1 implemented (`image`/`compose`/`helm`/`terraform`
  collectors); `ansible`/`deps` deferred, CVE scanning/policies/manifest
  embedding out of scope (see Related Decisions)
- Last updated: 2026-10-01

## Overview

v1's `strata build run` generates a CycloneDX 1.6 SBOM (`sbom.json`) **on
every build, unconditionally** — it is not an opt-in feature. v2 does not
generate one at all today. [ADR-0022](../decisions/0022-strata-build-run.md)
characterized this as having "no confirmed `build run` consumer in either
real repo" and cut it from v2's scope on that basis — **that framing was
too narrow, corrected here after reading v1's real source directly
(2026-10-01)**: the generation mechanism itself is real, automatic,
Tier-1-adjacent behavior (runs every time, same as Terraform/Helm
rendering); what's actually unconfirmed is whether anything *downstream*
(a CI step, a scanner) consumes the resulting `sbom.json` afterward. A
real v1→v2 migration silently loses this artifact today with nothing
flagging the loss.

This doc designs a v2 port scoped to what's cheaply portable now, matching
this repo's standing discipline of shipping a small, real slice before the
larger (and here, genuinely speculative) rest.

## Current Design (v1) — evidence, read directly from `e:\SourcesXYZ\strata`

- **Unconditional phase.** `RunBuildCommand._execute()`
  (`commands/builders/run_build_command.py`) runs 8 phases in a fixed
  order: `platform → terraform → ansible → bicep → compose → helm → sync
  → sbom`. `sbom` has no flag gating it — it always runs, right after
  `sync`, nothing else after it in the main pipeline.
- **`SbomBuilder.build()`** (`builders/sbom_builder.py`) runs 8 registered
  collectors (`_default_collectors()`), concatenates every
  `SbomComponentModel` they return, serialises to CycloneDX 1.6 JSON via
  `cyclonedx-python-lib` (`Bom()`/`Component()`/`JsonV1Dot6` — the only
  class that imports this library, by design, so collectors stay
  dependency-free), writes `{deployment_build_path}/sbom.json`, and
  returns a `SbomReferenceModel` (`path`/`format`/`sha256`/
  `component_count`) that `_write_build_manifest()` embeds into
  `manifest.json`'s `spec.sbom`. `after_build()` re-checks the file
  actually exists on disk — a real, explicit verification step, not just
  trust-the-write.
- **8 collectors** (`builders/sbom/*.py`), each a `BaseSbomCollector`
  (`collect(platform, work_path, deployment_build_path) ->
  list[SbomComponentModel]`, plus a drained `get_warnings()`):

  | Collector                                                    | Reads                                                                                                                                          | Portable to v2 now?                                                                                                                                          |
  | ------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------ |
  | `ContainerImageCollector` (`image`)                          | `platform.spec.modules[].services[].image` (in-memory model)                                                                                   | **Yes** — `ModuleModel.spec.services[].image`, same field                                                                                                    |
  | `ComposeImageCollector` (`compose`)                          | rendered `docker-compose.yml`/`.yaml` under the build dir (file scan)                                                                          | **Yes** — v2 already renders these                                                                                                                           |
  | `HelmChartCollector` (`helm`)                                | `provisioner.source.{chart_name,chart_version,chart_repository}` (in-memory model, Helm-as-provisioner)                                        | **Yes**, different host field — v2 models Helm as a workload `kind: module`, so it's `ModuleModel.spec.source.{chart_name,chart_version}` instead            |
  | `HelmChartFileCollector` (`helm`, same name — results merge) | `Chart.yaml` + its `dependencies[]`, recursively under the build dir (file scan)                                                               | **Yes** — v2 already materialises these via `sync_module_source()`                                                                                           |
  | `TerraformProviderCollector` (`terraform`)                   | `terraform { required_providers {...} }` blocks in every `*.tf` under the build dir, parsed via `python-hcl2`                                  | **Yes**, needs a new dependency (`python-hcl2`)                                                                                                              |
  | `TerraformModuleCollector` (`terraform-module`)              | `module "x" { source = ... }` blocks, same `*.tf` files/parser                                                                                 | **Yes**, same new dependency, effectively free once the provider collector's parse pass exists                                                               |
  | `AnsibleCollectionCollector` (`ansible`)                     | `requirements.yml` collections/roles                                                                                                           | **No** — v2 has no Ansible integration/provisioner at all (confirmed Tier 2, "not proven depended upon", `/memories/repo/v1-consumer-usage.md`)              |
  | `DependencyFileCollector` (`deps`)                           | app-level lockfiles (`requirements.txt`, `pyproject.toml`, `uv.lock`, `package-lock.json`, `go.sum`, via a pluggable `LockfileParserRegistry`) | **Defer** — by far the largest single collector (a whole parser-registry subsystem), and orthogonal to any provisioner/workload (scans arbitrary repo paths) |

- **Also out of scope for this doc, confirmed real but separate concerns**:
  CVE scanning (`CveAuditResultModel`, trivy/grype subprocess integration),
  `sbom-ignore.yaml`/allowlist (`SbomIgnore*RuleModel`), the four
  `sbom_*` policy types (part of v1's generic 18-type `PolicyModel` —
  already flagged in [gap_fit_v1.md](gap_fit_v1.md) gap #7 as unstarted,
  and [tenant-zone-policy.md](tenant-zone-policy.md) already established
  the "model the one real check directly, not the generic engine"
  precedent any future policy work here should follow), `--ai` CVE
  analysis (needs an AI integration v2 doesn't have), a standalone
  `strata sbom scan`/`inventory` CLI surface (v1's `scan()`/
  `scan_inventory()`/`render_inventory()` — no real usage evidence
  gathered for these independent of `build run` itself).
- **New v1 dependencies this would bring in**: `cyclonedx-python-lib>=7.0,<12`
  (plus its `json-validation` extra for schema validation, see Validation
  below), `packageurl-python>=0.11,<2` (both unavoidable for any CycloneDX
  output at all), `python-hcl2>=8.0` (only if the Terraform collectors are
  included — see Open Questions). **Confirmed v1 never actually
  schema-validates its own SBOM output** (`SbomBuilder.after_build()` only
  checks the file exists) — see Validation below for what v2 does instead.

## Design (v2 proposal)

### Scope: Phase 1 covers 4 of v1's 8 collectors

`image`, `compose`, `helm` (both variants, same collector name so results
merge), and `terraform` (both provider and module collectors, sharing one
HCL parse pass) — every one of them either reads data v2 already has
resolved in memory, or scans files v2's own build pipeline already
materialises. `ansible` stays out (nothing to collect from). `deps`
(lockfiles) is deliberately deferred to its own later phase — it is not
provisioner/workload-shaped at all, and is the single largest chunk of new
code in v1's implementation (a whole pluggable parser registry).

### Where it hooks into `build_run()`

A new, unconditional step **after** the workload-modules loop, immediately
before `return diagnostics` in
[`build_controller.py`](../../src/strata/controllers/build_controller.py)
— matching v1's own "sbom runs dead last, after every other builder"
ordering. Not gated behind a flag, matching v1's real behavior (every
build produces one) rather than inventing an opt-out v1 never had.

```python
    for namespace in graph.namespaces.values():
        build_workload_modules(...)

    if not dry_run:
        write_sbom(build_path, graph, index)   # new

    return diagnostics
```

`dry_run` skips it entirely (nothing to scan yet — matches every other
`dry_run` branch in this function skipping filesystem writes, and v1's own
`scan: dry_run → "[DRY-RUN] Would write SBOM..."` early-return).

### Where collection reads from — no per-provisioner/per-module bookkeeping needed

v1's collectors already run as a single pass over one deployment's whole
`deployment_build_path` for every file-based collector (`compose`,
`HelmChartFileCollector`, `terraform`) — they `rglob()` the entire build
directory rather than being threaded through the provisioner/workload
loops individually. v2's `build_path` is the exact same shape (one
deployment's entire rendered output, single root) — so the file-based
collectors can run as a single post-pass `rglob()` over `build_path`
*as a whole*, with **zero new wiring inside the provisioner loop or
`build_workload_modules()`** — a real simplification versus v1, which
still has to pass `deployment_build_path` through every collector call
individually. Only the two declarative collectors (`image`, `helm`-from-
model) need the resolved models — and `graph.namespaces` (already
assembled by `build_resolved_workspace_graph()`) already holds every
namespace's modules, so `index`/`graph` (both already in scope at the
`build_run()` call site) are the only inputs those two collectors need.

### Component model — port `SbomComponentModel`/`SbomReferenceModel` verbatim

Same discipline as every other kind in this repo ("port v1's model
field-by-field, verify against real usage before trusting the port"): new
`strata/models/sbom_model.py` with `SbomComponentModel`
(`component_type`/`name`/`version`/`purl`/`properties`/`source_collector`)
and `SbomReferenceModel` (`path`/`format`/`sha256`/`component_count`) —
the fields v1 actually uses, not the CVE/ignore-rule models (out of scope
here, see above).

### Extensibility — a new collector is one small class, zero core-repo changes for a third party

**v1's own answer to this (`.strata/collectors.yaml` + a bespoke
importlib-by-path `CollectorPluginLoader`) is not what v2 should copy.**
v2 already has a real, proven answer to the exact same shaped problem —
[ADR-0021 D10](../decisions/0021-integration-layer.md) built
`strata.integrations.registry` as a lean `dict[str, tuple[module_path,
class_name]]` of built-ins, lazily imported, PLUS discovery via Python's
standard `importlib.metadata.entry_points()` so a third-party package
registers a custom `Integration` class through its own `pyproject.toml`
(`[project.entry-points."strata.integrations"]`) with **zero changes to
this repo at all**. This design reuses that exact mechanism for SBOM
collectors instead of inventing a second, different plugin system:

- New package `strata/integrations/sbom_collectors/` (collectors sit at
  the `integrations` layer — same place `strata.integrations.registry`
  already lives, and the layering rule that matters,
  `controllers > integrations > models`, is respected: a collector reads
  already-resolved `models` objects and plain `Path`s, never a
  `controller`).
  - `base.py` — `SbomCollector` ABC: one method,
    `collect(graph: ResolvedWorkspaceGraph, index: DocumentIndex,
    build_path: Path) -> CollectorResult` (`CollectorResult` =
    `components: list[SbomComponentModel]` + `diagnostics: list[Diagnostic]`
    for warnings — see below). Deliberately narrower than v1's
    `BaseSbomCollector` (`work_path`/`deployment_build_path` collapse to
    v2's single `build_path`; no separate `get_warnings()` drain method,
    since `Diagnostics` already is v2's one shared findings channel).
  - `image_collector.py`, `compose_collector.py`, `helm_collector.py`,
    `terraform_collector.py` — one small class each, matching v1's
    one-file-per-collector shape (keeps a diff that adds a 5th collector
    a pure addition, never an edit to an existing file).
  - `registry.py` — `_KNOWN: dict[str, tuple[str, str]]` (built-ins) +
    `ENTRY_POINT_GROUP = "strata.sbom_collectors"`, `_resolve_class()`/
    `list_collectors()` copied structurally from
    `strata.integrations.registry._resolve_class()` — same collision rule
    (a built-in name can't be shadowed by a plugin), same
    `CollectorNotFoundError` shape.
- **Adding a built-in collector for a new technology** (e.g. a future
  `npm`/`cargo`/`bicep` source): one new class implementing `SbomCollector`
  + one new `_KNOWN` entry. Nothing else changes — `write_sbom()` iterates
  `registry.list_collectors()`, never a hardcoded list.
- **Adding a private/third-party collector** (an org-specific artifact
  type strata itself will never ship): a separate installable package
  declaring `[project.entry-points."strata.sbom_collectors"]` pointing at
  its own `SbomCollector` subclass — discovered automatically the moment
  it's installed alongside `xyz-strata`, identical to how a custom
  `Integration` already works today. No `.strata/`-local YAML config, no
  bespoke loader to maintain.

**Short example — adding an npm `package-lock.json` collector:**

```python
# strata/integrations/sbom_collectors/npm_collector.py
class NpmLockfileCollector(SbomCollector):
    def collect(self, graph, index, build_path: Path) -> CollectorResult:
        components, diagnostics = [], []
        for lockfile in build_path.rglob("package-lock.json"):
            for name, version in _parse_npm_lockfile(lockfile):
                components.append(SbomComponentModel(
                    component_type="library", name=name, version=version,
                    purl=f"pkg:npm/{name}@{version}", source_collector="npm",
                ))
        return CollectorResult(components=components, diagnostics=diagnostics)
```

```python
# registry.py — the only core-repo change for a BUILT-IN collector
_KNOWN["npm"] = ("strata.integrations.sbom_collectors.npm_collector", "NpmLockfileCollector")
```

A **third-party** package needs no core-repo change at all — just its own
`pyproject.toml`:

```toml
[project.entry-points."strata.sbom_collectors"]
npm = "acme_strata_plugins.npm_collector:NpmLockfileCollector"
```

`write_sbom()` never changes either way — it already iterates
`registry.list_collectors()`.

### Validation — in-process schema check, plus external-tool compatibility by construction

**Real finding worth calling out: v1 never schema-validates its own SBOM
output.** Read `SbomBuilder.build()`/`after_build()` directly — the only
check after serialisation is `sbom_path.exists()`. There is no call
anywhere to `cyclonedx-python-lib`'s own bundled validator. This design
closes that gap rather than porting the omission:

1. **In-process schema validation, before the file is written.**
   `cyclonedx-python-lib` ships `cyclonedx.validation.json.JsonValidator`/
   `JsonStrictValidator` (confirmed present in v1's installed `.venv` —
   `validation/json.py`), which validates a JSON string against the real,
   bundled official CycloneDX schema for a given `SchemaVersion` via
   `jsonschema`'s `Draft7Validator` — the same library already building
   the BOM, so this is a cheap extra call, not a new dependency axis.
   Needs the library's `json-validation` extra (pulls in `jsonschema` +
   `referencing`) — add as `cyclonedx-python-lib[json-validation]>=7.0,<12`
   rather than the bare package. `write_sbom()` calls
   `JsonStrictValidator(SchemaVersion.V1_6).validate_str(bom_json)` right
   after `_build_cyclonedx_json()` and before the write — a non-`None`
   result is a hard failure (`raise`, not a `Diagnostic`), since an
   invalid BOM at this point means a real bug in this code (a library/
   schema-version mismatch, a malformed `purl`), never a user-facing
   validation concern. Should be unreachable in practice (the library's
   own object model is constructed to only produce valid output) — this
   is insurance, the same category as `after_build()`'s existence check,
   not expected to ever actually fire.
2. **External-tool validation needs no bespoke integration at all.**
   `sbom.json` is a real, spec-compliant CycloneDX 1.6 JSON document by
   construction (built through the official library's own object model,
   not hand-rolled) — that is the entire point of emitting a standard
   format rather than a strata-specific one. Any CycloneDX-consuming tool
   (the OWASP `cyclonedx-cli validate`/`cyclonedx-cli analyze` binary,
   `trivy sbom`, `grype sbom:...`, Anchore Syft, OWASP Dependency-Track's
   upload API, GUAC) can consume this file directly, today, with zero
   strata-side work — nothing to design here beyond "keep emitting a
   real CycloneDX document," which Phase 1 already guarantees.
3. **Not proposed for Phase 1, noted as a cheap future option:** a thin
   `strata sbom validate <file>` command shelling out to the real,
   external `cyclonedx-cli` binary via the same `run_command()` pattern
   `TerraformIntegration`/`HelmIntegration` already use — useful only if a
   real consumer wants the canonical CLI tool's own validation (which
   also checks things the bundled JSON-schema check doesn't, e.g.
   cross-field semantic rules) rather than, or in addition to, the
   in-process check above. No real usage evidence gathered for this yet —
   matches this doc's own "don't build the standalone CLI surface without
   evidence" call for `strata sbom scan`/`inventory` above.

### Serialisation — reuse v1's exact `cyclonedx-python-lib` pattern

One new function, `_build_cyclonedx_json(components) -> str`, isolated to
the single module that imports `cyclonedx-python-lib` (same isolation
principle v1's own docstring states — collectors stay dependency-free).
`Bom()` + one `Component(type, name, version, purl, properties)` per
collected item + `JsonV1Dot6` output — identical shape to v1, no reason to
redesign a format CycloneDX itself already fixes. Lives in
`strata/controllers/sbom_controller.py` alongside `write_sbom()` — the
only two functions in this design that import `cyclonedx-python-lib`
directly; every collector stays free of it, same isolation v1 already
established.

### Output — write the file; do NOT invent a manifest to embed it in

v1 embeds `SbomReferenceModel` into `manifest.json` (`DeploymentManifestModel.
spec.sbom`). v2 has no equivalent manifest yet — `ConfigurationSpecModel`'s
own docstring still lists `deployment.manifest`/`outputs` as unmodelled
(gap #7), and `docs/work/audit-trail.md`'s own Layer 2 effort is the
closest existing thing, deliberately scoped elsewhere. **This is exactly
the shape of gap the user's recalled v1 defect ("the SBOM wasn't actually
saved") warns against** — generating a reference nobody keeps is as good
as not generating anything. So: write `sbom.json` directly to
`build_path / "sbom.json"` (v1's exact filename, reusing the already-
established `build_path` root instead of a per-deployment subdirectory v2
doesn't have), and **verify the write** immediately after
(`sbom_path.is_file()`, raising if not) — porting v1's `after_build()`
check as a hard failure, not a silent trust. No `SbomReferenceModel`
written anywhere else yet; that's wired up whenever the manifest feature
itself is designed (gap #7), at which point it reads the hash straight off
this already-written file — same conclusion
[version-pin-overlay.md](version-pin-overlay.md) and
[audit-trail.md](audit-trail.md) each reached for their own "write now,
reference later" splits.

### Collector warnings — surface as `Diagnostics`, not a separate message channel

v1's collectors accumulate free-text warnings (floating image tags,
unparseable files) drained via `get_warnings()` and printed conditionally
(`quiet`/`verbose` flags). v2 has no such ad hoc message channel — every
other `build_run()` finding already flows through the returned
`Diagnostics` (the `resolve=True` validation path is the direct
precedent). Each collector warning becomes one `Diagnostic` (`Severity.
WARNING`, not `ERROR` — a floating tag or an unparseable file is
informational, matching v1's own non-fatal treatment), returned as part of
that collector's own `CollectorResult` (see Extensibility above) and
merged by `write_sbom()` into the same `Diagnostics` instance `build_run()`
already returns, rather than a parallel `messages: list[str]` v2 doesn't
have anywhere else.

## Implementation Plan

- [x] **Phase 1 — `image`/`compose`/`helm`/`terraform` collectors, wired
  into `build_run()`, built on a pluggable registry.** Done 2026-10-01.
  - New `strata/models/sbom_model.py`: `SbomComponentModel`,
    `SbomReferenceModel` (ported fields only, per above).
  - New `strata/integrations/sbom_collectors/` package: `base.py`
    (`SbomCollector` ABC + `CollectorResult`), `image_collector.py`,
    `compose_collector.py`, `helm_collector.py` (model + `Chart.yaml` file
    variants merged in one class, both tagged `source_collector="helm"`),
    `terraform_collector.py` (provider + module blocks, one HCL parse
    pass — the "bundle vs. split" open question resolved in favor of
    bundling, as this doc originally leaned), `registry.py` (`_KNOWN`
    dict + `strata.sbom_collectors` entry point group, structurally
    copied from `strata.integrations.registry`).
  - New `strata/controllers/sbom_controller.py`: `write_sbom(build_path,
    graph, index) -> Diagnostics` — resolves every namespace's module
    references into a flat `list[ModuleModel]` once (via
    `workload_controller.resolve_module()`, so no collector ever touches
    `DocumentIndex` itself — ADR-0003 layering), iterates
    `registry.list_collectors()` (never a hardcoded list), merges every
    `CollectorResult`, calls `_build_cyclonedx_json()`, runs
    `JsonStrictValidator` before writing, writes `sbom.json`, verifies the
    write with a hard `RuntimeError` on either failure (schema validation
    or a missing file afterward) — exactly the class of defect this doc's
    Remaining Work traces to the recalled historical v1 bug.
  - `build_controller.py`: `write_sbom()` called right before `return
    diagnostics`, skipped entirely when `dry_run` (an `on_step` message
    either way).
  - `pyproject.toml`: added `cyclonedx-python-lib[json-validation]>=7.0,<12`,
    `packageurl-python>=0.11,<2`, `python-hcl2>=8.0`.
  - Tests: 21 pure-function tests for `sbom_utils.py`, 8 registry tests
    (mirroring `test_integrations_registry.py`'s built-in/entry-point/
    collision coverage, including `list_collectors()`), 22 collector unit
    tests (image/compose/helm/terraform — pinned images, floating-tag
    warnings, dedup, parse-error warnings, local-module skip), 5
    `write_sbom()` controller tests, and 2 end-to-end `build_run()` tests
    (one asserting a real `sbom.json` parses as valid CycloneDX JSON and
    contains both an image and a chart component, one confirming
    `dry_run` writes nothing) — 58 new tests total. Full check suite
    green: mypy (131 files), ruff, import-linter (1 kept, 0 broken),
    pytest (1747 passed, same 1 known pre-existing unrelated failure).
  - **Real bug found and fixed while writing the end-to-end test**:
    `HelmCollector._find_chart_files()`'s `_SKIP_DIRS` prune originally
    checked the full *absolute* path's parts against `_SKIP_DIRS` —
    `build_path` (the real scan root) is routinely named literally
    `build` (`layout.build_dir()`'s own convention), itself one of the
    skip-dir names, so every chart was silently skipped. Fixed to check
    only the path components *relative to the scan root*; locked in with
    a dedicated regression test
    (`test_collect_finds_chart_yaml_even_when_the_scan_root_is_named_build`).

## Related Decisions

- [ADR-0022](../decisions/0022-strata-build-run.md) — `build run`
  orchestration; its "SBOM ... cut, no confirmed consumer" framing is
  corrected by this doc (generation is real/automatic; only downstream
  consumption is unconfirmed).
- [ADR-0021](../decisions/0021-integration-layer.md) D10 — the
  entry-point plugin precedent (`strata.integrations`) this doc's
  `strata.sbom_collectors` registry is structurally copied from, rather
  than inventing a second, different extensibility mechanism.
- [docs/work/gap_fit_v1.md](gap_fit_v1.md) gap #7 — the still-open
  `policies`/deployment-manifest subtree this doc deliberately does not
  reach into (SBOM policies, CVE scanning, manifest embedding).
- [docs/work/tenant-zone-policy.md](tenant-zone-policy.md) — the "model
  the one real check directly, don't port the generic 18-type policy
  framework" precedent any future `sbom_*` policy work should follow.
- [docs/work/audit-trail.md](audit-trail.md) — the sibling "write the
  artifact now, embed its reference in a manifest later" split this doc
  reaches the same conclusion as.

## Remaining Work / Open Questions

1. ~~Bundle the Terraform collectors into Phase 1, or split them into
   their own Phase 2?~~ **Resolved 2026-10-01: bundled.** Implemented as
   one `terraform_collector.py`/`TerraformCollector`, one HCL parse pass
   producing both `terraform` (providers) and `terraform-module`
   components — matches v1 shipping them together and this doc's own
   original leaning.
2. **`deps` (lockfile) collector phase, when?** No trigger defined yet —
   unlike the tenant-zone-policy doc's explicit un-defer triggers, this
   doc doesn't propose one. Revisit only if a real consumer's build
   actually needs application-level dependency visibility (distinct from
   infra/container-level, which Phase 1 already covers).
3. **Historical v1 "SBOM not saved" defect — root cause not yet traced.**
   Current v1 HEAD's `build()`/`after_build()`/`_write_build_manifest()`
   wiring looks correct on inspection; the real defect is most likely
   either already fixed upstream (candidate commits `9705ecde`/`f8f5f842`,
   not yet read in detail) or environmental (this repo's own memory
   already flags haven vs. config-deploy using different build-output
   paths, `build/` vs `.strata/build/`, which could mean a CI
   `upload-artifact` step simply isn't pointed at wherever `sbom.json`
   actually lands). Not required to block this design (v2's own output
   path is `build_path` directly, no ambiguity), but worth closing out if
   this is picked up, so a specific regression test can be written for
   whatever the real defect turns out to be.
4. **Floating-tag / parse-error warnings — `Severity.WARNING` always, or
   configurable?** v1 has no severity concept at all for these (just a
   suppressible message). Proposed default here (always `WARNING`,
   never gates `diagnostics.ok`) is a new, not-yet-confirmed judgment
   call — revisit if a real consumer wants a floating `:latest` tag to be
   build-breaking.

## Changelog

- 2026-10-01: **Phase 1 implemented.** All 4 collectors
  (`image`/`compose`/`helm`/`terraform`) wired into `build_run()` via a
  new `strata/integrations/sbom_collectors/` package + `strata.
  sbom_collectors` entry-point registry + `strata/controllers/
  sbom_controller.py`'s `write_sbom()`. Resolved Open Question 1 in
  favor of bundling the Terraform provider/module collectors into one
  class/one HCL parse pass. Found and fixed a real bug while writing the
  end-to-end test: `HelmCollector`'s `_SKIP_DIRS` prune checked the
  absolute scan-root path's own parts, silently skipping every chart
  under a `build_path` literally named `build` (the real, routine case) —
  fixed to check only path components relative to the scan root, with a
  dedicated regression test. 58 new tests; full check suite green: mypy
  (131 files), ruff, import-linter (1 kept, 0 broken), pytest (1747
  passed, same 1 known pre-existing unrelated failure).
- 2026-10-01: Created. Corrects ADR-0022's "SBOM has no confirmed `build
  run` consumer" framing after reading v1's real source directly — the
  mechanism is unconditional/automatic, only downstream consumption is
  unconfirmed. Scopes a v2 Phase 1 to the 4 of 8 v1 collectors that are
  cheaply portable today (`image`/`compose`/`helm`/`terraform`), defers
  `ansible` (no v2 integration exists to collect from) and `deps`
  (largest single subsystem, no defined trigger yet) indefinitely, and
  explicitly keeps CVE scanning/policies/manifest-embedding/`--ai`
  analysis out of scope as separate, already-tracked concerns (gap #7,
  tenant-zone-policy.md, audit-trail.md). No implementation yet.
- 2026-10-01: Reviewed for extensibility and validation, per direct
  request. Two real findings changed the design: (1) v1's own
  per-repo-local `.strata/collectors.yaml` + bespoke importlib loader is
  NOT what v2 should copy — v2 already has a proven, idiomatic answer to
  the identical problem in `strata.integrations.registry` (ADR-0021 D10:
  built-in dict + `importlib.metadata.entry_points()` for third-party
  packages), so this doc now reuses that exact mechanism
  (`strata.sbom_collectors` entry-point group) instead of inventing a
  second plugin system. Collector architecture changed from "four private
  functions in one controller module" to a proper
  `strata/integrations/sbom_collectors/` package (one small class per
  collector, a `registry.py` structurally copied from the integrations
  one) — adding a new technology source is now one new file + one
  registry entry (built-in) or a separate installable package declaring
  an entry point (third-party), never an edit to existing collectors.
  (2) Checked whether v1 validates its own SBOM output at all — it
  doesn't (`after_build()` only checks the file exists, no schema check
  anywhere). Added a real validation design: `cyclonedx-python-lib`'s own
  bundled `JsonStrictValidator` runs in-process against the official
  CycloneDX schema right before the file is written (new
  `[json-validation]` extra dependency), and the written file is a
  standard, spec-compliant CycloneDX 1.6 document by construction, so any
  external tool (`cyclonedx-cli`, trivy, grype, Dependency-Track, Syft)
  can consume it directly with zero bespoke strata-side integration — a
  thin `strata sbom validate` wrapper around the real `cyclonedx-cli`
  binary is noted as a cheap, evidence-gated future option, not part of
  Phase 1.
