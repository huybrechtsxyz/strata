# `strata build run` Command — Design

- Status: `strata build run` exists and is end-to-end tested
  (`build_command.py` → `build_controller.build_run()`), including both
  halves of the workload pipeline (ADR-0022 D5-D7 — see
  [workload-pipeline.md](workload-pipeline.md)), stale-output cleaning
  (`--clean`/`--no-clean`), and `--dry-run` with shared step-by-step
  progress reporting on every run. Remaining parity-gap items: see below.
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
strata build run DEPLOYMENT [--path PATH] [--build-path PATH] [--clean/--no-clean] [--dry-run] [--resolve] [--env-file PATH]...
  └─ build_command.py: build_run_command()
       ├─ context = open_solution(path).require_valid()
       ├─ target = build_path or layout.build_dir(context.root, deployment)  # '<root>/build/<deployment>' by default
       ├─ should_clean = clean if clean is not None else build_path is None  # default path always cleaned; custom path only if --clean given
       └─ diagnostics = build_run(context, deployment, target, clean=should_clean, dry_run=dry_run,
                                   on_step=run.step, resolve=resolve, env_files=list(env_files))

build_run(context, deployment_name, build_path, *, clean=True, dry_run=False, on_step=None, resolve=False, env_files=None)
  ├─ for f in env_files: os.environ.setdefault(key, value) per load_env_file(f)   # docs/design/build-time-value-categories.md, Q9
  ├─ deployment = resolve_deployment(context, deployment_name)   # value_controller.py — shared with resolve_values()
  ├─ workspace = index.get(WORKSPACE, deployment.spec.workspace)
  ├─ environments = reachable_environments(context, deployment)   # value_controller.py
  ├─ variable_refs, feature_refs, secret_refs = build_value_references(environments)   # Q1/Q2/Q4/Q5 — CONSTANT/ENVIRONMENT only, no network
  ├─ properties = merge_workspace_environment_deployment_properties(workspace, environments, deployment, "properties")  # Q3
  ├─ custom = merge_workspace_environment_deployment_properties(workspace, environments, deployment, "custom")
  ├─ resolved = ValueResolution(deployment=deployment_name)   # empty placeholder unless --resolve
  ├─ if resolve: validation = resolve_values(context, deployment_name, all_declared_keys); diagnostics.extend(validation.diagnostics)  # Q7 — never writes values
  ├─ if clean and build_path.exists(): rmtree(build_path) or on_step("would clean ...") if dry_run
  ├─ graph = build_resolved_workspace_graph(index, workspace, variable_refs=..., feature_refs=..., secret_refs=..., properties=..., custom=...)
  ├─ write_resolved_manifest(build_path, graph)   # Q8 — build_path/resolved.yaml, skipped under --dry-run
  └─ for step in ordered_by_depends_on(workspace.spec.execution):  # build_controller.py, Kahn's-algorithm order
       ├─ provisioner = find_provisioner(workspace, step.provisioner)
       ├─ integration = resolve_integration(index, provisioner)   # always resolved for real, even under --dry-run
       ├─ if dry_run: on_step("would materialise/render ..."); continue
       ├─ source_path = sync_source(context.root, build_path, provisioner.source, remotes)  # skipped for sync/GitOps provisioners (no .source)
       └─ integration.prepare(source_path, resolved=resolved, provisioner=provisioner, graph=graph)
