# `strata deploy destroy` Command — Work

- Status: draft
- Last updated: 2026-10-09

## Overview

`strata deploy destroy` would tear down what `strata deploy run` created —
v1's real command, Tier 2 in `/memories/repo/v1-consumer-usage.md` ("operational/
observability commands, not on the critical deploy path" — neither real
consumer repo's CI workflows invoke it, only `deploy run`). **No such
command exists in v2 today** — confirmed by grep across `src/strata/commands/`
and `src/strata/controllers/`: there is no `destroy_command.py`, no
`destroy_run()`/`deploy_destroy()` controller function, and three separate
comments in the current codebase say so explicitly:

- `audit_run.py`: *"Only wired for `deploy` in this pass — no `destroy`
  command exists in v2 yet ... Extend when `deploy destroy` is built."*
- `docs/design/gitops-integration.md`: *"Explicitly out of scope, not a
  gap: no `deploy destroy`/`strata destroy run` command exists yet for
  *any* tool (pre-existing, not created here)."*
- `docs/design/cli-timeout.md`: `destroy`'s row in the Terraform timeout
  table reads *"schema field exists (phase `destroy`) but `deploy_run()`
  has no destroy call site yet — no consumer."*

This doc exists to collect what's already built and reusable (more than a
fresh read suggests — the integration layer was built destroy-aware from
the start, ADR-0021) against what's still a real gap, the same evidence-
first discipline `deploy-command.md` used for `deploy run`.

## Real evidence — v1's actual `--force`/`--dry-run` semantics for destroy

Already investigated once, while designing `deploy run` (`deploy-command.md`
Remaining Work item 7, reading v1's real installed source directly:
`xyz-strata` v1.11.2 `strata/deployers/terraform_deployer.py`,
`run_deploy_command.py`) — restated here since it's `destroy`-specific and
this doc, not that one, is `destroy`'s real home:

- **`-auto-approve` only appears for `destroy`.**
  `TerraformDeployer.destroy()` passes `auto_approve=self.force` — unlike
  `apply()` (which applies a saved plan file Terraform never prompts for
  regardless of flags), `terraform destroy` has no saved-plan-file
  equivalent and genuinely prompts interactively unless told not to.
- **`deploy destroy`'s CLI wiring is the *only* one of the two commands
  that enforces "exactly one of `--force`/`--dry-run` required."**
  `deploy run` has no such requirement (both default `False`, a real
  non-dry-run apply still proceeds) — `deploy destroy` is stricter by
  design, matching the higher blast radius of an irreversible teardown.
- Conclusion for v2: `destroy_run()`'s `--force` is **not** inert like
  `deploy run`'s (`deploy-command.md`'s own documented current state) —
  it has one concrete, real job here: `integration.destroy(path,
  auto_approve=force, ...)`.

## Current Design — already built, confirmed by reading the code directly

Nothing here was designed for this doc either — the integration layer
(ADR-0021) was built with `destroy()` as one of its three core
`InfraIntegration` methods from day one, alongside `plan()`/`deploy()`:

- **`InfraIntegration.destroy()`** — `@abstractmethod` on the ABC
  (`capabilities.py`), real implementations in all four concrete
  integrations, already tested against mocked `run_command` (none are
  stubs):
  - `TerraformIntegration.destroy()`: real `terraform destroy` argv
    (`-auto-approve`, `-var-file`, `-var` per variable, `-target` per
    resource) — the one place `-auto-approve` appears anywhere in this
    integration.
  - `ComposeIntegration.destroy()`: `docker stack rm <namespace>` —
    requires `namespace` (raises `IntegrationError` naming it if missing,
    matching `deploy()`'s own required-kwarg pattern).
  - `HelmIntegration.destroy()`: `helm uninstall --namespace ns release`
    — requires both `release` and `namespace`.
  - `BaseGitOpsIntegration.destroy()` (`gitops.py`): removal via
    `git_push.remove_file()` — *"v1's `destroy` step"*, its own docstring
    says; `remove_file()` is explicitly described elsewhere as
    *"`destroy()`'s mirror-image primitive"* to `push_file()`.
- **`DeploymentStageTimeoutsModel.destroy: int | None`** — the schema
  field already exists (ADR-0032, ported directly from v1's real
  `setup`/`check`/`plan`/`apply`/`destroy` phase names), with a hardcoded
  1800s default on `TerraformIntegration.destroy()` itself — genuinely
  wired for *timeout*, just never reached because nothing calls
  `destroy()` from `deploy_controller.py` yet.
- **Audit models already treat `destroy` as a first-class action**, not
  an afterthought bolted on later:
  - `AuditManifestModel.action: Literal["build", "deploy", "destroy"]`.
  - `audit_metrics_model.py`'s own field doc: *"'deploy' | 'destroy' —
    never 'build', never a dry run."*
  - `audit_event_rendering.py`'s `render_deployment_event()` **already
    branches on it**: `suffix = "destroyed" if manifest.action ==
    "destroy" else "completed"` — the CloudEvents/ECS renderer is already
    destroy-aware; only `audit_run.py`'s glue (which action to pass in,
    and when to call it) is the real gap, not the rendering itself.
