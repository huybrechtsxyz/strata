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
  [integration-layer.md](../work/integration-layer.md) — `InfraIntegration`,
  `prepare_namespace()`'s home on the ABC
- [ADR-0025](../decisions/0025-strata-supplies-input-not-source-rewriting.md)
  — why `sync_module_source()` copies a chart verbatim and never rewrites it
- [build-command.md](../work/build-command.md) — the provisioner pipeline this one
  runs alongside, inside the same `build_run()` call


## History

- `module.spec.compose_file`/`module.spec.files` (pass-through compose files / extra verbatim-copied files) are not wired into the workload pipeline yet - no real example uses either today; `ComposeIntegration.prepare_namespace()` raises a clear error rather than silently ignoring `compose_file`.
- STRATA_* template substitution on copied module files (v1's `_apply_templates_to_dir()`) is deliberately not ported - a synced module source is third-party content the deployment doesn't own, and Helm/Compose already have their own native parameter path (`values.yaml`/`${KEY}`); see ADR-0025.
- Build-time validation that every `${var:}`/`${secret:}`/`${feature:}` reference inside a rendered `values.yaml` is actually declared somewhere reachable is not built - a real, if soft, gap: a typo'd token only fails at `helm upgrade` time today, not at `build run` time.
- Two real bugs were found and fixed post-implementation: `sync_module_source()` never created the module directory for a chart-based (registry) source (the primary real-world Helm case); and a raised `IntegrationError` could escape `command_run()`'s error handling and surface as a raw traceback for an unimplemented/unregistered tool type - both are now handled.
