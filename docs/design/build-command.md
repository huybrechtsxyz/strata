# `strata build run` Command — Design

- Status: `strata build run` exists and is end-to-end tested
  (`build_command.py` → `build_controller.build_run()`); only the
  Compose/Helm workload pipeline (ADR-0022 D5-D7) remains.
- Last updated: 2026-09-24

## Overview

`strata build run` will render a workspace's provisioners/modules into
on-disk artifacts (Terraform `.tfvars.json`, Helm `values.yaml`, Compose
`docker-compose.yml`, ...) without executing anything — decided in
[ADR-0022](../decisions/0022-strata-build-run.md) (orchestration) and
[ADR-0023](../decisions/0023-build-output-rendering.md) (what gets
rendered). Like [validate-command.md](validate-command.md) and
[integration-layer.md](integration-layer.md), `src/strata/commands/build_command.py`
now exists and is registered in `cli.py` as `strata build run DEPLOYMENT` —
thin glue over `strata/controllers/build_controller.py` (the orchestrator
itself), matching `validate_command.py`/`values_command.py`'s shape exactly:
no business logic in the command body, `command_run()` for lifecycle,
`open_solution(path).require_valid()` for the precondition.

## Current Design

```
strata build run DEPLOYMENT [--path PATH] [--build-path PATH]
  └─ build_command.py: build_run_command()
       ├─ context = open_solution(path).require_valid()
       ├─ target = build_path or layout.build_dir(context.root, deployment)  # '<root>/build/<deployment>' by default
       └─ diagnostics = build_run(context, deployment, target)          # build_controller.py — the orchestrator

build_run(context, deployment_name, build_path)
  ├─ resolve_deployment(context, deployment_name)        # value_controller.py — shared with resolve_values()/build_time_keys()
  ├─ keys = build_time_keys(context, deployment_name)    # value_controller.py — variables/features only, never secrets
  ├─ resolved = resolve_values(context, deployment_name, keys)
  ├─ workspace = index.get(WORKSPACE, deployment.spec.workspace)
  ├─ graph = build_resolved_workspace_graph(index, workspace)   # build_controller.py, D1a's assembly step
  └─ for step in ordered_by_depends_on(workspace.spec.execution):  # build_controller.py, Kahn's-algorithm order
       ├─ provisioner = find_provisioner(workspace, step.provisioner)
       ├─ integration = resolve_integration(index, provisioner)
       ├─ source_path = sync_source(context.root, build_path, provisioner.source, remotes)  # skipped for sync/GitOps provisioners (no .source)
       └─ integration.prepare(source_path, resolved=resolved, provisioner=provisioner, graph=graph)
```

Every line above is real, built code today — not a sketch.

Plus a second, independent workload pipeline (ADR-0022 D5-D7,
Compose/Helm, driven by `Namespace.spec.modules` rather than
`ProvisionerModel`) — **entirely not built**, including
`prepare_namespace()` itself (neither `ComposeIntegration` nor
`HelmIntegration` implement it yet).

### What already exists (usable once the orchestrator calls it)

