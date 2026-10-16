# `strata deploy` Commands — Design

- Status: current for `deploy run` (fully built — orchestrator, CLI, `TF_VAR_`
  injection, cross-step output context, `output.template`'s real render, all
  tested). Locking, SIEM/audit-sink ingestion, whole-run timeout, and
  `--force`'s real advisory-gate consumption are deliberately deferred scope
  boundaries (no critical-path evidence from either real consumer repo), not
  open questions. `deploy destroy` is **fully designed but not implemented**
  — tracked explicitly in its own Remaining Work section below, not hidden.
  The other ten v1 `deploy` subcommands (`status`/`history`/`health`/`lock`/
  `output`/`show`/`list`/`plan`-viewer/`drift`/`drift_history`/
  `acknowledge_drift`) are surveyed, none implemented.
- Last updated: 2026-10-16

## Overview

`strata deploy run` executes what `strata build run` only renders —
`plan`/`apply` against a workspace's provisioners (Terraform), and the
equivalent for Compose/Helm — using **fully** resolved values (secrets,
integration-backed variables/features included), unlike `build run`'s
deliberately-partial build-time resolution. Matches ADR-0022 D4's own
framing exactly: *"build run renders; it does not execute... deploy run
runs plan/apply."*

This doc consolidates what were three separate, scattered work docs
(`deploy run`'s own design, `deploy destroy`'s design, and a survey of
v1's ten other `deploy` subcommands) into one: `deploy run` is finished
and is the bulk of this doc's durable content; `deploy destroy` and the
surveyed subcommands are real, tracked, **not yet implemented** work,
kept here rather than scattered across separate work docs now that
`deploy run` itself has nothing left pending.

For pipelines that want each phase of `deploy run`'s sequence as its own
CI stage/approval gate — a `plan` stage reviewed before a separate, later
`apply` stage — see
[deploy-pipeline-stage-commands.md](deploy-pipeline-stage-commands.md):
`deploy setup`/`deploy check`/`deploy plan`/`deploy apply`, additive
subcommands on top of the one atomic `deploy run` this doc designs.

---

## `deploy run`

### Real evidence — confirmed CLI shape and workflow patterns (both real consumer repos)

Read directly from `.github/workflows/*.yml` in both reference repos
(corroborates `/memories/repo/v1-consumer-usage.md`'s census):

- **Universal sequence, both repos**: `strata validate --deep` →
  `strata build run --file $DEPLOYMENT_FILE` → `strata deploy run
  --file $DEPLOYMENT_FILE --force [--dry-run] [--stage NAME] [--scope
  infra|apps]`.
- **`haven/.github/workflows/deploy-infra.yml`** uses `--scope infra`
  explicitly, with its own comment explaining why: *"restricts this run
  to stages that declare `scope: infra`... this workflow is explicitly
  API-only (no SSH/kubeconfig setup), so it must never pick up
  applications_forge (scope: apps, ...) even when --stage is omitted."*
  `haven/config/deployment.yaml`'s real stages: `infrastructure_hearth`/
  `infrastructure_forge` (`scope: infra`), `applications_forge`
  (`scope: apps`, Helm) — `infra`/`apps` are this deployment's own
  convention, not a fixed vocabulary (`scope` is a free-form string,
  matching v1's own `deployment_model.py`: `scope: Optional[str]`).
- **Secret injection, confirmed both repos**: haven's own comment —
  *"the terraform deployer auto-injects each resolved secret as
  `TF_VAR_<key>` before plan/apply/destroy — no manual `TF_VAR_*`
  wiring needed here."* Cloud auth (`TERRAFORM_API_TOKEN`, Infisical
  machine identity, Azure OIDC) flows through plain env vars the
  workflow itself sets — a **different** channel from strata-resolved
  secrets, never confused with them. config-deploy's real (non-template)
  workflow has **zero** manual `TF_VAR_*`/secret wiring at all.
- **`--scope` is real but haven-specific** — config-deploy's workflows
  use `--stage`/`--dry-run`/`--force` only (pure-Terraform workspace, no
  Helm/apps stage to separate from infra). Both flags are real CLI
  surface; `--scope` just has a narrower real user base.
- **Cloud-provider auth happens *outside* strata entirely** in both
  repos — `deploy run` only resolves *strata-declared* secrets/variables
  and hands them to an already-authenticated `terraform`/`helm` binary;
  it is never responsible for cloud credential exchange itself.

### Current Design — what's built

- **`InfraIntegration.plan()`/`.deploy()`/`.destroy()`** — fully
  implemented, real subprocess calls, for all four integrations
  (`TerraformIntegration`, `ComposeIntegration`, `HelmIntegration`,
  `BaseGitOpsIntegration`). All accept an `env: Mapping[str, str] | None`
  kwarg that reaches `transport.run_command()`'s `merged_env =
  {**os.environ, **(env or {})}` — passing `TF_VAR_*` entries here
  **merges** with the full process environment, never replaces it, so
  `PATH`/cloud-auth env vars a CI workflow already set survive untouched.
- **`value_controller.resolve_values()`** resolves *every* store type
  (`constant`/`environment` direct, everything else — `vault`/
  `infisical`/`consul`/`etcd`/`azure-appconfig`/`artifact` — dispatched
  to a real `StoreIntegration.resolve()`/`resolve_artifact_field()`).
  Deploy run needs zero new resolution logic.
- **`ordered_by_depends_on()`** (`build_controller.py`) — the same
  topological sort `build_run()` already uses, reused as-is.
- **`TerraformIntegration.init()`** — real `terraform init
  -backend-config=... [-upgrade] [-reconfigure]`, called as its own
  step, never auto-chained inside `plan`/`deploy`/`destroy` — confirms
  the real, v1-precedented per-step sequence: **setup (init) → check
  (validate) → plan → apply**.
- **`deploy_controller.deploy_run()`** (`src/strata/controllers/
  deploy_controller.py`) — the real orchestrator; see "Orchestrator
  shape" below.
- **`src/strata/commands/deploy_command.py`** — the real CLI, matching
  `build_command.py`'s shape: positional `deployment` (no `-f FILE`,
  same reasoning as `build`/`validate`/`values` — v2 addresses documents
  by `(kind, name)`), `--path`/`--build-path`/`--force`/`--dry-run`/
  `--smoke-test`/`--follow`/`--stage`/`--scope`/`--pin`/
  `--output-max-age`, plus the full `--change-*`/`--change-approved-*`
  audit-trail flag set (docs/design/audit-trail.md). See
  [deploy-pipeline-stage-commands.md](deploy-pipeline-stage-commands.md)
  for the additive `setup`/`check`/`plan`/`apply` subcommands built on
  top of this same orchestrator.
- **`--smoke-test`/`--dry-run`/`--follow`** — three distinct execution
  modes plus live streaming; see
  [deploy-plan-preview.md](deploy-plan-preview.md)
  ([ADR-0030](../decisions/0030-deploy-run-plan-preview-and-streaming.md))
  for the full, current behavior (not repeated here).
- **`output.template`'s real Jinja2 render** — `build run` only
  validates references (every value needed for a full render is
  deploy-time-only); `deploy run` renders for real, via
  `InfraIntegration.render_output_template()`, before `init()`.
- **Helm/Compose orchestration** (`deploy_namespace()`, docs/design/
  gap_fit_v1.md gap #13) — a container-capable step (`Capability.
  CONTAINER in integration.CAPABILITIES`) never goes through Terraform's
  init/validate/plan/deploy sequence — it calls `integration.
  deploy_namespace(namespace, modules, ...)` once per targeted
  namespace, filtered to that integration's own module `TYPE` (mirrors
  v1's real `DeployerFactory.resolve_type()`: the step's own provisioner
  picks the tool, never inferred from namespace linkage). `HelmIntegration.
  deploy_namespace()` reads each module's `meta.yaml`, resolves the chart
  reference (OCI/Helm-registry remotes), and delivers secrets via
  `--set-string`. `ComposeIntegration.deploy_namespace()` resolves the
  namespace's already-merged compose file and does one `docker stack
  deploy` for the whole namespace. The orchestrator dispatches purely on
  `integration.CAPABILITIES` — never a tool-name string comparison.

### Orchestrator shape

```
strata deploy run DEPLOYMENT [--path PATH] [--build-path PATH] --force
  [--smoke-test | --dry-run] [--follow] [--stage NAME] [--scope LABEL]
  └─ deploy_command.py: deploy_run_command()
       └─ diagnostics = deploy_run(context, deployment, build_path, force=force,
              dry_run=dry_run, smoke_test=smoke_test, stage=stage, scope=scope,
              on_step=run.step, on_line=on_line, pin=pin, output_max_age=output_max_age)

deploy_run(...)
  ├─ deployment, workspace, environments = resolve_deployment()/reachable_environments()
  ├─ resolved = resolve_values(...)                      # FULL resolution — secrets included
  ├─ steps = ordered_by_depends_on(workspace.spec.execution)
  ├─ steps = filter by --stage/--scope
  ├─ preflight: integration.is_available() for every filtered step, fail fast
  │    before any step runs (docs/design/deploy-command.md's own "Structural
  │    gotchas from v1": a later step's missing tool/credential must never
  │    be discovered only after an earlier step already made real changes)
  │
  └─ for step in steps:
       path = <source-keyed build_path — matches build_run()'s own
               source_sync.sync_source() destination exactly>
       env = tf_var_env(resolved, integration.ENV_VAR_PREFIX)
       tokens = {**resolved.values, **this step's visible upstream outputs}
       backend_config = resolve_value_tokens_in_mapping(provisioner.backend.configuration, tokens)

       if Capability.CONTAINER in integration.CAPABILITIES:
           deploy_namespace() per targeted namespace, filtered to integration.TYPE
           continue

       render output.template if declared
       integration.init(path, backend_config=backend_config, env=env)
       integration.validate(path, env=env)
       plan_result = integration.plan_or_warn(path, out_file=f"{step.name}.tfplan", env=env, ...)
       # dry_run stops here; a tool with no real preview is a warning, not a hard failure
       deploy_result = integration.deploy(path, plan_file=f"{step.name}.tfplan", env=env, ...)
       # applying a saved plan file needs no -auto-approve — Terraform never
       # prompts for one regardless of any flag (confirmed in v1's own
       # TerraformDeployer.apply(), which never reads `force` at all) — so
       # `force` plays no role in the normal apply path (it is currently
       # inert, kept in the signature for a future advisory-gate consumer)
       step_outputs[step.name] = collect_step_outputs(...)
       output_store.record(step.name, ...)   # durable, if a workspace output_store is configured
```

### Cross-step output context — deploy-time value chaining

A step's apply (e.g. Terraform creating a VM) can produce values a
*later*, dependent step needs (e.g. a Helm step needing the VM's IP).

**Same-invocation (in-memory)**: a new 4th value-token kind,
`${output:<step_name>.<key>}`, alongside `${var:}`/`${secret:}`/
`${feature:}` — widens `VALUE_TOKEN_PATTERN`, resolved via
`resolve_value_tokens_in_mapping()` unchanged. Dependency-scoped (a step
may only reference a step it transitively `depends_on`, enforced via
`_upstream_step_names()`) and namespaced (`<step>.<key>`, never a flat
ambient pool) — unlike v1's real mechanism (a single mutable
`ResolvedValues` object every later stage sees globally, regardless of
`depends_on`, with silent key-collision risk on shadowing) — checked
against how other platforms solve this (GitHub Actions' `steps.<id>.
outputs`, GitLab CI `needs:`, Terraform `terraform_remote_state`): every
one of them scopes to an explicit dependency and keeps outputs
namespaced; v1's flat-pool behavior is the outlier, not the norm.
Collected via `collect_step_outputs()` (`getattr(integration, "output",
None)`, Terraform-specific like `init`/`validate` — Compose/Helm
integrations contribute nothing, no native "outputs" concept in either
real reference repo). Only resolves inside schema-owned string fields
(`provisioner.backend.configuration`, and `dns`/`networks`/`firewalls`
documents claimed by an owning step) — never folded into `env=`/
`TF_VAR_*` (a `"step.key"`-shaped name isn't a valid Terraform
identifier, and ambient env injection is the exact v1 flaw this design
avoids).

**Cross-invocation (durable)**: a later, wholly separate `deploy run`
process (a different CI job, a fresh runner) reading an earlier
invocation's outputs is solved by `DeploymentOutputStore`
([ADR-0036](../decisions/0036-cross-run-key-value-output-store.md)) — a
pluggable key/value backend the producing step durably writes to
(`output_store.record()`, immediately after collection, not batched to
run-end) and a later invocation reads from (`_merge_durable_outputs()`,
transparently filling in any upstream step not produced by *this*
invocation's own in-memory `step_outputs`). A durable record that was
simply never written is forgiving (that step may not even be
referenced); one that exists but is stale or unreachable fails loudly,
immediately, before the referencing step's tool ever runs — never a
silent wrong value. `strata validate`'s own static check
(`WorkspaceService.claimed_document_names()`,
`unresolved_value_tokens()`'s `output_claimed` parameter) no longer
false-rejects a claimed `${output:...}` reference, and
[output-declarations.md](output-declarations.md)'s dynamic pre-flight
check reports every referenced key missing from a step's own collected
outputs before any resolution is attempted.

### Structural gotchas from v1 — adopted vs. deliberately deferred

Found by reading v1's real `run_deploy_command.py` execution flow
directly:

- **Adopted**: preflight-validate every filtered step's tool *before*
  running anything; filtering happens *before* preflight-checking (a
  disabled/filtered-out step's tooling is never required); `on_failure:
  continue` steps are still preflight-checked but a failure there is a
  warning, not a hard stop.
- **`on_failure: "rollback"` does nothing different from `"stop"` in
  v1's actual code** (confirmed: the word appears only in comments,
  never in a branch condition) — not ported as a distinct behavior.
- **Deliberately deferred, no critical-path evidence from either real
  consumer**: deployment-level locking (Terraform's own remote backends
  already provide real state-locking independent of strata for the
  Terraform case specifically; Helm/Compose have much weaker or no
  native equivalent — a fact for whoever designs locking later, not a
  decision made now); SIEM/audit-sink ingestion beyond the manifest/
  metrics `audit_run.py` already writes; a whole-run timeout + hard-kill
  (v2 already has *per-command* timeouts on `plan()`/`.deploy()`'s own
  `timeout` kwarg — partial mitigation, not full parity with v1's
  worker-thread/shutdown-coordinator mechanism).

### `scope` field placement

`ProvisioningStepModel.scope: str | None` (workspace side), not
`DeploymentStageModel` (deployment side) — a free-form label (v1's real
`deployment_model.py`: `scope: Optional[str]`, not a closed enum).
Whether a step is "infra" or "apps" is a fact about what the step does,
not about which environment runs it — it has no business being
re-declared per deployment, matching the same workspace/deployment
separation-of-concerns `ProvisioningStepModel`'s own "this step realizes
topology X is derived, not declared" precedent (ADR-0011) already
established. `--scope LABEL` filters `workspace.spec.execution` directly,
independent of which deployment/environment is targeted.
(Disambiguation: an unrelated, identically-named
`DeploymentEnvironmentRef.scope` exists for the *promotion* system's own
environment-file targeting — a different concept sharing a field name,
not to be conflated.)

### `--force` semantics

Read directly from v1's real installed source
(`strata/deployers/terraform_deployer.py`,
`run_deploy_command.py`): **`--force` is not Terraform's
`-auto-approve` for a normal `deploy run` apply.**
`TerraformDeployer.apply()` always applies a *saved plan file*
(`terraform apply <stage>.tfplan`) — Terraform never prompts to apply a
saved plan regardless of any flag, so `self.force` is never even read
inside `apply()`. `-auto-approve` only appears for `destroy` (see
below), which has no saved-plan-file equivalent. For `deploy run`,
v1's real `--force` instead overrides *advisory* approval gates (AI
plan review, a promotion-override guard) — neither of which v2 has
built (Tier 2, no critical-path evidence). **v2 conclusion**:
`deploy_run()` applies via the same saved-plan-file mechanism
(`plan(out_file=...)` → `deploy(plan_file=...)`), so the normal path
needs no `auto_approve` at all; `force` is currently inert, kept in the
signature/CLI so a future advisory gate can consume it without changing
the call shape.

---

## `deploy destroy` — designed, not implemented

`strata deploy destroy` would tear down what `deploy run` created — v1's
real command, Tier 2 (`/memories/repo/v1-consumer-usage.md`: neither
real consumer repo's CI invokes it). **No such command exists in v2
today.** This section is fully designed, evidence-based, but genuinely
unbuilt — see "Remaining Work" below for the concrete gap list.

### Real evidence

- **`-auto-approve` only appears for `destroy`.**
  `TerraformDeployer.destroy()` passes `auto_approve=self.force` —
  unlike `apply()`, `terraform destroy` has no saved-plan-file
  equivalent and genuinely prompts interactively unless told not to.
- **`deploy destroy`'s CLI is the only one of the two commands that
  enforces "exactly one of `--force`/`--dry-run` required"** — stricter
  than `deploy run`, matching the higher blast radius of an
  irreversible teardown.
- **v1's real `stage_selection.py`** (shared between `deploy run`/
  `deploy destroy` for "which stages run, and why the others do not"):
  `StageSelectionMode.DESTROY` **never gates on `enabled`** — its own
  docstring: *"gating a teardown would strand infrastructure created
  while the flag was on."* It also **never reverses step order** — its
  own docstring admits this is a gap: *"Declaration order, because a
  correct teardown needs the reverse order and that is a decision
  ADR-0083 does not make."* v2's design below does not copy that
  acknowledged gap — it reverses `ordered_by_depends_on()`'s output,
  tearing down a step's dependents before the step itself.
- **v1's real Helm/Compose destroy shape**: `HelmDeployer.destroy()`
  loops `helm uninstall --namespace <ns> <release>` once per module/
  release; `ComposeDeployer.destroy()` is one `docker stack rm
  <namespace>` call (Compose already merges per-namespace at build
  time, nothing to loop over).

### Current Design — what's already built, destroy-aware

- **`InfraIntegration.destroy()`** — `@abstractmethod`, real
  implementations in all four integrations already (`terraform destroy
  -auto-approve`, `docker stack rm`, `helm uninstall`,
  `BaseGitOpsIntegration.destroy()` via `git_push.remove_file()`).
- **`DeploymentStageTimeoutsModel.destroy: int | None`** — the schema
  field already exists (ADR-0032), with a hardcoded default on
  `TerraformIntegration.destroy()` itself — wired for *timeout*, just
  never reached because nothing calls `destroy()` yet.
- **Audit models already treat `destroy` as first-class**:
  `AuditManifestModel.action: Literal["build", "deploy", "destroy"]`,
  and `audit_event_rendering.py`'s renderer already branches on it
  (`"destroyed"` vs. `"completed"`) — only `audit_run.py`'s glue (which
  action to pass, when to call it) is the real gap.
- **No `destroy_namespace()` equivalent exists** for the
  `Capability.CONTAINER` path — the real, concrete gap for Helm/Compose.

### Orchestrator shape (proposed)

A deliberate variant of `deploy_run()`'s own shape, not an independent
design:

```
strata deploy destroy DEPLOYMENT [--path PATH] [--build-path PATH]
  (--force | --dry-run)   # exactly one required — stricter than deploy run
  [--stage NAME] [--scope LABEL]

destroy_run(...)
  ├─ deployment, workspace, environments, resolved = <identical setup to deploy_run()>
  ├─ steps = list(reversed(ordered_by_depends_on(workspace.spec.execution)))
  ├─ steps = filter by --stage/--scope only — NO `enabled` filtering
  │     (matches v1's real StageSelectionMode.DESTROY exactly)
  └─ for step in steps:
       if Capability.CONTAINER in integration.CAPABILITIES:
           integration.destroy_namespace(namespace, modules, tokens=..., dry_run=dry_run)  # NEW method
       else:
           integration.init(...); integration.validate(...)   # still needed — must target the real state
           if not dry_run:
               integration.destroy(path, auto_approve=force, env=env)   # NO saved-plan-file step
           else:
               integration.plan_or_warn(path, destroy=True, ...)         # terraform plan -destroy preview

  if diagnostics.ok and not dry_run:
      output_store.clear(s.name for s in steps)   # NEW method — same CAS loop record() uses,
                                                    # popping destroyed steps' keys instead of adding one
```

Deliberately **not** carried over from `deploy_run()`: no saved-plan-file
mechanism (`destroy()` has no two-phase plan-then-apply shape in any
integration — `--force` maps directly to `auto_approve`, the one case
`force` is NOT inert); no output-context collection (nothing meaningful
to collect post-teardown); no `output.template` render (no "final
state" to describe).

### Remaining Work — `deploy destroy` (not yet implemented)

1. **`InfraIntegration.destroy_namespace()`** — new ABC method, same
   non-abstract/raise-by-default base shape as `deploy_namespace()`.
   `HelmIntegration` loops `destroy()` per module; `ComposeIntegration`
   does one `destroy(namespace=...)` call.
2. **`destroy_run()` controller function** — the orchestrator above,
   reusing every `deploy_run()` resolution helper unchanged.
3. **`--force`/`--dry-run` mutual-exclusion + required-one-of** — a
   `UsageError` in the command layer, stricter than `deploy run`'s own
   handling.
4. **CLI command** — `strata deploy destroy`, a subcommand on the
   existing `deploy` group (decided — matches v1's own CLI shape,
   avoids a second top-level command group for one verb), same
   `--path`/`--build-path`/`--stage`/`--scope` plus the audit
   change-reference flags.
5. **Audit wiring** — extend `audit_run.py`'s
   `finalize_and_distribute_deploy_audit()` for `action="destroy"`; the
   renderer is already destroy-aware, this is glue work only.
6. **Timeout wiring** — `_timeout_kwargs(step, stage, "destroy")`
   extends with zero new code once `destroy()` has a real call site.
7. **`DeploymentOutputStore.clear()`** — new method, same CAS
   read-modify-write loop `record()` already uses, popping destroyed
   steps' keys. Called once after the whole run succeeds (not per
   step), so a partial failure leaves every already-recorded output
   intact for a retry — prevents a later `deploy run` of new
   infrastructure from reading stale pre-destroy outputs still inside
   the trust window.
8. **Not yet investigated**: whether a partial-scope destroy (tearing
   down only `apps` while leaving `infra` standing) has real-world
   demand — no evidence either way from either reference consumer
   (neither uses `deploy destroy` at all today).

---

## Other `deploy` subcommands — surveyed, nothing built

v1 ships ten more subcommands under `commands/deploy/`, all Tier 2
(`/memories/repo/v1-consumer-usage.md`: *"operational/observability
commands, not on the critical deploy path"* — neither real consumer
repo's CI invokes any of them).

**The one finding that matters most**: four of the ten are **already
built in v2** under a different name — `deploy list-values`/
`get-values`/`set-values`/`resolve-values` are deployment-scoped value
inspection, and `strata values list/get/set/resolve DEPLOYMENT ...`
([values-secrets-command.md](values-secrets-command.md)) already does
the identical job. Not a gap; do not re-implement under a `deploy`
subcommand.

| Command                                            | What it does (v1's real source)                                                                                                        | v2 status                                                                                                                                                                                                                                                                                     |
| -------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `deploy status`                                    | Per-stage live infra status: resource count, output count/keys, last apply serial, backend reachability. `--offline` reads cache only. | None. Genuinely new.                                                                                                                                                                                                                                                                          |
| `deploy history`                                   | Reads `.strata/logs/` for `deploy_run`/`deploy_destroy` events, renders a table.                                                       | None — v2 has no queryable execution-history reader (only `audit_run.py`'s forward-looking write).                                                                                                                                                                                            |
| `deploy health`                                    | Runs `health_checks` (HTTP GET/TCP connect) against live Terraform outputs.                                                            | **Closest gap** — `DeploymentHealthCheckModel`/`DeploymentStageModel.health_checks` already exist in the schema; only the command that runs them is missing.                                                                                                                                  |
| `deploy lock status`/acquire/release               | Deployment-level state lock via a pluggable backend.                                                                                   | None — same deliberately-deferred locking gap noted under `deploy run` above.                                                                                                                                                                                                                 |
| `deploy output`                                    | Shows outputs from a local cache or durable outputs directory, human-facing.                                                           | Partial — `DeploymentOutputStore` persists outputs for *programmatic* consumption; no human-facing inspection CLI exists yet.                                                                                                                                                                 |
| `deploy show`                                      | Prints resolved deployment config (remotes, stages, resolved env, secrets masked); gating is disclosed, never applied.                 | Partial overlap with `values list`; adds deployment-wide context that command doesn't show.                                                                                                                                                                                                   |
| `deploy list`                                      | Scans every `kind: deployment` file, prints metadata — built for CI matrix generation.                                                 | None. No evidence either real repo needs this.                                                                                                                                                                                                                                                |
| `deploy plan` (v1's viewer)                        | Shows a human-readable summary from an already-saved `.tfplan`, entirely offline.                                                      | **Mostly superseded** — v2's own `strata deploy plan` ([deploy-pipeline-stage-commands.md](deploy-pipeline-stage-commands.md)) computes *and* shows a diff by default, a strict superset for the common case. The one remaining gap: re-viewing an already-saved plan without recomputing it. |
| `deploy drift`/`drift_history`/`acknowledge_drift` | `terraform plan -detailed-exitcode` without saving, severity classification, persisted history, suppression.                           | None. A whole new subsystem (`DriftController`, a history store) — zero v2 equivalent today. Largest unscoped gap in this survey.                                                                                                                                                             |

### Remaining Work — other subcommands (no evidence yet, build only if asked)

1. **`deploy health`** — the easiest real wire-up: resolve live
   Terraform outputs per stage (reuse `collect_step_outputs()`),
   resolve each check's target, run an HTTP GET/TCP connect, report
   pass/fail.
2. **`deploy status`/`history`/`output`/`show`** — read-only
   observability surfaces over data v2 already produces; no CLI today.
3. **`deploy lock`** — same deferred locking gap as `deploy run`.
4. **`deploy plan` (v1's viewer)** — only the narrow "re-view a stale
   saved plan without recomputing" behavior remains unaddressed.
5. **`deploy drift`/`drift_history`/`acknowledge_drift`** — needs its
   own dedicated design pass (severity scheme, history persistence
   shape, acknowledgement semantics) before any implementation — not a
   small wire-up.

---

## Related Decisions

- [ADR-0021](../decisions/0021-integration-layer.md) — defines
  `InfraIntegration.plan()`/`.deploy()`/`.destroy()`, already
  implemented for all four real integrations.
- [ADR-0022](../decisions/0022-strata-build-run.md) — D4's
  render-vs-execute split this whole doc builds on.
- [ADR-0023](../decisions/0023-build-output-rendering.md) — what
  `build run` already wrote to disk; `deploy run` executes against it,
  never re-renders.
- [ADR-0027](../decisions/0027-strata-deploy-run.md) — the point-in-time
  decision record summarizing `deploy run`'s settled orchestrator shape.
  `destroy_run()` is a deliberate variant of it, not an independent
  decision — no separate ADR needed for destroy.
- [ADR-0030](../decisions/0030-deploy-run-plan-preview-and-streaming.md)
  / [deploy-plan-preview.md](deploy-plan-preview.md) — `--smoke-test`/
  `--dry-run`/`--follow`, the current, full execution-mode behavior.
- [ADR-0032](../decisions/0032-deploy-time-step-timeout-overrides.md) —
  the `destroy` timeout phase/field this doc's destroy section would
  finally give a real call site.
- [ADR-0036](../decisions/0036-cross-run-key-value-output-store.md) —
  `DeploymentOutputStore`, the durable cross-invocation output mechanism.
- [deploy-pipeline-stage-commands.md](deploy-pipeline-stage-commands.md)
  — the additive `setup`/`check`/`plan`/`apply` subcommands built on top
  of this doc's orchestrator.
- [value-token-resolution.md](value-token-resolution.md) — the Value
  Supply Mechanisms A/B/C taxonomy; Mechanism B is the deploy-time-only
  resolver this doc's token substitution uses.
- [docs/design/audit-trail.md](audit-trail.md) — Layer 2's audit
  manifest/metrics models, already `destroy`-aware; `deploy destroy`'s
  Remaining Work item 5 extends the existing glue, not the models.
- [docs/design/gitops-integration.md](gitops-integration.md) — first
  flagged the `deploy destroy` gap, explicitly out of scope there.
- `/memories/repo/v1-consumer-usage.md` — the real-CI-usage census both
  the destroy and other-subcommands sections ground their Tier 2
  classification in.

## History

- `deploy run`'s real gap, found by reading both reference repos' actual
  CI workflows and v1's real installed source directly (not inferred):
  `InfraIntegration.plan()`/`.deploy()`/`.destroy()` and full-featured
  `resolve_values()` were already built and working, unused by anything
  — a materially smaller true gap than a fresh ADR-0022 read suggested.
- `scope`'s placement (`ProvisioningStepModel`, not `DeploymentStageModel`)
  and `--force`'s real semantics (overrides advisory gates, never
  `-auto-approve` for a normal apply) were both settled by reading v1's
  real installed source directly rather than assuming from its CLI
  surface or doc comments alone — both findings reversed an initial,
  more surface-level assumption.
- Cross-invocation output access went through three designs before
  landing on the shipped one: an initial "opportunistic live
  re-collection" idea (re-run `terraform init`/`output` against a
  producing step's own remote backend from a later invocation) was
  designed and partly validated, then superseded entirely by
  `DeploymentOutputStore` (ADR-0036) — a pluggable durable write/read
  mechanism that works for any output-capable tool, not just ones with
  a real remote Terraform backend. A related, independent bug was found
  and fixed along the way: `strata validate` blanket-rejected every
  `${output:...}` token, even already-correctly-claimed ones, because
  `unresolved_value_tokens()` was never updated after claiming was
  wired into deploy-time resolution — fixed via a `claimed_document_
  names()`/`output_claimed` parameter, independent of either output-
  access design.
- `deploy destroy`'s reverse-dependency-order and never-gate-on-`enabled`
  decisions are both grounded directly in v1's own `stage_selection.py`
  — notably, v1's own docstring admits it never decided reverse order
  at all (its real `order_stages()` runs unreversed for destroy); v2
  deliberately does not copy that acknowledged gap.
- This doc consolidates three previously separate work docs
  (`deploy-command.md`, `deploy-destroy-command.md`,
  `deploy-operational-commands.md`) now that `deploy run` itself has
  nothing left pending — `deploy destroy` and the other-subcommands
  survey remain real, tracked, unimplemented work, kept in this doc's
  own Remaining Work sections rather than scattered across separate
  files.
