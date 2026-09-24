# `strata build run` Command — Design

- Status: not implemented — no CLI command, no orchestrator controller.
  Several of the pieces it will call are already built (see below).
- Last updated: 2026-09-24

## Overview

`strata build run` will render a workspace's provisioners/modules into
on-disk artifacts (Terraform `.tfvars.json`, Helm `values.yaml`, Compose
`docker-compose.yml`, ...) without executing anything — decided in
[ADR-0022](../decisions/0022-strata-build-run.md) (orchestration) and
[ADR-0023](../decisions/0023-build-output-rendering.md) (what gets
rendered). Unlike [validate-command.md](validate-command.md) and
[integration-layer.md](integration-layer.md), there is **no
`src/strata/commands/build_command.py`, no `strata/controllers/build_controller.py`,
and `cli.py` does not register a `build` group at all** — this doc is
grounded in the ADRs plus the real building-block code that already exists
for it, not in a working command.

## Current Design (planned, per ADR-0022/0023)

```
build_run(context, deployment_name, build_path)
  ├─ resolve_deployment(...)                          # NOT BUILT — no such controller function yet
  ├─ resolve_values(context, deployment_name, keys=build_time_keys)  # EXISTS (value_controller.py)
  ├─ workspace = deployment -> workspace              # NOT BUILT
  ├─ graph = build_resolved_workspace_graph(...)       # NOT BUILT (D1a's assembly step)
  └─ for step in ordered_by_depends_on(workspace.spec.provisioning):  # NOT BUILT
       ├─ provisioner = find_provisioner(workspace, step.provisioner)
       ├─ integration = resolve_integration(index, provisioner)       # NOT BUILT (D2's auto-bind-or-error)
       ├─ source_path = sync_source(provisioner.source, build_path/step.name)  # NOT BUILT (D3)
       └─ integration.prepare(source_path, resolved=resolved, provisioner=provisioner, graph=graph)  # EXISTS
```

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
| `resolve_integration()` (D2's auto-bind-or-error) | — | Not built |
| `sync_source()` (D3, including the sibling-provisioner relative-path gap) | — | Not built |
| Build orchestrator (`build_controller.py`, the loop itself) | — | Not built |
| `strata build run` CLI command | — | Not built — no `commands/build_command.py`, not registered in `cli.py` |
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
- [remotes.md](remotes.md) — `sync_source()`'s own unbuilt prerequisite: nothing resolves a remote name to a filesystem path yet, for any type
- [build-pipeline-status.md](build-pipeline-status.md) — the cross-ADR phase dashboard this doc's "what's built" table refines with real code references

## Remaining Work / Open Questions

Everything in the "not built" rows above. In build order (per ADR-0022's
own dependency chain):

1. `build_resolved_workspace_graph()` — assemble a real `ResolvedWorkspaceGraph` from a loaded `SolutionContext`/`DocumentIndex`.
2. Remote resolution ([remotes.md](remotes.md) — its own prerequisite chain, `sync_source()`'s real blocker) and `resolve_integration()` (D2).
3. `sync_source()` (D3) itself, once (2)'s remote resolution exists for the types actually in use.
4. The `build_controller.py` orchestrator loop itself (D1's pseudocode above).
5. `strata build run` CLI command (`commands/build_command.py`), matching `validate_command.py`'s thin-glue-over-controller shape.
6. `ComposeIntegration`/`HelmIntegration.default_output()`, `build_workload_modules()`, and `prepare_namespace()` (D5-D7) — the entire workload pipeline.
7. Phase 3/4 of ADR-0023: token substitution (`resolve_expr_tokens()`, wired into `dns`/`networks` too), the `output.template` escape hatch, `required_variables`/`.../`secrets` manifest.
8. `tenant`/`modules` projection categories, once real fixture data exists.

## Changelog

- 2026-09-24: Created. Grounded in the real (uncommitted) building-block
  code (`resolved_context.py`, `terraform_projection.py`,
  `capabilities.py`) rather than purely reconstructed from ADR text, since
  meaningful implementation work has happened since ADR-0022/0023 were
  written but the command itself still does not exist.