| Piece | File | Status |
| --- | --- | --- |
| `ValueResolution` (flat `values: dict[str, str]`) | `strata/integrations/resolved_context.py` | Built — used today by `value_controller.resolve_values()` |
| `ResolvedWorkspaceGraph` (workspace + providers/topologies/resources/namespaces/firewalls/dns/networks, all by name) | `strata/integrations/resolved_context.py` | Built as a type; nothing assembles a real one yet — no `build_resolved_workspace_graph()` function exists |
| `InfraIntegration.prepare()` — calls `default_output()`, writes each returned file | `strata/integrations/capabilities.py` | Built (ADR-0023 D5's dispatch only — no `output.template`/backend-token-substitution wiring yet) |
| `TerraformIntegration.default_output()` — the real Terraform projection | `strata/integrations/terraform.py` + `terraform_projection.py` | Built for Phase 1 + 2a + 2c's `dns`/`networks` (see table below) |
| `ComposeIntegration`/`HelmIntegration.default_output()` | — | **Not overridden** — both currently inherit the empty "generate nothing" default, which is *not* their intended behaviour (unlike Bicep, for which "generate nothing" is correct) |
| `resolve_integration()` (D2's auto-bind-or-error) | `strata/controllers/integration_resolution.py` | Built — named binding always wins; auto-bind falls back to zero/one enabled matching `Integration` document; more than one is a `UsageError` naming every candidate. Also verifies the resolved class is actually `InfraIntegration`-capable. |
| `sync_source()` (D3, including the sibling-provisioner relative-path gap) | `strata/controllers/source_sync.py` | Built — mirrors each source's own `source_path` as its build-directory destination (not the step name), which is what makes sibling relative composition resolve correctly with no cross-provisioner awareness needed. Chart-based sources explicitly out of scope (Helm's own pull mechanism, not a file copy). |
| `build_resolved_workspace_graph()` (D1a's assembly step) | `strata/controllers/build_controller.py` | Built — walks all seven of a workspace's name-lists (providers/topology/resources/namespaces/firewalls/dns_zones/networks) via the index, skipping a name that does not resolve (defensive; Phase 1's `validate_references` already guarantees these exist). |
| `ordered_by_depends_on()` | `strata/controllers/build_controller.py` | Built — Kahn's-algorithm topological sort, same shape `provisioning_model.validate_provisioning_steps()` already uses to *detect* a cycle, but returning the order instead of discarding it. Assumes already-validated input (acyclic) — `WorkspaceSpecModel.validate_execution()` guarantees this for real workspaces. |
| `find_provisioner()` | `strata/controllers/build_controller.py` | Built — trivial lookup; `WorkspaceSpecModel.validate_execution()` already guarantees the name exists. |
| `build_time_keys()` (ADR-0022 D1a's safety note) | `strata/controllers/value_controller.py` | Built — reuses `resolve_values()`'s own deployment/environment-reachability walk (factored into `resolve_deployment()`/`_reachable_environments()`) so the two can never disagree about which environments are in scope; returns variable/feature keys only, never secrets. |
| Build orchestrator (`build_controller.build_run()`, the loop itself) | `strata/controllers/build_controller.py` | Built and end-to-end tested — a real workspace/provider/resource/deployment fixture materialises its Terraform source and writes real `.auto.tfvars.json` output (`tests/strata/controllers/test_build_controller.py`). |
| `strata build run` CLI command | `strata/commands/build_command.py` | Built and end-to-end tested — thin glue over `build_run()`, matching `validate_command.py`/`values_command.py`'s shape. `--build-path` overrides the default `layout.build_dir(root, deployment)` (`<root>/build/<deployment>`, already excluded from discovery by `DEFAULT_IGNORED_DIRS`'s `build` entry). |
| `build_workload_modules()` (D6, Compose/Helm grouping-by-type) | — | Not built |

### `TerraformIntegration.default_output()` — the one real projection built so far

`terraform_projection.py`'s `build_platform_projection()` produces one
payload per document category, written as one `*.auto.tfvars.json` file per
non-empty category (`planned_files()`), matching Terraform's own auto-load
convention:

| Category | Status |
| --- | --- |
| `workspace`, `providers`, `topologies`, `resources_by_category` | Built (Phase 1) |
| `namespaces`, `firewalls` | Built (Phase 2a) |
| `dns`, `networks` | Built (Phase 2c) — **known gap**: `${var:}`/`${secret:}`/`${feature:}` tokens inside `DnsRecordModel.value`/`SubnetModel.cidr`/`NetworkDefinitionModel.address_space` are written as-is, unresolved (token resolution is Phase 3, not wired into these two categories yet) |
| `modules` (Phase 2b) | Deliberately skipped — checked all real workspaces available, zero use of `TopologyComponentModel.modules`; Compose/Helm modules go through the separate workload pipeline instead |
| `tenant` (Phase 2c remainder) | Not built — no fixture data to ground its shape against yet |
| `required_variables`/`required_features`/`required_secrets` | Not built (Phase 3) — no v2 model has a `references` field to walk; needs a token-scan of resolved `configuration`/`backend`/`custom` |
| `output.template` (Jinja2 escape hatch, D3) | Not built (Phase 4) |
| `provisioner.backend`/`.configuration` token substitution (D2) | Not built (Phase 3) |

## Related Decisions

- [ADR-0022](../decisions/0022-strata-build-run.md) — orchestration design (D1-D7)
- [ADR-0023](../decisions/0023-build-output-rendering.md) — rendering design (D1-D5, Implementation Plan phases)
- [ADR-0021](../decisions/0021-integration-layer.md) / [integration-layer.md](integration-layer.md) — the `Integration`/`InfraIntegration` classes this command will call
- [remotes.md](remotes.md) — `sync_source()`'s own prerequisite (remote-to-filesystem-path resolution, done for `local`/`git`)
- [build-pipeline-status.md](build-pipeline-status.md) — the cross-ADR phase dashboard this doc's "what's built" table refines with real code references

## Remaining Work / Open Questions

Everything in the "not built" rows above. In build order (per ADR-0022's
own dependency chain):

1. ~~`build_resolved_workspace_graph()`~~ — done.
2. ~~Remote resolution~~ ([remotes.md](remotes.md) — done for `local`/`git`; `oci`/`helm` and credentialed private-repo fetches still open) and ~~`resolve_integration()`~~ (D2, done).
3. ~~`sync_source()`~~ (D3) — done, using `remote_resolution.resolve_remote()`.
4. ~~The `build_controller.py` orchestrator loop itself~~ — done and end-to-end tested.
5. ~~`strata build run` CLI command~~ (`commands/build_command.py`) — done, matching `validate_command.py`'s thin-glue-over-controller shape.
6. `ComposeIntegration`/`HelmIntegration.default_output()`, `build_workload_modules()`, and `prepare_namespace()` (D5-D7) — the entire workload pipeline. The only remaining gap for a working `strata build run`.
7. Phase 3/4 of ADR-0023: token substitution (`resolve_expr_tokens()`, wired into `dns`/`networks` too), the `output.template` escape hatch, `required_variables`/`.../`secrets` manifest.
8. `tenant`/`modules` projection categories, once real fixture data exists.

## Changelog

- 2026-09-24: Created. Grounded in the real (uncommitted) building-block
  code (`resolved_context.py`, `terraform_projection.py`,
  `capabilities.py`) rather than purely reconstructed from ADR text, since
  meaningful implementation work has happened since ADR-0022/0023 were
  written but the command itself still does not exist.
- 2026-09-24: `sync_source()`'s own prerequisite (remote-to-filesystem-path
  resolution) implemented for `local`/`git` remotes — see
  [remotes.md](remotes.md). `sync_source()` itself still does not exist.
- 2026-09-24: `sync_source()` implemented (`strata/controllers/source_sync.py`),
  resolving D3's sibling-provisioner relative-path gap by mirroring each
  source's own `source_path` as its destination under `build_path` rather
  than the step/provisioner name. Remaining before the orchestrator itself:
  `resolve_integration()` (D2) and `build_resolved_workspace_graph()`.
- 2026-09-24: `resolve_integration()` implemented
  (`strata/controllers/integration_resolution.py`), per D2's own design
  unchanged — grounded directly in `IntegrationModel`/`registry.get()`'s
  real signatures, no surprises found. Returns `InfraIntegration` (narrower
  than D2's original `Integration` sketch) since every real caller needs
  the narrower type anyway. Only real gap left before the orchestrator loop
  itself: `build_resolved_workspace_graph()`.
- 2026-09-24: The orchestrator loop itself implemented
  (`strata/controllers/build_controller.py`: `build_resolved_workspace_graph()`,
  `ordered_by_depends_on()`, `find_provisioner()`, `build_run()`) and
  `value_controller.py` refactored to expose `resolve_deployment()`/
  `build_time_keys()` as shared, public helpers rather than duplicating the
  deployment/environment-reachability walk a second time. A real
  workspace/provider/resource/deployment fixture now materialises its
  Terraform source and writes real `.auto.tfvars.json` output end to end
  (`test_build_run_materialises_source_and_writes_terraform_output`). Only
  the CLI command and the Compose/Helm workload pipeline remain.
- 2026-09-24: `strata build run` CLI command implemented
  (`strata/commands/build_command.py`, registered in `cli.py`) — thin glue
  matching `validate_command.py`/`values_command.py`'s shape exactly,
  `DEPLOYMENT` positional plus `--path`/`--build-path` options. Added
  `layout.build_dir(root, deployment)` for the default output location
  (`<root>/build/<deployment>`), rather than hardcoding the segment in the
  command — keeps `layout.py`'s own "every derived path belongs here" rule
  intact, and reuses the `build`/`dist` entries `DEFAULT_IGNORED_DIRS`
  already floors out of discovery. Only remaining gap for a working
  `strata build run`: the Compose/Helm workload pipeline (D5-D7).
