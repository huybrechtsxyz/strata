# Workload Pipeline (Compose/Helm modules) — Design

- Status: current — both halves implemented and end-to-end tested
  (`HelmIntegration.prepare_namespace()`, `ComposeIntegration.prepare_namespace()`,
  `workload_controller.py`, wired into `build_controller.build_run()`).
  `config/`'s example solution builds both a Helm module (authentik) and a
  merged Compose namespace (portainer) end to end.
- Last updated: 2026-09-25

## Overview

ADR-0022 D5-D7 found this while checking how v1 actually shaped its input:
`Namespace.spec.modules` (Compose/Helm workloads) is a second, disconnected
pipeline from the provisioner one `build-command.md` describes —
`ModuleReferenceModel`, never `ProvisionerModel`/`ProvisioningStepModel`,
and its integration resolves from a bare `module.spec.type` string, never a
named `integration:` binding. This doc tracks that second pipeline's real
build-out, the same way `build-command.md` tracks the provisioner one.

## Current Design

```
build_run(context, deployment_name, build_path)          # build_controller.py
  ├─ ...the provisioner loop (see build-command.md)...
  └─ for namespace in graph.namespaces.values():          # already resolved onto ResolvedWorkspaceGraph
       build_workload_modules(index, root, remotes, namespace, resolved, build_path)  # workload_controller.py

build_workload_modules(index, root, remotes, namespace, resolved, build_path)
  ├─ for reference in namespace.spec.modules:
  │    module = resolve_module(index, reference)                       # PlatformKind.MODULE lookup
  │    module_dir = build_path / namespace.meta.name / reference.name   # keyed by REFERENCE name, not module.meta.name
  │    sync_module_source(root, module_dir, module.spec.source, remotes)  # materialises a git-based (local) chart; no-op for a registry chart
  │    group by module.spec.type into ResolvedModule(reference, module, source_path=module_dir)
  └─ for module_type, group in by_type.items():
       integration = resolve_module_integration(index, module_type)     # D5: bare type, no named binding
       integration.prepare_namespace(namespace, group, resolved=resolved)

HelmIntegration.prepare_namespace(namespace, modules, *, resolved)        # helm.py
  └─ for item in modules:
       values = _render_values(item.module)     # env/persistence per service, module.spec.configuration merged in
       if values: write item.source_path/values.yaml
       write item.source_path/meta.yaml          # releaseName/namespace, chart coordinates if registry-based

ComposeIntegration.prepare_namespace(namespace, modules, *, resolved)     # compose.py
  ├─ services, volumes = _render_namespace_services(namespace.meta.name, modules)  # merges the WHOLE group
  └─ if services: write modules[0].source_path.parent/docker-compose.yml    # ONE shared file, unlike Helm
```

Every line above is real, built code today — not a sketch.

### Corrections found while grounding this against v1's real `HelmBuilder`

- **Module build directories are keyed by the reference's `name`, not
  `module.meta.name`** — ADR-0022 D6's own pseudocode passed a bare
  `list[ModuleModel]` into `prepare_namespace()`, discarding the reference.
  v1's real `HelmBuilder` keys `module_dir`/`releaseName` off the *loaded
  module's own* `meta.name` — but `ModuleReferenceModel`'s own docstring
  explicitly anticipates the same Module document being attached to a
  namespace twice under different reference names, which v1's convention
  would silently collide on (same `module_dir`, same default `releaseName`,
  same live Helm release fighting itself). Fixed here: `ResolvedModule`
  carries both `reference` and `module`; directory placement and
  `releaseName`'s default both key off `reference.name` (guaranteed unique
  within its namespace — `NamespaceSpecModel.validate_namespace_spec()`),
  never `module.meta.name`.
- **Chart materialisation is a controller-layer concern, not
  `HelmIntegration`'s** — mirrors the exact split `sync_source()`/
  `resolve_remote()` already establish for the provisioner path (ADR-0021
  D2: an `Integration` never touches `DocumentIndex`/remotes directly, and
  `strata.integrations` sits below `strata.controllers` in the
  import-linter layering, so it could not import `resolve_remote()` even if
  the design wanted it to). `workload_controller.build_workload_modules()`
  calls the new `sync_module_source()` *before* handing a fully-materialised
  `ResolvedModule.source_path` to `prepare_namespace()`.
