# `strata deploy run` — Execution Orchestrator

- Status: accepted — `--dry-run`'s exact preview semantics within
  `deploy run` (left unspecified here beyond listing the flag) are now
  decided by [ADR-0030](0030-deploy-run-plan-preview-and-streaming.md),
  which also adds `--smoke-test`/`--follow`; every other decision in this
  ADR stands unchanged.
- Date: 2026-09-27
- Related: [ADR-0021](0021-integration-layer.md) (the integration layer
  this consumes — `InfraIntegration.plan()`/`.deploy()`/`.destroy()`,
  `StoreIntegration.resolve()` — already implemented before this ADR),
  [ADR-0022](0022-strata-build-run.md) (D4's render-vs-execute split this
  whole decision builds on; also the origin of `ResolvedWorkspaceGraph`/
  `sync_source()`/`ordered_by_depends_on()`, reused unchanged here),
  [ADR-0023](0023-build-output-rendering.md) (what `build run` already
  wrote to disk before this command executes against it)

See also: [docs/work/deploy-command.md](../work/deploy-command.md)
(full design history, real evidence from both reference repos, and every
correction made along the way — this ADR summarizes the outcome, that doc
is the living record)

## Context and Problem Statement

`strata build run` (ADR-0022) renders a workspace's provisioners into
on-disk artifacts but never executes anything — by design (ADR-0022 D4).
Nothing in v2 called `InfraIntegration.plan()`/`.deploy()`/`.destroy()`,
even though all three were fully implemented, real, working code with zero
callers. Both real reference repos' CI (haven, config-deploy)
universally follow `build run` with `strata deploy run --file <file>
--force [--dry-run] [--stage X] [--scope infra|apps]` — the execution half
of the same pipeline, with no v2 equivalent at all.

Beyond "wire up plan/deploy", real evidence surfaced three concrete gaps
with no home in any prior design: `ProvisioningStepModel` had no `scope`
field to support the real, confirmed `--scope infra` filter;
`provisioner.backend.configuration`'s `${var:}`/`${secret:}`/`${feature:}`
tokens had no deploy-time resolver (Mechanism B, long deferred across
every `build_run`-era doc); and a step's real infrastructure outputs (e.g.
a VM's IP) had no way to reach a later, dependent step.

## Considered Options

- **Extend `build_run()` itself to also execute.** Rejected outright —
  directly contradicts ADR-0022 D4's explicit, deliberate render/execute
  split; would also make `--dry-run`'s meaning ambiguous between "don't
  render" and "don't execute".
- **`scope` on `DeploymentStageModel`** (the deployment side), matching
  where v1's real data happens to declare it. Rejected after reasoning
  through the workspace/deployment separation of concerns already
  established elsewhere in the schema (`ProvisioningStepModel`'s own
  `depends_on`/topology-realization precedent, ADR-0011): whether a step
  is "infra" or "apps" is a fact about what the step *does*, not which
  environment runs it, so re-declaring it per deployment would reproduce
  the exact config-drift risk that split exists to prevent.
- **`force` maps directly to terraform's `-auto-approve`.** Rejected after
  reading v1's real installed source (`xyz-strata` v1.11.2) directly:
  `TerraformDeployer.apply()` always applies a *saved plan file*, which
  Terraform never prompts for regardless of any flag — `force` is never
  even read there. `-auto-approve` only appears in v1's `destroy()`. V1's
  real use for `--force` on `run` is bypassing *advisory* approval gates
  (AI plan review, promotion overrides), neither of which v2 has built.
