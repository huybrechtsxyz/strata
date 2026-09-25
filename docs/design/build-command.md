# `strata build run` Command — Design

- Status: `strata build run` exists and is end-to-end tested
  (`build_command.py` → `build_controller.build_run()`), including both
  halves of the workload pipeline (ADR-0022 D5-D7 — see
  [workload-pipeline.md](workload-pipeline.md)). Stale-output cleaning
  (parity gap 1 below) is now fixed; the rest of the parity-gap list is
  still open.
- Last updated: 2026-09-25

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
`ProvisionerModel`) — **Helm built and wired into `build_run()`'s loop,
Compose not yet** — see [workload-pipeline.md](workload-pipeline.md) for
that pipeline's own design/status, tracked separately from this doc since
ADR-0022 D5 itself found it to be a disconnected input shape, not a
variant of the provisioner loop above.

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
| Build orchestrator (`build_controller.build_run()`, the loop itself) | `strata/controllers/build_controller.py` | Built and end-to-end tested — a real workspace/provider/resource/deployment fixture materialises its Terraform source and writes real `.auto.tfvars.json` output (`tests/strata/controllers/test_build_controller.py`). Also calls the workload pipeline (below) for every namespace on the resolved graph. |
| `strata build run` CLI command | `strata/commands/build_command.py` | Built and end-to-end tested — thin glue over `build_run()`, matching `validate_command.py`/`values_command.py`'s shape. `--build-path` overrides the default `layout.build_dir(root, deployment)` (`<root>/build/<deployment>`, already excluded from discovery by `DEFAULT_IGNORED_DIRS`'s `build` entry). |
| Workload pipeline (`prepare_namespace()`, Helm) | `strata/controllers/workload_controller.py`, `strata/integrations/helm.py` | Built and end-to-end tested for Helm — see [workload-pipeline.md](workload-pipeline.md). Compose not built. |

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
6. `ComposeIntegration.prepare_namespace()`/`build_workload_modules()`'s Compose half (D5-D7) — the entire remaining gap in the workload pipeline; see [workload-pipeline.md](workload-pipeline.md). Helm's half is done.
7. Phase 3/4 of ADR-0023: token substitution (`resolve_expr_tokens()`, wired into `dns`/`networks` too), the `output.template` escape hatch, `required_variables`/`.../`secrets` manifest.
8. `tenant`/`modules` projection categories, once real fixture data exists.

### v1 parity gaps found 2026-09-25 — not recorded in any ADR

A v1-vs-v2 build comparison (v1's `RunBuildCommand` + its 7 builders against
today's `build_run()`) confirmed that nearly every large v1 feature absent
from v2 is an *explicit, reasoned* cut — ADR-0022 (lines 34-62) cuts SBOM,
CVE audit, `--ai`, lock-mode, cache warming, Ansible and `PlatformBuilder`/
`platform.json`; ADR-0020 Tier 2 cuts `build plan`/`build clean`;
ADR-0023 cuts `OutputProfileModel` and defers token substitution,
`output.template`, Compose, `modules`/`tenant`. The items below are the
ones that fell through: real v1 build behaviour with no v2 equivalent and
no decision recorded anywhere. Listed here rather than in an ADR because
each is small enough to be ordinary remaining work, not a decision needing
its own document — except where noted.

1. **~~No stale-output cleaning before a build (correctness).~~ Fixed.** v1's
   `PlatformBuilder` wipes `build/<deployment>/` on pre-build precisely so
   a removed resource type's `resx_*.auto.tfvars.json` cannot survive into
   the next `terraform apply`. `build_run()` gained a `clean: bool = True`
   parameter (default matches v1 — wipe `build_path` before rendering) —
   safe by default because the *default* `build_path`
   (`layout.build_dir()`) is exclusively strata's own directory. A custom
   `--build-path` is a different trust boundary (it may point somewhere
   the caller doesn't exclusively own), so the CLI passes `clean=False`
   for it unless `--clean` is also given — see `build_command.py`'s
   `--clean/--no-clean` flag. This was also the answer to "should
   `--build-path` even exist, given the wipe risk": yes — real reason to
   redirect output (CI artifact staging directories, e.g. haven's
   `upload-artifact` step per item 3 below) — the fix scopes the *risk*
   correctly instead of removing the *option*.
2. **~~`ModuleReferenceModel.enabled` is ignored by the workload pipeline.~~
   Fixed.** The field exists (`common_models.py`, "Whether this module is
   enabled/deployed") and its resource-side twin *is* honoured
   (`terraform_projection.py` skips `WorkspaceResourceModel.enabled=False`),
   but `build_workload_modules()` never checked `reference.enabled` — a
   disabled module still got its source materialised and its
   `values.yaml`/`meta.yaml` written. `build_workload_modules()` now skips
   a disabled reference before it is even resolved (`resolve_module()` is
   never called, so a disabled reference can name a module that doesn't
   exist in the index at all) — mirrors
   `_build_resources_payload()`'s identical `if not
   workspace_resource.enabled: continue` exactly.
3. **No `.gitignore` emission into the build output.** v1 wrote
   `*.tfstate`, `.terraform/`, `.terraform.lock.hcl` into each provisioner
   directory. v2 writes none. Relevant because a real consumer (haven)
   round-trips `build/` through `upload-artifact`/`download-artifact`.
4. **No token/template substitution inside *synced source files*.** v1's
   `BaseBuilder` Jinja2-renders every copied `.tf`/`.tfvars`/`.bicep`/
   playbook with a `STRATA_*` + `variables` + `features` context (secrets
   deliberately excluded). `sync_source()` is a byte-for-byte copy.
   Adjacent to ADR-0023 Phase 3 but not covered by it: Phase 3 scopes token
   resolution to `provisioner.backend`/`.configuration`/`.properties`, and
   the copied-file surface is never named. Decide explicitly whether v2
   wants this at all — if the answer is "no, sources are opaque", that is
   worth writing down rather than leaving implicit.
5. **Build lifecycle hooks and `phase: build` policies.** v1 fires
   `build_run_before`/`build_validate`/`build_generate`/`build_run_after`
   and evaluates build-phase policies with deny/warn/audit enforcement.
   v2 mentions lifecycle only as unresolved schema-parity Issue 5
   ([v1-schema-parity-tracking.md](v1-schema-parity-tracking.md)) and as a
   deferred Environment subtree, never as `build run` behaviour. Probably
   correct to skip given `ConfigurationSpecModel` defers `policies`
   wholesale — but it was never stated as a decision.

Minor, same origin:

- **`build run --dry-run`** — v1 has it for CI preview (renders in memory,
  logs planned paths, writes nothing). v2's implicit position is "build is
  already dry", which conflates *does not execute* with *does not write*.
  Unstated either way.
- **Terraform input validation against `variables.tf`** — v1 fails the
  build on a declared-input/schema mismatch before `apply` would.
  [provisioning-injection-model.md](provisioning-injection-model.md)
  mentions parsing `variables.tf` as a capability lookup, but not as a
  build-time gate.

### Two recorded deferrals whose trigger condition has now fired

Both were deferred *conditionally*, and the condition is now met — neither
is a missed conversion, but neither has been revisited:

- **Cross-manifest overlap detection** (v1's `overlap_controller.py`: same
  Terraform state backend or namespace claimed by two deployments). The
  recorded reason to defer was "until `strata build` exists — it needs the
  build layer's artifact/state-identity concept to key on". `build run`
  now exists and writes real artifacts.
- **`OutputProfileModel` (ADR-0023 D2).** ADR-0023's own Remaining Work
  already records that real load-bearing usage was found in
  `cfg-int-deployment`'s `control/workspace.yaml`
  (`output: {format: custom, emits: [...]}`), contradicting the "zero
  usage" evidence D2 was decided on. Still the biggest known correctness
  risk against a real consumer, and still unresolved.

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
- 2026-09-24: Helm's half of the workload pipeline (D5-D7) implemented and
  wired into `build_run()`'s loop — see [workload-pipeline.md](workload-pipeline.md)
  for the full design, including two real corrections found while grounding
  it against v1's actual `HelmBuilder` (module build directories/`releaseName`
  keyed by the reference name, not `module.meta.name`; chart materialisation
  split into a new controller-layer `sync_module_source()`, never touched by
  `HelmIntegration` itself). Compose still not built.
- 2026-09-25: Recorded the v1-vs-v2 build comparison's findings under
  Remaining Work — five v1 build behaviours with no v2 equivalent and no
  decision recorded in any ADR (stale-output cleaning, `ModuleReferenceModel.enabled`
  ignored by the workload pipeline, `.gitignore` emission, substitution
  inside synced source files, build lifecycle hooks/`phase: build`
  policies), two minor ones (`--dry-run`, `variables.tf` input validation),
  and the two conditionally-deferred items whose trigger has now fired
  (overlap detection — it was waiting for `build run` to exist; ADR-0023
  D2's `OutputProfileModel` revisit). Everything else absent from v2 was
  confirmed to be an explicit, reasoned cut in ADR-0020/0022/0023 — this
  list is only the residue that was never written down anywhere.
- 2026-09-25: Parity gap 1 (stale-output cleaning) fixed. `build_run()`
  gained `clean: bool = True` — wipes `build_path` before rendering,
  matching v1's `PlatformBuilder` exactly, with a new `BuildCleanError`
  (`SystemError`) if the wipe itself fails. `build_command.py` gained
  `--clean/--no-clean` (tri-state, `default=None`): the default build path
  is always cleaned regardless of the flag (it's exclusively this build's
  own directory — always safe); a custom `--build-path` is only cleaned
  when `--clean` is explicitly passed, since it may point somewhere the
  caller doesn't exclusively own. This also settled a real design question
  raised alongside it — whether `--build-path` should exist at all, given
  the wipe risk it introduces — by scoping the risk to the flag rather
  than removing the option (a custom build path has a real use, e.g.
  redirecting into a CI artifact-staging directory, per parity gap 3).
  9 new tests. Full check suite green: 973/973 tests passing.
- 2026-09-25: Parity gap 2 (`ModuleReferenceModel.enabled` ignored)
  fixed. `workload_controller.build_workload_modules()` now skips a
  disabled reference before `resolve_module()` is even called — mirrors
  `terraform_projection._build_resources_payload()`'s identical
  `enabled=False` skip on the resource-attachment side of the same shared
  field. 4 new tests. Full check suite green: 976/976 tests passing.