- **`sync_module_source()` is not `sync_source()` reused as-is** — a new,
  sibling function in `source_sync.py`. Two real differences: (1) a
  chart-based `source` (`chart_name` set) is a silent no-op here, not an
  error — unlike provisioners (D3: "no real provisioner example uses
  chart-based sourcing"), a registry chart pull is Helm's real primary use
  case for a *module* (`SourceModel`'s own docstring example is exactly
  this). (2) destination is always the given `module_dir`, never derived
  from `source.source_path`/`.target_path` — `sync_source()`'s
  repo-mirroring convention exists to preserve sibling *provisioners'*
  relative composition (D3), which a self-contained Helm chart never needs,
  and reusing it verbatim would collide for the same "attached twice" case
  above (both attachments share the same `source.source_path`).
- **Deploy-time value substitution is genuinely out of scope for `build
  run`, not merely deferred** — confirmed against ADR-0023's own
  value-substitution table: Helm's `${var:}`/`${secret:}`/`${feature:}`
  tokens resolve at *deploy* time (secrets via `--set-string`, never
  written to disk; vars/features via a rewritten file), never at build
  time. `_render_values()` therefore copies `service.environment[].value`
  verbatim — this is not a "Phase 2 will resolve these" gap the way
  Terraform's `dns`/`networks` tokens are (ADR-0023 Phase 2c); there is no
  future build-time phase that resolves them at all. The only thing left
  genuinely unbuilt is `strata deploy run` itself, which does not exist yet.

## Related Decisions

- [ADR-0022](../decisions/0022-strata-build-run.md) — D5-D7: the workload
  pipeline's existence, grouping and `prepare_namespace()` signature
- [ADR-0023](../decisions/0023-build-output-rendering.md) — value
  substitution's differing kind/timing per output (the table this doc's
  "out of scope" correction above is grounded in)
- [ADR-0021](../decisions/0021-integration-layer.md) /
  [integration-layer.md](integration-layer.md) — `InfraIntegration`,
  `prepare_namespace()`'s home on the ABC
- [ADR-0025](../decisions/0025-strata-supplies-input-not-source-rewriting.md)
  — why `sync_module_source()` copies a chart verbatim and never rewrites it
- [build-command.md](build-command.md) — the provisioner pipeline this one
  runs alongside, inside the same `build_run()` call

## Remaining Work / Open Questions

- `module.spec.compose_file` (pass-through: copy an external compose file
  verbatim instead of generating one from `spec.services`, at most one per
  namespace, mutually exclusive with any generative module in the same
  group) — not implemented; `ComposeIntegration.prepare_namespace()` raises
  a clear `IntegrationError` (→ `UsageError`) if a module sets it, rather
  than silently ignoring it. No real example uses it yet.
- `module.spec.files` (`ModuleFileModel` — extra files copied verbatim into
  the module's build output directory, with `@repo/` cross-repo references
  and glob support) is not wired into `sync_module_source()`/
  `build_workload_modules()` yet, for either Compose or Helm — v1's real
  builders copy these alongside the chart/compose file; v2's equivalent
  pass has not been built.
- ~~STRATA_* template substitution on copied module files~~ (v1's
  `_apply_templates_to_dir()`) — **decided against, not a gap**; now
  [ADR-0025](../decisions/0025-strata-supplies-input-not-source-rewriting.md).
  A synced module source (a fetched chart, a vendored compose service) is
  third-party content the deployment doesn't own, and Helm/Compose already
  have their own native parameter path (`values.yaml`/`${KEY}`) — strata
  supplies input to IaC, it does not rewrite IaC source.