- **Global, flat, ambient output-passing** (v1's real
  `ResolvedValues.stage_outputs` — every later stage sees every earlier
  stage's outputs, injected as bare `TF_VAR_<key>` env vars). Rejected
  after comparing against how GitHub Actions/GitLab CI/Azure Pipelines/
  Terraform remote state all actually do this: every one of them scopes
  output visibility to an explicit dependency declaration and keeps
  outputs namespaced — v1's global flat pool was the outlier, not the
  norm, and a real footgun (silent collision, no provenance) at any scale
  beyond haven's own.
- **Saved-plan-file apply + dependency-scoped, namespaced output tokens**
  (chosen) — see Decision Outcome.

## Decision Outcome

Chosen: **a new `deploy_controller.deploy_run()` orchestrator** that walks
`workspace.spec.execution` (via the existing `ordered_by_depends_on()`),
filtered by `--stage`/`--scope`, resolves every value fully (via
`resolve_values()`, not `build_value_references()`'s build-time-safe
subset), and executes each step through `init → validate → plan → deploy`
— `init`/`validate` accessed via `getattr(integration, name, None)` so the
loop stays tool-agnostic (Compose/Helm simply don't have them).

Concrete decisions:
- **`scope: str | None`** added to `ProvisioningStepModel` (workspace
  side), free-form (matching v1's real, undocumented-as-enum semantics),
  filtering `workspace.spec.execution` directly, independent of which
  deployment/environment is running.
- **Apply via a saved plan file**: `plan(path, out_file="<step>.tfplan")`
  → `deploy(path, plan_file="<step>.tfplan")` — no `-auto-approve` on the
  normal path, matching v1's real mechanism exactly. `force` is currently
  inert (kept in the signature/CLI for a future advisory gate to consume,
  without changing the call shape).
- **`tf_var_env()`**: a small, named function mapping every resolved
  value to `TF_VAR_<key>`, the only place a Terraform-specific env prefix
  is ever added.
- **Cross-step output context**: a new, dependency-scoped
  `${output:<step_name>.<key>}` value-token kind (a 4th kind alongside
  `var`/`secret`/`feature`, reusing the existing token-resolution
  machinery unchanged). A step's outputs are collected after a successful
  `deploy()` (`terraform output -json`, parsed; Compose/Helm integrations
  simply contribute nothing) and made visible only to steps that
  (transitively) `depends_on` it — never folded into `env=`/`TF_VAR_`,
  since a `"step.key"`-shaped name isn't a valid Terraform identifier and
  ambient injection was the exact v1 behaviour rejected above.
- **`output.template`'s actual render**: `InfraIntegration.
  render_output_template()`, base-implemented (tool-agnostic, matching
  `prepare()`'s own dispatch), using every value now fully resolved —
  `build run` could only ever validate this field's references
  (ADR-0023 D3), never render them.
- **CLI**: `strata deploy run DEPLOYMENT [--path] [--build-path] [--force]
  [--dry-run] [--stage] [--scope]`, matching `build_command.py`'s
  established shape exactly (positional deployment name, no `-f FILE`).

## Consequences

- Good: zero new resolution machinery needed for secrets/integration-
  backed values — `resolve_values()` (ADR-0021 Phase 4) already did the
  hard part; this orchestrator is the first real caller.
- Good: the orchestrator's shape leaves explicit extension points
  (a preflight-validation pass, per-step hooks, an unconditional
  cleanup point) for locking/SIEM/audit to slot into later without a
  rewrite, even though none of that is built now.
- Good: a genuinely pre-existing bug was caught and fixed in the
  process — `deploy_run()`'s step directory originally didn't match
  where `build_run()` actually materialises a sourced provisioner
  (`build_path / (source.target_path or source.source_path)`, keyed by
  source location, not by whichever step references it); every earlier
  phase's tests passed anyway since they never touched the real
  filesystem, until `output.template`'s render did.
- Bad: `store: artifact` variables failed to resolve at deploy time —
  `resolve_values()`'s dispatch had no case for `VariableStoreType.
  ARTIFACT` at all (that special-casing only existed in
  `build_value_references()`'s build-time path), and since `deploy_run()`
  aborts on any single key's resolution failure, one unrelated `store:
  artifact` variable anywhere in scope blocked the whole run. **Fixed
  same-day**: `resolve_values()` now special-cases it identically,
  calling `resolve_artifact_field()` directly (see Remaining Work).
- Bad: `force` currently does nothing — real, correct v1 behaviour (skip
  an advisory approval gate) has no v2 gate to skip yet. Not misleading in
  practice (every real invocation in both reference repos always passes
  it regardless), but worth remembering it is not a no-op by v1's own
  definition, only by v2's current scope.
- Bad: Compose/Helm's own outputs equivalent (if any) is unconfirmed by
  real evidence — neither reference repo's Helm/Compose stage shows a
  comparable pattern to design against yet.