```

Every line above is real, built code today — not a sketch. `build_time_keys()`
(the old `keys = build_time_keys(...); resolved = resolve_values(...)`
unconditional call this pseudocode used to show) was deleted — fully
superseded by `build_value_references()`, which does the equivalent
reachability walk without ever touching the network or `ValueResolution`.
See [build-time-value-categories.md](build-time-value-categories.md) for
the full design and phase-by-phase implementation history.

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
| `build_value_references()`/`merge_workspace_environment_deployment_properties()` (replaces the old `build_time_keys()`) | `strata/controllers/value_controller.py` | Built — see [build-time-value-categories.md](build-time-value-categories.md) Q1/Q3/Q4/Q6 for the full design; reuses `resolve_deployment()`/`reachable_environments()` (now public) so it can never disagree with `resolve_values()` about which environments are in scope. |
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
| `dns`, `networks` | Built (Phase 2c) — `${var:}`/`${secret:}`/`${feature:}` tokens inside `DnsRecordModel.value`/`SubnetModel.cidr`/`NetworkDefinitionModel.address_space` are written as-is, unresolved. **Not a `build run`-scope fix** — decided 2026-09-25, see [value-token-resolution.md](value-token-resolution.md): v1 itself never resolves these at build time either (confirmed directly in `terraform_builder.py`), only at deploy with fully-resolved values; a `dns`/`networks`-only build-time resolver would be inconsistent with `firewall`/`module` (same token mechanism, same gap). One shared resolver, applied uniformly to every kind, belongs at deploy time. |
| `modules` (Phase 2b) | Deliberately skipped — checked all real workspaces available, zero use of `TopologyComponentModel.modules`; Compose/Helm modules go through the separate workload pipeline instead |
| `tenant` (Phase 2c remainder) | Not built — no fixture data to ground its shape against yet |
| `required_variables`/`required_features`/`required_secrets` | **Superseded by `resolved.yaml`** (docs/design/build-time-value-categories.md, Q8) — this row's old description was wrong: v1's real `required_variables`/`required_features`/`required_secrets` is a **declaration-based** inventory (`_collect_environment_variables()`, walking every declared key regardless of whether anything references it), not a token-scan of resolved `configuration`/`backend`/`custom` as previously stated here. `resolved.yaml` already is that inventory (`variable_refs`/`feature_refs`/`secret_refs`), just as plain YAML instead of `*.auto.tfvars.json` (deliberately, to avoid requiring matching `variable {}` blocks in the user's `.tf` files — see Q8). Nothing further to build for this specific row. |
| `output.template` (Jinja2 escape hatch, D3) | **Split, see [value-token-resolution.md](value-token-resolution.md).** ADR-0023's Phase 4 sketch assumed full build-time rendering — found to be the same flaw as the `dns`/`networks`/`backend` rows: `variables`/`flags` only carry `constant`/`environment`-backed values at build time, so a real template referencing a Vault/AppConfig-backed key would raise unconditionally, every build. (a) **Build-time validation — Built.** `strata/utils/templater.py`'s `validate_template_references()` (static-only, `jinja2.meta.find_undeclared_variables()` + an AST walk for `variables.KEY`/`flags.KEY`/`secrets.KEY` access), wired into `InfraIntegration.prepare()` — when `provisioner.output.template` is set, `default_output()` is skipped entirely and nothing is written; validation failures raise `IntegrationError`→`UsageError`, same as any other build failure. (b) **Actual rendering** — still deploy-time only, blocked on `deploy run`, same backlog as the row below. |
| `provisioner.backend`/`.configuration` token substitution (D2) | **Re-scoped 2026-09-25, see [value-token-resolution.md](value-token-resolution.md).** Same root cause as the row above and the `dns`/`networks` row — needs fully-resolved values (including secret-shaped leaves, which must never be written to disk), only available at deploy time. Confirmed directly in v1: `resolve_expr_string()`/`EXPR_PATTERN` is used exclusively by `TerraformDeployer`/`HelmDeployer` (deploy-time), never by any build-time builder. Not `build_run`-scope. |
| `flags`/`variables`/`properties`/`custom` (v1's other four default categories) | **Built.** `TerraformIntegration.default_output()` reads `graph.variable_refs`/`.feature_refs`/`.properties`/`.custom` directly — no merge/reachability logic inside the integration itself, all five values are computed once by `build_controller.py` before the provisioner loop. See [build-time-value-categories.md](build-time-value-categories.md) for the full design and all 5 implementation phases (status: done). |

## Related Decisions

- [ADR-0022](../decisions/0022-strata-build-run.md) — orchestration design (D1-D7)
- [ADR-0023](../decisions/0023-build-output-rendering.md) — rendering design (D1-D5, Implementation Plan phases)
- [ADR-0021](../decisions/0021-integration-layer.md) / [integration-layer.md](integration-layer.md) — the `Integration`/`InfraIntegration` classes this command will call
- [ADR-0025](../decisions/0025-strata-supplies-input-not-source-rewriting.md) — why `sync_source()` copies verbatim and never rewrites a materialised source
- [build-time-value-categories.md](build-time-value-categories.md) — design in progress for the `features`/`variables`/`properties`/`custom` gap in `default_output()` table above
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
7. Phase 3/4 of ADR-0023: `provisioner.backend`/`.configuration` token substitution, the `output.template` escape hatch. `dns`/`networks` token substitution is **not** part of this — moved to [value-token-resolution.md](value-token-resolution.md) (deploy-time, one shared resolver for every kind, not `build run`-scoped).
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
3. **~~No `.gitignore` emission into the build output.~~ Retracted —
   mis-attributed to `build_run()` on first pass, no real code ever
   located to confirm it.** Re-checked directly against v1's actual
   builders (`terraform_builder.py`, `base_builder.py`,
   `platform_builder.py`, `compose_builder.py`, `helm_builder.py`) —
   **none of them write a `.gitignore`, or any `*.tfstate`/`.terraform/`
   pattern, anywhere.** What actually exists is
   `strata/templates/solution/dot.gitignore`, a **solution-root scaffold
   file written once by `strata init`** (not `build run`), which excludes
   the entire `build/` directory with one blanket top-level rule (plus
   defense-in-depth `**/.terraform/`/`*.tfstate` lines for the rarer case
   of running Terraform outside `build/`). This was never a `build_run()`
   concern — it belongs to `strata init`/solution scaffolding, which v2
   does not have at all yet (no `commands/init_command.py`, confirmed).
   Not a `build run` gap; not actionable here. If/when a `strata init`
   equivalent is built, its own scaffold template is the right place for
   an equivalent `.gitignore`, not this command.
4. **~~No token/template substitution inside *synced source files*.~~
   Decided against — now [ADR-0025](../decisions/0025-strata-supplies-input-not-source-rewriting.md).**
   v1's `BaseBuilder` Jinja2-renders every copied `.tf`/`.tfvars`/`.bicep`/
   playbook with a `STRATA_*` + `variables` + `features` context (secrets
   deliberately excluded) — confirmed real, unlike gap 3 above
   (`terraform_builder.py` calls
   `self._apply_templates_to_dir(dest_dir, template_context)` right after
   every `copytree`/git-ref extraction). v2 will not port it: **strata
   supplies input to IaC, it does not rewrite IaC source.** Full reasoning,
   scope boundary and consequences in the ADR; the short version is that
   every tool already has a native input mechanism strata writes *alongside*
   the source (`.auto.tfvars.json`, `values.yaml`, `STRATA_*`), and
   mutating fetched/vendored source in place is both redundant with that and
   strictly worse. `sync_source()`/`sync_module_source()` staying
   byte-for-byte copies is now a recorded decision, not an open gap.
5. **Build lifecycle hooks and `phase: build` policies — real, but not a
   `build run` gap; split and re-homed to
   [lifecycle.md](lifecycle.md).** Both halves verified in v1:
   `run_build_command.py` fires `build_run_before`/`build_validate`/
   `build_generate`/`build_run_after` via
   `LifecycleController.execute_configuration_phase()`, and
   `_evaluate_build_policies()` evaluates `Configuration.spec.policies`
   filtered to `phase == "build"` with deny/warn/audit enforcement. What the
   original note got wrong is the *scope*:
   - **Hooks are not build-specific and there is nothing for `build_run()`
     to call.** v1 fires lifecycle hooks from ~30 sites across ~12 commands
     (build/deploy/solution), and v2 executes lifecycle **nowhere, for any
     command** — zero references outside `models/`. v2's
     `ConfigurationSpecModel` doesn't even have a `lifecycle` field, which
     is exactly where v1's build hooks come from. Adding a call in
     `build_run()` would have nothing to read and no executor to call.
     Tracked in [lifecycle.md](lifecycle.md), blocked on schema-parity
     Issue 5 (hierarchy precedence).
   - **Policies are already a recorded decision.**
     [ADR-0020](../decisions/0020-v1-consumer-feature-priority.md) lists
     `policies` among the deferred `ConfigurationSpecModel` fields, with a
     stated convention ("port incrementally alongside the command that
     needs each") and a matching Remaining Work entry. Not an undocumented
     gap. Worth noting a v2 build-phase policy engine would also have a
     much emptier context than v1's, whose `PolicyContext` carries
     `platform_artifact`/`sbom_components`/`cve_audit_result` — all three
     cut by ADR-0022.

Minor, same origin:

- **~~`build run --dry-run`~~ — implemented, but not as v1's parallel
  "render in memory" code path.** `build_run()`/`build_workload_modules()`
  gained `dry_run: bool = False` and `on_step: Callable[[str], None] | None`
  parameters. `dry_run` doesn't fake a render (there's no way to "render in
  memory" here the way `deploy run`'s real `plan` differs from `apply`) —
  it skips exactly three real side effects (the `clean` wipe, materialising
  a source, and the actual `prepare()`/`prepare_namespace()` write) while
  every validating step still runs for real (deployment/workspace/value/
  integration resolution), so a dry run still catches a bad deployment name
  or an unresolvable tool type. `on_step` reports a one-line message at each
  of those same points — "would materialise .../would render ..." under
  `--dry-run`, "materialised .../rendered ..." on a real run — one reporting
  path shared by both, not a second parallel one. `strata build run` gained
  `--dry-run`, wired to `run.step` for live console output. Full check suite
  green: 990/990 tests passing.
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
  `cfg-deployment`'s `control/workspace.yaml`
  (`output: {format: custom, emits: [...]}`), contradicting the "zero
  usage" evidence D2 was decided on. Still the biggest known correctness
  risk against a real consumer. **Investigated 2026-09-25, found to be
  blocked on a bigger prerequisite, not yet resolved.** The real example's
  need is `emits: [features, variables, properties]` — but v2 doesn't emit
  `features`/`variables`/`properties` categories *at all* yet (see the
  `default_output()` table above), so there is nothing for `emits` to
  gate. Emit-suppression only starts to matter once those categories
  exist. Full v1 schema confirmed
  (`OutputProfileModel`/`OutputFileModel`/`EmitCategory`,
  `workspace_model.py`) — `format: strata|custom|script|none`, `emits: [...]`,
  `files: [...]` (single/multi-source/script-generated custom files). No
  real evidence found yet for the `script` format or custom `files[]` —
  the one real example uses only `format: custom` + `emits`.

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
- 2026-09-25: Parity gap 3 (`.gitignore` emission) retracted, not fixed.
  Re-grounding it against v1's real builders before implementing (this
  session's own established discipline) found no code anywhere that
  writes a `.gitignore`/`*.tfstate`/`.terraform/` pattern into build
  output — the original note's claim had never actually been located in
  source, only inferred. The real behaviour it was describing
  (`strata/templates/solution/dot.gitignore`, excluding the whole `build/`
  directory with one blanket rule) belongs to `strata init`'s
  solution-scaffold template, a command v2 does not have at all yet — not
  `build_run()`. No code changed; the parity-gap entry itself was
  corrected instead.
- 2026-09-25: Parity gap 4 (token/template substitution inside synced
  source files) re-verified as real (unlike gap 3) — confirmed directly in
  `terraform_builder.py`, which calls
  `self._apply_templates_to_dir(dest_dir, template_context)` after every
  copy/extract. Decided against porting it: the tool-native input
  mechanism (`.tfvars`/`TF_VAR_*`, `STRATA_*`/`.env`, `values.yaml`) is
  strictly better and already built for Terraform
  (`terraform_projection.py`'s typed, auto-loaded `.auto.tfvars.json`);
  rewriting fetched/vendored source in place after copying it is the wrong
  layer, and v1's own implementation already needed a permanent
  skip-non-Jinja2-files escape hatch as a symptom of that. Also updated
  [workload-pipeline.md](workload-pipeline.md)'s matching remaining-work
  line (same decision, module/Compose/Helm side). No code changed —
  `sync_source()`/`sync_module_source()` staying byte-for-byte copies is
  now a recorded decision, not an open gap.
- 2026-09-25: That decision promoted out of this doc into
  [ADR-0025](../decisions/0025-strata-supplies-input-not-source-rewriting.md)
  ("strata supplies input to IaC; it does not rewrite IaC source"), since
  it is a point-in-time architectural decision with a scope boundary worth
  stating once — not build-out status. Per `docs/decisions/README.md`'s
  own convention, the reasoning now lives in the ADR and this doc's parity
  gap 4 entry is a short pointer to it.
- 2026-09-25: Parity gap 5 (build lifecycle hooks / `phase: build` policies)
  verified real in v1 but found **mis-scoped**, and split. The hooks half is
  not a `build run` concern at all: v1 fires lifecycle from ~30 sites across
  ~12 commands, v2 executes lifecycle *nowhere for any command* (zero
  references outside `models/`), and v2's `ConfigurationSpecModel` lacks the
  `lifecycle` field v1's build hooks actually read from — so there is
  nothing for `build_run()` to call and nothing to call it with. Re-homed to
  a new [lifecycle.md](lifecycle.md), which records v2's inert schema (seven
  kinds), v1's real `LifecycleController` mechanism, a correction to
  ADR-0021 D11's characterisation of v1's interpreter dispatch, and nine
  open questions gating implementation — gated in turn on schema-parity
  Issue 5. The policies half needed no new record: ADR-0020 already defers
  `Configuration.spec.policies` explicitly, with a porting convention and a
  Remaining Work entry. No code changed.
- 2026-09-25: `--dry-run` implemented for `build run` — deliberately not a
  port of v1's parallel "render in memory" builder path (there's no
  meaningful "fake" build the way `deploy run`'s `plan` differs from
  `apply`). Instead, `build_run()`/`build_workload_modules()` gained a
  shared `on_step` progress-reporting callback used by both a real run and
  a dry run alike (real work described when it happens; planned work
  described instead when `dry_run=True` skips the three actual side
  effects — the `clean` wipe, materialising a source, and the render
  itself). Every validating step still runs for real under `--dry-run`
  (deployment/workspace/value/integration resolution), so a bad deployment
  name or an unresolvable tool type is still caught. `strata build run`
  gained `--dry-run`, wired to `run.step` for live console progress on
  every invocation, dry or not. 18 new tests. Full check suite green:
  990/990 tests passing.
- 2026-09-25: Investigated the `OutputProfileModel` revisit (priority
  item). Confirmed v1's full real schema
  (`OutputProfileModel`/`OutputFileModel`/`EmitCategory`) and the exact
  real need (`emits: [features, variables, properties]`, documented in
  `cfg-deployment` alongside a real strata bug/fix, PR #309) — but
  found it's blocked on a bigger, previously-unnoticed prerequisite:
  `TerraformIntegration.default_output()` discards `resolved.values`
  entirely (`del resolved`), so the `features`/`variables`/`properties`
  categories `emits` would gate don't exist in v2 at all yet. Traced this
  into [provisioning-injection-model.md](provisioning-injection-model.md)
  (Context) while checking whether it was a missing-Context problem — it
  isn't; `ValueResolution` already flows through the whole build pipeline,
  this is a narrower, one-method gap. No code changed; both this doc and
  provisioning-injection-model.md updated with the findings and split into
  separately-actionable items.