- Build-time validation that every `${var:}`/`${secret:}`/`${feature:}`
  reference inside a rendered `values.yaml` is actually declared somewhere
  reachable (v1's `_validate_expr_refs()`, ADR-0075) is not built — v2 has
  no `resolve_expr_tokens()`/token-scanning utility at all yet (also
  flagged as missing in ADR-0023's own Remaining Work, for Terraform's
  `provisioner.backend`/`.configuration`). A real, if soft, safety gap:
  today a typo'd `${secret:DB_PASWORD}` only fails at `helm upgrade` time,
  not at `build run` time.
- `strata deploy run` (the command that would actually resolve these tokens
  and call `helm upgrade`) does not exist.

## Changelog

- 2026-09-24: Created. Helm's half of ADR-0022 D5-D7 implemented and
  end-to-end tested: `ResolvedModule` (`resolved_context.py`),
  `InfraIntegration.prepare_namespace()` base method (`capabilities.py`),
  `HelmIntegration.prepare_namespace()`/`_render_values()`/`_render_meta()`
  (`helm.py`), `sync_module_source()` (`source_sync.py`),
  `resolve_module_integration()`/`_auto_bind_config()` (refactored out of
  `resolve_integration()`, `integration_resolution.py`), and the new
  `workload_controller.py` (`resolve_module()`, `build_workload_modules()`),
  wired into `build_controller.build_run()`'s loop. A real
  workspace/namespace/module fixture materialises a local chart directory
  and writes real `values.yaml`/`meta.yaml` output end to end
  (`test_build_run_renders_helm_workload_modules`). Full check suite green:
  948/948 tests passing.
- 2026-09-24: Post-implementation review found and fixed two real bugs,
  both confirmed by manually reproducing the crash before patching:
  (1) `sync_module_source()` never created `module_dir` for a chart-based
  (registry) source — the primary real-world Helm use case — because its
  early-return skipped the `mkdir` the git-based branch happened to do
  incidentally; `HelmIntegration.prepare_namespace()`'s own unit tests
  never caught this because they manually pre-created the directory in
  their own fixtures rather than going through the real caller. Fixed by
  moving the `mkdir` before the early-return. (2) `IntegrationError`
  (`strata.integrations.errors`, a plain `Exception`, not a `StrataError`)
  could escape `command_run()`'s `except StrataError` entirely and surface
  as a raw traceback — reachable today via a `compose`-typed module
  (`ComposeIntegration.prepare_namespace()` isn't implemented, so it hits
  the base method's raise) or a provisioner/module naming an unregistered
  tool type (e.g. `ansible`). Fixed by wrapping every `registry.get()`
  call (`integration_resolution.py`'s new `_construct()`) and both
  `prepare()`/`prepare_namespace()` call sites (`build_controller.py`/
  `workload_controller.py`) to translate `IntegrationError` into
  `UsageError`. 8 new regression tests added. Full check suite green:
  952/952 tests passing.
- 2026-09-25: `ComposeIntegration.prepare_namespace()` implemented
  (`compose.py`: `_render_namespace_services()`, `_render_mounts()`,
  `_resolve_depends_on()`, `_render_healthcheck()`), ported from v1's real
  `ComposeBuilder._render_module_services()` — same merge-into-one-file
  behaviour, same `{module}-{service}` prefixing, same `@module/service`
  cross-module `depends_on` resolution, adapted to v2's collapsed
  `ModuleServiceEnvironmentModel.value` schema. `compose_file` pass-through
  deliberately not ported (no real example uses it) — raises a clear
  `IntegrationError` instead of silently doing nothing. The stale
  `test_build_workload_modules_raises_usage_error_for_a_type_without_prepare_namespace`
  test (written when `compose` was the only reachable example of "a
  registered type without `prepare_namespace()`") was replaced: a real
  end-to-end Compose test, plus a new `test_integrations_capabilities.py`
  test that guards the ABC's own base-raise behaviour directly instead of
  depending on which concrete type currently happens to lack an override.
  `config/`'s example solution now builds a real, merged
  `hearth/docker-compose.yml` (portainer) alongside the Helm-rendered
  `hearth/authentik/` output and the Terraform provisioner's output, fully
  end to end with `strata build run prd-deployment --path config` — no
  scratch copy needed (the `infra` remote was also switched from a
  placeholder GitHub URL to a real local stand-in, `config/vendor/infra/`).
  29 new tests. Full check suite green: 966/966 tests passing.