- **No `destroy_namespace()` equivalent exists** for the Capability.CONTAINER
  path. `InfraIntegration.deploy_namespace()` (the `deploy run` per-module
  counterpart for Helm/Compose, `docs/design/gap_fit_v1.md` gap #13) has
  no sibling — a `deploy destroy` run against a step whose integration has
  `Capability.CONTAINER` would need a new, analogous `destroy_namespace()`
  method (same non-abstract/raise-by-default base shape
  `prepare_namespace()`/`deploy_namespace()` already establish), looping
  `HelmIntegration.destroy()` per module the same way
  `HelmDeployer.validate_workspace()`'s real v1 precedent does for every
  other lifecycle method.
- **`resolve_deploy_time_files()`/`tf_var_env()`** (`deploy_controller.py`,
  `terraform_projection.py`) — the deploy-time token/secret delivery
  mechanisms `deploy run` built are tool-agnostic and reusable as-is for
  `destroy`: a teardown still needs the same resolved backend config
  (to `init()` against the right state) and the same `TF_VAR_<KEY>`
  secret delivery (the state may reference secret-bearing resources during
  the plan Terraform computes internally for destroy). Nothing new needed
  here, only a new caller.

## Orchestrator shape — proposed, not yet implemented

Mirrors `deploy_run()`'s real shape (`deploy_controller.py`) with the
differences a teardown actually requires — not an independent design,
a deliberate variant:

```
strata deploy destroy DEPLOYMENT [--path PATH] [--build-path PATH]
  (--force | --dry-run)                      # exactly one required (v1's real enforcement, stricter than `deploy run`)
  [--stage NAME] [--scope LABEL] [--verbose]
  └─ destroy_command.py: destroy_run_command()
       └─ diagnostics = destroy_run(context, deployment, build_path, force=force, dry_run=dry_run,
                                      stage=stage, scope=scope, on_step=run.step)

destroy_run(context, deployment_name, build_path, *, force, dry_run, stage=None, scope=None, on_step=None)
  ├─ deployment, workspace, environments, resolved = (identical to deploy_run()'s own setup — same
  │     resolve_deployment()/reachable_environments()/resolve_values() calls, no new resolution logic)
  ├─ steps = ordered_by_depends_on(workspace.spec.execution)
  ├─ steps = list(reversed(steps))                 # NEW: teardown order is the inverse of build order —
  │                                                 #      destroy a step's dependents before the step itself
  ├─ steps = [s for s in steps if stage is None or s.name == stage]
  ├─ steps = [s for s in steps if scope is None or s.scope == scope]
  │
  ├─ for step in steps:
  │    integration = resolve_integration(index, find_provisioner(workspace, step.provisioner))
  │    path = <same source-keyed path build_run()/deploy_run() already resolve>
  │    env = tf_var_env(resolved, integration.ENV_VAR_PREFIX)
  │    backend_config = resolve_value_tokens_in_mapping(provisioner.backend.configuration, resolved.values)
  │
  │    if Capability.CONTAINER in integration.CAPABILITIES:
  │        modules = <same namespace/module resolution deploy_run() already does>
  │        diagnostics.extend(integration.destroy_namespace(namespace, modules, tokens=..., dry_run=dry_run))  # NEW method, gap above
  │    else:
  │        if not dry_run:
  │            integration.init(path, backend_config=backend_config, env=env)   # still needed — destroy must target the real state
  │            result = integration.destroy(path, auto_approve=force, env=env)  # NO saved-plan-file step — destroy has none
  │        else:
  │            integration.plan_or_warn(path, destroy=True, env=env, diagnostics=diagnostics, location=step.name)  # terraform plan -destroy preview
  │    on_step(f"... destroyed {step.name} ...")
  │
  └─ return diagnostics
```

Deliberately **not** carried over from `deploy_run()`:

- **No saved-plan-file mechanism** (`out_file`/`plan_file`) — `destroy()`
  has no equivalent two-phase plan-then-apply shape in any of the four
  integrations; `--force` maps directly to `auto_approve`, matching v1
  exactly (the one case `deploy_run()`'s own `force` parameter is
  currently *inert* for, by contrast).
- **No output-context collection** — `collect_step_outputs()` has nothing
  meaningful to collect after a teardown (the outputs described resources
  that no longer exist); `${output:}` tokens are meaningless on this path.
- **No `output.template` render** — same reasoning; a destroy run has no
  "final state" to describe.

## Related Decisions

- [ADR-0021](../decisions/0021-integration-layer.md) — defines
  `InfraIntegration.destroy()` as one of the three core capability
  methods (`plan`/`deploy`/`destroy`), already implemented for all four
  real integrations; this doc is purely about wiring an orchestrator and
  CLI around an already-built capability, not building a new one.
- [ADR-0027](../decisions/0027-strata-deploy-run.md) — `deploy run`'s
  settled orchestrator shape; `destroy_run()` is a deliberate variant of
  it, not an independent design.
- [ADR-0032](../decisions/0032-deploy-time-step-timeout-overrides.md) —
  the `destroy` timeout phase/field this doc would finally give a real
  call site.
- [deploy-command.md](deploy-command.md) — the sibling work doc for
  `deploy run`; this doc's "Real evidence" section cites that doc's own
  phase 7 `--force`/`-auto-approve` finding rather than re-deriving it.
- [docs/design/gitops-integration.md](../design/gitops-integration.md) —
  flagged this exact gap first (*"no `deploy destroy`/`strata destroy
  run` command exists yet for *any* tool"*), explicitly out of scope
  there.
- [docs/design/audit-trail.md](../design/audit-trail.md) — Layer 2's
  audit manifest/metrics models already model `destroy` as a first-class
  action; `audit_run.py`'s `finalize_and_distribute_deploy_audit()` would
  need a `destroy`-aware counterpart (or a shared, action-parameterized
  version) once this command exists.

## Remaining Work / Open Questions

1. **`InfraIntegration.destroy_namespace()`** (new method on the ABC,
   `capabilities.py`) — the `destroy`-side counterpart to
   `deploy_namespace()`, same non-abstract/raise-by-default base shape.
   `HelmIntegration` overrides it (loop `destroy()` per module, reading
   release/namespace back from the same `meta.yaml` `deploy_namespace()`
   already reads); `ComposeIntegration` overrides it with one
   `destroy(namespace=...)` call (matches its own merge-at-build-time,
   one-release-per-namespace shape). Blocks the Capability.CONTAINER
   branch above — without it, a destroy run against a Helm/Compose step
   would have no implementation to call.
2. **`destroy_run()` controller function** (`deploy_controller.py` or a
   new sibling module — open question itself, see item 6) — the
   orchestrator pseudocode above, reusing `resolve_deployment()`/
   `reachable_environments()`/`resolve_values()`/`ordered_by_depends_on()`
   unchanged, reversing step order, and calling `destroy()`/
   `destroy_namespace()` instead of `plan()`+`deploy()`.
3. **`--force`/`--dry-run` mutual-exclusion + required-one-of enforcement**
   — v1's real, confirmed CLI behavior (see "Real evidence" above); needs
   a `UsageError` raised in the command layer if neither (or both) are
   given, unlike `deploy run`'s own more permissive handling.
4. **CLI command** (`src/strata/commands/destroy_command.py`, or a
   `deploy destroy` subcommand on the existing `deploy_command` group —
   open question itself, see item 6) — thin glue matching
   `deploy_command.py`'s shape: `--path`/`--build-path`/`--force`/
   `--dry-run`/`--stage`/`--scope`, likely the same audit/change-reference
   flags (`--change-system`/`--change-id`/etc.) `deploy run` already
   carries, since a teardown is exactly the kind of change that needs an
   audit trail too.
5. **Audit wiring** — extend (or generalize) `audit_run.py`'s
   `finalize_and_distribute_deploy_audit()` for `action="destroy"`; the
   renderer (`audit_event_rendering.py`) is already destroy-aware, so this
   is glue work, not new rendering logic.
6. **Open question: is this `strata deploy destroy` or `strata destroy
   run`?** Both names appear across existing comments (`audit_run.py`
   says "`deploy destroy`"; `gitops-integration.md` says "`deploy
   destroy`/`strata destroy run`" as if undecided). `deploy destroy` reads
   more naturally as a subcommand of the existing `deploy` group
   (consistent with v1's own real CLI shape, confirmed in
   `v1-consumer-usage.md`'s Tier 2 list: `deploy status/history/health/
   destroy` are all `deploy`-group subcommands) — tentative preference,
   not yet decided.
7. **Timeout wiring** — once `destroy()` has a real call site,
   `_timeout_kwargs(step, stage, "destroy")` (the same helper
   `deploy_controller.py` already uses for `setup`/`check`/`plan`/`apply`)
   extends with zero new code, just a new call site passing `"destroy"`.
8. **Preflight pass** — `deploy_run()`'s `is_available()` preflight check
   is equally relevant here (fail fast before tearing anything down);
   reuse unchanged.
9. **Not yet investigated**: whether `destroy` should support
   `--scope`/`--stage` filtering identically to `deploy run`, or whether a
   partial-scope destroy (tearing down only `apps` while leaving `infra`
   standing) has real-world demand — no evidence either way yet from
   either reference consumer (neither uses `deploy destroy` at all today,
   per Tier 2 classification).

## Changelog

- 2026-10-09: Created. Triggered by confirming (via grep across
  `src/strata/commands/` and `src/strata/controllers/`) that `deploy
  destroy` has zero implementation in v2 today, despite three separate
  existing code comments already anticipating it
  (`audit_run.py`/`gitops-integration.md`/`cli-timeout.md`). Collected
  what ADR-0021's integration layer already built destroy-aware
  (`InfraIntegration.destroy()` for all four real integrations, audit
  manifest/metrics models' `destroy` action literal, the renderer's
  existing `destroyed` branch) against the real remaining gaps (no
  orchestrator, no CLI command, no `destroy_namespace()` for the
  container path). Restated `deploy-command.md`'s own phase 7
  `--force`/`-auto-approve` finding here since it's `destroy`-specific
  and belongs in this doc, not duplicated as new research.
