# `strata deploy run` Command — Design

- Status: partially-implemented — orchestrator, CLI, `TF_VAR_` injection,
  cross-step output context, and `output.template`'s actual render are all
  built and tested (see Implementation Plan below); locking, SIEM/audit,
  whole-run timeout, and `--force`'s real advisory-gate consumption are
  deliberately deferred (no real evidence forces them yet); a confirmed
  gap (not just a hypothetical) remains for `store: artifact` at deploy
  time (Remaining Work item 8)
- Last updated: 2026-09-27

## Overview

`strata deploy run` executes what `strata build run` only renders —
`plan`/`apply` against a workspace's provisioners (Terraform), and the
equivalent for Compose/Helm — using **fully** resolved values (secrets,
integration-backed variables/features included), unlike `build run`'s
deliberately-partial build-time resolution. Matches ADR-0022 D4's own
framing exactly: *"build run renders; it does not execute... deploy run
runs plan/apply."*

This doc exists because far more of the work is already built than a
fresh read of ADR-0022 alone suggests — `InfraIntegration.plan()`/
`.deploy()`/`.destroy()` and full-resolution `resolve_values()` are both
real and working today, just never called by anything. The remaining gap
is the orchestrator and CLI layer wiring them together, plus a handful of
deploy-time-only mechanisms every prior `build run`-era design doc
explicitly deferred here (token substitution, `output.template`'s actual
render).

## Real evidence — confirmed CLI shape and workflow patterns (both repos)

Read directly from `.github/workflows/*.yml` in both reference repos
(2026-09-27; corroborates `/memories/repo/v1-consumer-usage.md`'s
2026-09-23 census):

- **Universal sequence, both repos**: `strata validate --deep` →
  `strata build run --file $DEPLOYMENT_FILE` → `strata deploy run
  --file $DEPLOYMENT_FILE --force [--dry-run] [--stage NAME] [--scope
  infra|apps]`.
- **`haven/.github/workflows/deploy-infra.yml`**: uses `--scope infra`
  explicitly, with its own comment explaining why: *"restricts this run
  to stages that declare `scope: infra` (infrastructure_hearth,
  infrastructure_forge) — this workflow is explicitly API-only (no
  SSH/kubeconfig setup), so it must never pick up applications_forge
  (scope: apps, ...) even when --stage is omitted."*
- **`haven/config/deployment.yaml`** (v1 syntax) — real stage shape:
  `{name, topology, scope: <free-form label>, secrets: [...], provisioner
  (Helm stage only)}`. Three real stages: `infrastructure_hearth`
  (`scope: infra`), `infrastructure_forge` (`scope: infra`),
  `applications_forge` (`scope: apps`, `provisioner: haven_helm`) — `infra`/
  `apps` are this deployment's own convention, not a fixed vocabulary (see
  Remaining Work item 1 — v1's real `scope` field is a free-form string).
- **Secret injection, confirmed both repos**:
  - haven's own comment: *"the terraform deployer auto-injects each
    resolved secret as `TF_VAR_<key>` before plan/apply/destroy — no
    manual `TF_VAR_*` wiring needed here."* Cloud auth
    (`TERRAFORM_API_TOKEN`, Infisical machine identity) flows through
    plain env vars set by the workflow itself — a **different**
    channel from strata-resolved secrets, never confused with them.
  - cfg-int-deployment's real (non-template) `deploy-spoke-z01-s01.yml`
    has **zero** manual `TF_VAR_*`/secret wiring at all — Azure OIDC
    login (`azure/login@v2`) authenticates the process, then `strata
    deploy run` alone must be resolving and injecting every
    `store: azure-keyvault`/`azure-appconfig` secret itself. (Its
    sibling `deploy.yml`, a generated template, shows 3
    commented-out *manual* alternatives — Key Vault CLI, Vault CLI,
    plain GitHub secrets — presumably scaffolding for repos without a
    supported store backend configured yet, not evidence against
    auto-injection.)
- **`--scope` is real but haven-specific** — cfg-int-deployment's two
  real workflows use `--stage` and `--dry-run`/`--force` only, never
  `--scope` (pure-Terraform workspace, no Helm/apps stage to separate
  from infra). Both flags are real; `--scope` just has a narrower real
  user base.
- **Cloud-provider auth happens *outside* strata entirely** in both
  repos (Azure OIDC login, Terraform Cloud API token via env var) —
  `deploy run` only needs to resolve *strata-declared* secrets/variables
  and hand them to the already-authenticated `terraform`/`helm` binary;
  it is never responsible for cloud credential exchange itself.

## Current Design — already built, confirmed by reading the code directly

Nothing here was designed for this doc — it's what exists today, found
while investigating what `deploy run` would need to call:

- **`InfraIntegration.plan()`/`.deploy()`/`.destroy()`** — fully
  implemented, real subprocess calls, for **all three** real
  integrations:
  - `TerraformIntegration`: real `terraform plan`/`apply`/`destroy`
    argv (`-var-file`, `-var`, `-out`, `-target`, `-auto-approve`,
    `-destroy`, `-detailed-exitcode`).
  - `ComposeIntegration`/`HelmIntegration`: same shape, own tool's argv.
  - None of these are stubs or `NotImplementedError` — they run the
    real command via `self.run()` today.
  - All three accept an `env: Mapping[str, str] | None` kwarg that
    reaches `transport.run_command()`'s `merged_env = {**os.environ,
    **(env or {})}` — confirmed by reading it directly: passing
    `TF_VAR_*` entries here **merges** with the full process
    environment, never replaces it. `PATH`/cloud-auth env vars the
    workflow already set (Azure OIDC, `TF_TOKEN_*`) survive untouched —
    item 3 below has nothing left to design on the transport side, just
    needs the resolved-secret→`TF_VAR_<KEY>` mapping built and passed.
- **`value_controller.resolve_values()`** — already resolves *every*
  store type, not just build-time-safe ones: `constant`/`environment`
  read directly, everything else (`vault`/`infisical`/`consul`/`etcd`/
  `azure-appconfig`) dispatched to a real `StoreIntegration.resolve()`.
  This is the exact mechanism `deploy run` needs and it needs zero new
  resolution logic — `build_value_references()`'s constant/environment-
  only restriction was a deliberate *build-time* narrowing on top of
  this same, already-full-featured function.
- **`DeploymentStageModel`** (`deployment_model.py`) — already models
  per-step runtime parameters: `step` (names a
  `WorkspaceSpecModel.execution[].name`), `enabled` (bool or unevaluated
  string expression), `on_failure` (`stop`/`rollback`/`continue`),
  `timeouts`, `health_checks`, `scripts`, `secrets`, `namespace`,
  `helm_namespaces`. No `scope` here by design — it lives on
  `ProvisioningStepModel` instead (workspace-owned, not deployment-owned;
  see the flow below and `docs/_gap_v1.md`'s gap #6 for why).
- **`ordered_by_depends_on()`** (`build_controller.py`) — topological
  sort over `workspace.spec.execution`, already reused as-is by
  `build_run()`; `deploy run`'s orchestrator needs the identical
  ordering, no new sort logic.
- **ADR-0022 D4** already commits to this exact split in writing:
  *"This design calls only `prepare()` — never `plan`/`deploy`/
  `destroy`."* — `deploy run` was always understood as the eventual
  caller of those three methods, just not designed yet.
- **`TerraformIntegration.init()`** — also already implemented (real
  `terraform init -backend-config=... [-upgrade] [-reconfigure]`), and
  its own docstring already names the exact design this doc settles:
  *"the future deployer... calls this as its own step, same as v1's
  setup step; never auto-chained inside `plan`/`deploy`/`destroy`."*
  Confirms the real, v1-precedented per-step sequence: **setup (init) →
  check (validate) → plan → apply**, matching v1's own
  `STEP_SETUP`/`STEP_CHECK`/`STEP_PLAN`/`STEP_APPLY` constants imported
  by `run_deploy_command.py`.

## Orchestrator shape — concrete design

```
strata deploy run DEPLOYMENT [--path PATH] [--build-path PATH] --force
  [--dry-run] [--stage NAME] [--scope LABEL] [--verbose]
  └─ deploy_command.py: deploy_run_command()
       ├─ context = open_solution(path).require_valid()
       ├─ build_path = build_path or layout.build_dir(context.root, deployment)  # same default as build run
       └─ diagnostics = deploy_run(context, deployment, build_path, force=force, dry_run=dry_run,
                                     stage=stage, scope=scope, on_step=run.step)

deploy_run(context, deployment_name, build_path, *, force, dry_run=False, stage=None, scope=None, on_step=None)
  ├─ deployment = resolve_deployment(context, deployment_name)   # shared with build_run(), value_controller.py
  ├─ workspace = index.get(WORKSPACE, deployment.spec.workspace)
  ├─ environments = reachable_environments(context, deployment)
  ├─ all_keys = every variable/secret/feature key merge_environment_models(environments) declares
  ├─ resolved = resolve_values(context, deployment_name, all_keys)   # FULL resolution — secrets included (ADR-0021 Phase 4, already built)
  ├─ steps = ordered_by_depends_on(workspace.spec.execution)         # same sort build_run() already uses
  ├─ steps = [s for s in steps if stage is None or s.name == stage]
  ├─ steps = [s for s in steps if scope is None or s.scope == scope] # ProvisioningStepModel.scope (new, workspace-owned)
  │
  ├─ # --- preflight pass (extension point 1) --------------------------------
  │    for step in steps:
  │        integration = resolve_integration(index, find_provisioner(workspace, step.provisioner))
  │        integration.is_available() and integration.ensure_version(provisioner.version)  # tool binary + version — fail fast, before any step runs
  │    (a step whose DeploymentStageModel.enabled resolves False is excluded before this point, never checked)
  │
  ├─ for step in steps:                                              # extension point 2: per-step before/after hooks go here later
  │    stage_overrides = deployment.spec.stages find-by(step=step.name)  # optional; DeploymentStageModel
  │    provisioner = find_provisioner(workspace, step.provisioner)
  │    integration = resolve_integration(index, provisioner)
  │    path = build_path / step.name                                # same path build_run() already wrote to
  │    env = tf_var_env(resolved) | output_context.for_step(step)    # generic values + this step's visible upstream outputs — TF_VAR_ prefixing happens only here
  │
  │    tokens = {**resolved.values, **output_context.for_step(step)}
  │    backend_config = resolve_value_tokens_in_mapping(provisioner.backend.configuration, tokens)  # Mechanism B, deploy-time only
  │    if not dry_run:
  │        integration.init(path, backend_config=backend_config, env=env)
  │        integration.validate(path, env=env)                       # v1's "check" step
  │        result = integration.plan(path, out_file=f"{step.name}.tfplan", env=env)
  │        result = integration.deploy(path, plan_file=f"{step.name}.tfplan", env=env)  # applying a saved plan file needs no -auto-approve (v1's real mechanism — Remaining Work item 7)
  │        if result.is_successful:
  │            output_context.collect(step.name, integration, path, env=env)  # e.g. `terraform output -json`; dependency-scoped for later steps ("Cross-step output context" below)
  │    on_step(f"... deployed {step.name} ...")
  │    if result failed:
  │        if stage_overrides.on_failure == "continue": warn, keep going
  │        else: return diagnostics                                  # extension point 3: cleanup/finally goes here later
  │
  └─ return diagnostics                                              # extension point 3 (unconditional): locking/audit cleanup later
```

Deliberately **not** shown as a separate step: `output.template`'s
actual Jinja2 render. Every value needed for it is now available
(`resolved.values` is fully resolved, unlike `build run`'s partial set),
so it slots in as one more per-step branch (mirroring
`InfraIntegration.prepare()`'s existing `if provisioner.output.template`
branch, just actually rendering instead of only validating) — not
designed in detail here since it's a template-rendering mechanism, not
an orchestration one.

## Example — `scope` on the workspace, operational overrides on the deployment

Modelled on haven's real shape, corrected per the resolved design above
(`scope` moved to `ProvisioningStepModel`; `DeploymentStageModel` keeps
only per-environment operational behaviour):

```yaml
# workspace.yaml — the recipe: what exists, how it's structured, and its
# own fixed classification. Identical across every deployment of this
# workspace.
spec:
  execution:
    - name: infrastructure_hearth
      provisioner: haven_iac
      targets: [haven_vm_hetzner_hearth]
      scope: infra                    # <- lives here now, not on the deployment

    - name: infrastructure_forge
      provisioner: haven_iac
      targets: [haven_vm_hetzner_forge]
      depends_on: [infrastructure_hearth]
      scope: infra

    - name: applications_forge
      provisioner: haven_helm
      targets: [system, immich, media, documents]
      depends_on: [infrastructure_forge]
      scope: apps
```

```yaml
# deployment.yaml — the instantiation: which environment-specific
# operational behaviour applies to an *already-defined* workspace step.
# Never redeclares scope/provisioner/topology — those are workspace facts.
spec:
  workspace: haven_platform
  environments: [prd]
  stages:
    - step: infrastructure_hearth
      secrets: [HETZNER_API_TOKEN]

    - step: infrastructure_forge
      secrets: [HETZNER_API_TOKEN, HETZNER_PUBLIC_KEY]

    - step: applications_forge
      secrets: [IMMICH_DB_PASSWORD, NEXTCLOUD_ADMIN_PASSWORD]
      on_failure: continue            # <- a real per-environment operational choice:
                                       #    a dev deployment of the same workspace
                                       #    could set 'stop' here instead, without
                                       #    touching the workspace at all.
```

```bash
# CLI — --scope filters workspace.spec.execution[].scope directly,
# independent of which deployment/environment is targeted:
strata deploy run --file deployment.yaml --force --scope infra
#   -> runs infrastructure_hearth, infrastructure_forge only
#      (applications_forge is scope: apps, excluded)

strata deploy run --file deployment.yaml --force --stage applications_forge
#   -> runs exactly one step, using that deployment's own stage overrides
#      (on_failure: continue, secrets: [...])
```

The same `workspace.yaml` deployed as `dev` instead of `prd` reuses the
identical `scope: infra`/`scope: apps` classification unchanged — only
`deployment.yaml`'s own `stages:` entries (secrets available, failure
behaviour, timeouts) differ per environment, never the workspace's own
structural facts.

## Structural gotchas from v1 — process/safety patterns, not feature bloat

Found by reading v1's real `run_deploy_command.py`'s actual execution
flow directly (not just its CLI surface). Distinct from the Tier 2
feature list already excluded elsewhere (gates/work-items, promotion/
wave, AI review, change-management, cost-history) — these are lower-level
*process correctness* patterns worth deciding on deliberately, one way or
the other, rather than discovering as a gap later. None of them need
building now; the point is the orchestrator's shape shouldn't foreclose
them.

1. **`on_failure: "rollback"` does nothing different from `"stop"` in
   v1's actual code** — confirmed directly: the word appears only in
   comments, never in a branch condition. The real stage loop only
   special-cases `on_failure == "continue"`; everything else (`"stop"`,
   `"rollback"`, or any future value) falls through to the identical
   fatal-abort path. If v2 ports this enum value at all, don't invent
   distinct rollback behaviour for it without new evidence — today it's
   a synonym for `stop`, in v1 as much as anywhere.
2. **Preflight-validate every stage's provisioner (tool binary + auth)
   *before* acquiring any lock or running any stage** — fails fast so a
   later stage's missing tool/credential is caught before an earlier
   stage has already made real infrastructure changes. Cheap, valuable,
   worth adopting even in a minimal v2 — this is a pure validation pass,
   no new tooling required.
3. **Filtering happens *before* preflight-checking** — `--stage`/
   `--scope`/`enabled` filtering runs first; a disabled/filtered-out
   stage's tooling is never checked. Get this ordering backwards and an
   unrelated stage's auth/tool requirement blocks a run that doesn't
   even touch it.
4. **`on_failure: continue` stages are still preflight-checked (for
   visibility) but a failure there is downgraded to a warning, not a
   hard failure** — mirrors how they're already tolerated once real
   execution reaches them. A subtlety worth matching if v2 does
   preflight checks at all.
5. **Lock release / cleanup always happens in a `finally`**, regardless
   of success, stage failure, or an unhandled exception — no code path
   in v1 can leave a lock stuck without at least attempting release.
   Whatever cleanup v2 eventually needs (lock release, audit-trail
   finalisation, whatever comes later) should sit in the same
   unconditional position in the loop, not on the happy path only.
6. **No deployment-level locking exists in v2's design yet — a
   deliberate future item, explicitly out of scope for this phase, but
   the orchestrator's loop shape should not make adding it later a
   rewrite.** Nuance worth carrying into that future design: Terraform's
   own remote backends (`azurerm`, S3+DynamoDB, Terraform Cloud) already
   provide real state-locking independent of anything strata does, so an
   app-level lock is partially redundant for the Terraform case
   specifically — Helm/Compose have much weaker or no native equivalent.
   Not a decision to make now; a fact to hand to whoever designs locking
   later so they don't reinvent something Terraform's backend already
   guarantees.
7. **SIEM/audit-trail logging** — same treatment as locking: v1 has
   real, working change-management/cost-history/audit machinery this
   phase deliberately excludes (Tier 2, no proven `deploy run` critical-
   path dependency in either real repo). Not built now; the per-stage
   before/after hook shape (item 2 above) is exactly where it would plug
   in later without disturbing the core plan/apply loop.
8. **A "whole deploy run" timeout + hard-kill exists in v1** (a worker
   thread plus a shutdown coordinator that kills subprocesses and
   releases the lock on timeout) — v2 already has *per-command* timeouts
   on `plan()`/`.deploy()`'s own `timeout` kwarg (partial mitigation:
   confirmed real, `deploy()`'s own default is 1800s), but nothing at the
   "whole run" level yet. Worth noting as an accepted, deliberate gap
   rather than assuming per-command timeouts already cover it fully.

## Cross-step output context — deploy-time value chaining

A step's `apply` (e.g. Terraform creating a VM) can produce values a
*later*, dependent step needs (e.g. a Helm step needing the VM's IP to
template a kubeconfig context) — v1 has a real, working mechanism for
this; v2 has none yet. Design settled 2026-09-27, found by reading v1's
real mechanism directly and comparing it against how other
orchestration platforms solve the same problem.

**v1's real mechanism** (`strata/utils/resolved_values.py`,
`run_deploy_command.py`): a single mutable `ResolvedValues` object is
threaded through the whole run. After every stage that includes
`STEP_APPLY`, `deployer.collect_outputs()` splits the provisioner's
outputs into `stage_outputs` (non-sensitive) and `stage_outputs_sensitive`
(Terraform's own `sensitive = true` outputs) and merges them into the
shared object. Every *subsequent* stage's `as_tf_vars()`/`as_compose_env()`
folds `stage_outputs` into the same flat `TF_VAR_<key>`/bare-env-var
namespace as declared variables/secrets/features — **globally**: every
later stage sees every earlier stage's outputs, regardless of
`depends_on`, with no namespacing (a later stage's output can silently
shadow an earlier value or another stage's output on key collision).
`stage_outputs_sensitive` is tracked for the deployment manifest but
never injected into subprocess env.

**Compared against how other platforms do this** (GitHub Actions'
`steps.<id>.outputs`, GitLab CI's `dotenv` artifacts + `needs:`, Azure
Pipelines' `$(Stage.Job.Var)`, Terraform's own `terraform_remote_state`/
Pulumi `StackReference`) — every one of them, unlike v1, scopes output
visibility to an **explicit dependency declaration** and keeps outputs
**namespaced** (referenced by step/job id, never silently flattened into
one ambient pool). v1's global-flat-pool behaviour is the outlier here —
livable at haven's small scale, but a real footgun (silent collision, no
provenance) at any size. Also checked what "sensitive" actually buys:
Terraform's own `sensitive = true` only suppresses console/plan-output
*display* — `terraform output -json` still returns the real value, and
state itself is plaintext — so none of the platforms above (nor
Terraform itself) treat "sensitive" as an env-injection access boundary;
it's a display-redaction hint only.

**v2 design** (synthesizing both):
- **New 4th value-token kind, `${output:<step_name>.<key>}`**, alongside
  `${var:}`/`${secret:}`/`${feature:}` — widens `VALUE_TOKEN_PATTERN` and
  reuses `resolve_value_tokens()`/`resolve_value_tokens_in_mapping()`
  unchanged (Implementation Plan phase 2's utility already built), just
  giving token resolution a second values source to consult. A step's
  `backend.configuration` references a prior step's output **explicitly
  by name** — never ambient, never ambiguous about where a value came
  from. **Scope, confirmed by the actual implementation (2026-09-27
  review)**: only `provisioner.backend.configuration` is wired to this —
  there is no mechanism for a downstream step's plain Terraform variable
  (a `variable "x" {}` in its own `main.tf`, fed via `TF_VAR_x`) to
  receive a cross-step output; `${output:}` only resolves inside
  schema-owned string fields, matching Mechanism B's own scope
  (value-token-resolution.md) exactly. Extending `env=`/`TF_VAR_*` to also
  carry outputs was considered and deliberately rejected (see below), not
  merely not-yet-built.
- **Dependency-scoped, not global**: a step may only reference
  `${output:X.key}` for a step `X` it (transitively) `depends_on` —
  matches every real platform surveyed above, and v2 already has the
  real dependency graph (`ordered_by_depends_on()`) to enforce it
  against, something v1 never had to begin with.
- **Generic internal namespace, no baked-in tool prefix.** Collected
  outputs live in the same plain `dict[str, str]` shape `resolved.values`
  already uses — `tf_var_env()` (Implementation Plan phase 4, already
  built) remains the *only* place a `TF_VAR_` prefix gets added, and only
  when handing values to a Terraform-family integration's `env=` kwarg.
  A future Compose/Helm-specific translation (if one is ever needed) gets
  its own small named function the same way, rather than baking a prefix
  choice into the collection/storage layer itself.
- **Sensitive outputs are not tracked as a separate structure.** Given
  Terraform itself doesn't treat `sensitive = true` as an access
  boundary (see above), v2 folds every collected output into the same
  resolution path — the only concession is that `on_step()` progress
  messages and `--dry-run` echoes never print a collected output's raw
  value (matches the same "don't echo secrets" discipline `resolved.
  values` already gets elsewhere), avoiding a parallel sensitive/
  non-sensitive data structure for a distinction the underlying tool
  doesn't enforce either.
- **Collection mechanism**: after a step's `deploy()` succeeds, if the
  integration exposes an `output()` method (Terraform-specific, like
  `init`/`validate` — confirmed not on `Helm`/`Compose`), call
  `output(path, json_format=True)`, parse Terraform's own `{key: {value,
  sensitive, type}}` JSON shape, and merge the `value`s into the per-run
  output context keyed by `<step_name>.<key>`. Compose/Helm integrations
  simply contribute nothing (no native "outputs" concept in either real
  repo) — same tool-agnostic `getattr(integration, "output", None)`
  pattern the loop already uses for `init`/`validate`.

## Remaining Work / Open Questions

1. **`scope: str | None` — resolved (2026-09-27): belongs on
   `ProvisioningStepModel` (the workspace side), not `DeploymentStageModel`
   (the deployment side).** Reasoning, thinking through it as a
   workspace/deployment separation-of-concerns question:
   - The core principle already established elsewhere in this schema
     (workspace = the reusable recipe / single source of truth for
     *what* gets built and its structure; deployment = a thin
     instantiation supplying environment-specific values and
     *operational* overrides, never structure) — and it's already been
     applied once: v2 replaced v1's `DeploymentStageModel.provisioner`/
     `.topology` (the deployment re-declaring *which* workspace step to
     bind) with a name-only `step: PlatformName` reference. A deployment
     selects an existing workspace step; it doesn't define a new one.
   - Whether a step is "infra" or "apps" is a fact about *what the step
     does*, not about *which environment runs it* — a `prd` and a `dev`
     deployment of the same workspace would classify
     `infrastructure_hearth` identically. It has no business varying
     per-deployment, so it has no business living on the per-deployment
     override model.
   - Direct precedent already in the codebase for this exact judgment:
     `ProvisioningStepModel`'s own docstring states *"this step realizes
     topology X" is a derived fact, not declared* (ADR-0011) — the
     workspace step deliberately does not duplicate topology binding,
     precisely to avoid workspace/deployment drift. `scope` is the same
     category of fact.
   - Putting `scope` on `DeploymentStageModel` would mean every
     deployment of a workspace has to independently, correctly
     re-declare the same infra/apps split — the exact config-drift risk
     the workspace/deployment split exists to prevent. One workspace,
     one classification, defined once.

   **Conclusion**: add `scope: str | None` to `ProvisioningStepModel`
   (free-form label, matching v1's real documented semantics — not a
   fixed enum); `--scope infra` filters `workspace.spec.execution`
   directly, independent of which deployment/environment is running.
   `DeploymentStageModel` keeps only genuine per-environment *operational*
   overrides (`enabled`, `on_failure`, `timeouts`, `health_checks`) —
   things that legitimately vary by environment (stricter `on_failure` in
   prod than dev, say). This also resolves the `topology`-binding
   question below in the same direction: v1's direct `topology:`/
   `provisioner:` stage binding was the anti-pattern to avoid, not a gap
   to fill — v2's `step`-only reference is the correct, already-made call.
   **Disambiguation, still relevant**: v1 also has an unrelated,
   identically-named `DeploymentEnvironmentRef.scope` (`"shared"`/a layer
   name, used by the *promotion* system to target which environment file
   a wave edits) — a completely different concept that happens to share
   the field name; do not conflate the two when implementing.
2. **The orchestrator itself** (`deploy_controller.py`'s `deploy_run()`)
   — walks `workspace.spec.execution` via the existing
   `ordered_by_depends_on()`, filtered by `--stage`/`--scope`, resolves
   full values via `resolve_values()` (not `build_value_references()`),
   and calls `integration.plan()`/`.deploy()` against the *already-
   rendered* `build run` output directory (never re-renders it). **The
   loop's shape should leave room for locking/audit-logging/gates to be
   inserted later** (see "Structural gotchas from v1" below) without a
   redesign — concretely, structure it with named extension points
   (a preflight-validation pass over the filtered stage list, before
   any stage runs; a per-stage before/after hook; a `finally`-guaranteed
   cleanup step) even though *what* runs at each point is explicitly
   out of scope for this phase. Don't build locking/SIEM now — just
   don't paint the loop into a shape that can't hold them later.
3. **`TF_VAR_<KEY>` secret injection** — confirmed real (haven's own
   comment) and confirmed universally relied upon (cfg-int-deployment's
   real workflow has no manual alternative). Needs a real, named
   function (mirrors ADR-0022 D1a's own call for a named
   `build_time_keys()`-style function, never inline) that maps a
   resolved secret key to a `TF_VAR_<KEY>` env var passed to
   `terraform plan`/`.deploy()`'s `env=` kwarg — never written to a
   file on disk. Compose/Helm's equivalent (if any) is unconfirmed by
   real evidence yet — neither real repo's Helm/Compose stage shows a
   comparable pattern to check against.
4. **Deploy-time token substitution** (`provisioner.backend`/
   `.configuration`, `dns`/`networks` `${var:}`/`${secret:}`/
   `${feature:}` tokens) — deferred by every `build_run`-era design doc
   specifically because `deploy run` didn't exist; this is its natural
   home now.
5. **`output.template`'s actual Jinja2 render** — `build run` only
   validates references today (`InfraIntegration.prepare()`'s own
   docstring: *"a 'final' render would be dishonest here... the actual
   render is deploy-time work, not yet built"*). `deploy run` has every
   value resolved, so the render can happen for real here.
6. **CLI command** (`src/strata/commands/deploy_command.py`) — thin
   glue matching `build_command.py`'s shape, exposing `--file`/`--force`/
   `--dry-run`/`--stage`/`--scope`.
7. **`--force`'s exact semantics — resolved (2026-09-27), found by
   reading v1's real installed source directly** (`uv tool`-installed
   `xyz-strata` v1.11.2: `strata/commands/deploy/run_deploy_command.py`,
   `strata/deployers/terraform_deployer.py`):
   - **`--force` is *not* terraform's `-auto-approve` for a normal
     `deploy run` apply.** `TerraformDeployer.apply()` always applies a
     *saved plan file* (`terraform apply <stage>.tfplan`) — Terraform
     never prompts to apply a saved plan regardless of any flag, so
     there is nothing for `--force` to auto-approve on the critical
     path. `self.force` is never even read inside `apply()`.
   - **`-auto-approve` only appears for `destroy`** —
     `TerraformDeployer.destroy()` passes `auto_approve=self.force`,
     because `terraform destroy` (no saved plan file involved) does
     prompt interactively unless told not to. `deploy destroy`'s CLI
     wiring is also the *only* one of the two commands that enforces
     "exactly one of `--force`/`--dry-run` required" — `deploy run` has
     no such requirement; both default `False` and a real, non-dry-run
     apply still runs.
   - **For `deploy run`, `--force` instead overrides advisory approval
     gates**: skips the interactive prompt when `--ai`'s plan review
     flags high risk on a TTY (blocks instead, without `--force`, in
     non-interactive/CI mode); overrides the "deployment is
     promotion-managed" guard that otherwise refuses a manual
     `--version-file`/`--ring` override. It explicitly does **not**
     override `--strict-ai-review` (called out directly in v1's own
     help text) — `--force` overrides *advisory* gates only, never an
     explicit deny-enforcement one.
   - **v2 conclusion**: `deploy_run()` applies via the same
     saved-plan-file mechanism (`plan(out_file=...)` →
     `deploy(plan_file=...)`), so the normal path needs no
     `auto_approve` at all — phase 3's original `auto_approve=force`
     wiring didn't match v1's real semantics and is corrected by
     Implementation Plan phase 5 below. `force` itself is a currently-
     inert parameter in v2 (kept in the signature/CLI so a future
     advisory gate — AI review, promotion guard, both explicitly
     out-of-scope Tier 2 per `/memories/repo/v1-consumer-usage.md` — can
     consume it later without changing the call shape).
8. **`ArtifactSpecModel`/`store: artifact` at deploy time — resolved
   (2026-09-27, same session as the review that sharpened its severity).**
   `resolve_values()` (the full, deploy-time resolver `deploy_run()` uses)
   used to dispatch purely on `store.store`: `constant`/`environment` read
   directly, everything else fell to `resolvers.get(store_type.value)` —
   an actual `StoreIntegration` lookup by that name, which `artifact`
   isn't and never will be registered as (it never fetches anything
   remotely; a real image reference resolves against the solution's own
   `ArtifactModel` + `kind: version` pins, an entirely different
   mechanism, `value_controller.resolve_artifact_field()`). Because
   `deploy_run()` calls `resolve_values()` with **every** declared
   variable/secret/feature key from every reachable environment and
   aborts the whole run on any single resolution failure, this was a
   whole-run-blocking bug, not a single-value gap. **Fix**: `resolve_
   values()`'s loop now special-cases `store.store ==
   VariableStoreType.ARTIFACT` before ever reaching `_resolve_store_
   value()`'s integration dispatch, calling `resolve_artifact_field()`
   directly — the same mechanism `build_value_references()`'s
   `_variable_value()` already used at build time, now shared by both.
   2 new tests (`tests/strata/controllers/test_value_controller.py`):
   a real `store: artifact` variable now resolves through `resolve_
   values()` end to end; an unresolvable artifact name is reported as a
   normal value-resolution error, not "no resolver implemented yet".
   Full check suite green (1130 tests, mypy 107 files, 0 broken
   import-linter contracts).
9. **`store: artifact`'s `value` has zero cross-reference validation —
   resolved (2026-09-27, same day as the review that found it).**
   `EnvironmentService.validate_artifact_references(artifact_names)`
   (new) checks every `store: artifact` variable's `value` against the
   solution's real `ArtifactModel` names, mirroring `WorkspaceService.
   validate_topology_references()`'s exact precedent for a
   conditionally-meaningful field. Wired into `semantic_checks.py` as an
   8th check, `_check_environments()`, called from `run_semantic_checks()`
   alongside the existing seven. 6 new tests (4 service-level unit tests,
   2 end-to-end `strata validate`-path tests in `test_semantic_checks.py`
   confirming a real, unresolvable artifact reference is now caught with
   the bad name in the message). Full check suite green (1128 tests,
   mypy 107 files, 0 broken import-linter contracts).

## Implementation Plan

Phased the same way `build-time-value-categories.md`/`artifact-references.md`
were — small, independently-testable, full check suite after each.

1. ✅ **`ProvisioningStepModel.scope: str | None`** (workspace side, per
   the resolved design above) — done 2026-09-27. One new optional field,
   no validator (free-form label, nothing to cross-check, matching v1's
   real documented semantics exactly). 3 new tests: round-trips/defaults
   to `None`, accepts any free-form string (not just `infra`/`apps`),
   uniqueness NOT enforced (`validate_provisioning_steps()` doesn't
   reject two steps sharing a scope label — that's the whole point of
   the filter). Full check suite green (1073 tests, mypy 105 files, 0
   broken import-linter contracts).
2. ✅ **Deploy-time value-token substitution utility** — done 2026-09-27.
   `strata/utils/value_tokens.py` gained `resolve_value_tokens(value: str,
   values: dict[str, str]) -> str` (walks `${var:KEY}`/`${secret:KEY}`/
   `${feature:KEY}` tokens via the existing `VALUE_TOKEN_PATTERN`,
   indifferent to `kind` — resolution reads `key` only, since
   `resolve_values()` already merged every store kind into one flat
   mapping; raises on an unresolved key, mirroring `output.template`'s
   own "unknown reference is an error" precedent; a plain literal with
   no tokens passes through unchanged) and
   `resolve_value_tokens_in_mapping(data: dict[str, Any], values:
   dict[str, str]) -> dict[str, Any]` (recurses into nested dicts/lists,
   for `provisioner.backend.configuration`'s real shape). This is
   Mechanism B's long-deferred deploy-time resolver
   ([value-token-resolution.md](value-token-resolution.md)) — used for
   `provisioner.backend.configuration` first (needed just to call
   `init()`), `dns`/`networks` next if real evidence still shows the
   need once phase 3 starts. 11 new tests
   (`tests/strata/utils/test_utils_value_tokens.py`, a new file — no
   dedicated test file existed for `value_tokens.py` before). Full check
   suite green (1084 tests, mypy 105 files, 0 broken import-linter
   contracts).
3. ✅ **`deploy_controller.py`'s `deploy_run()` core loop** — done
   2026-09-27. `src/strata/controllers/deploy_controller.py` (new):
   resolves deployment/workspace/environments, full `resolve_values()`,
   filters by `--stage`/`--scope` (`ProvisioningStepModel.scope`),
   preflight pass (`integration.is_available()` per filtered step —
   `ensure_version()` deferred, no real `provisioner.version` fixture
   evidence yet to test against), then per-step `init → validate → plan
   → deploy` against `build run`'s already-rendered output directory.
   `init`/`validate` are Terraform-specific extras (confirmed not on the
   `InfraIntegration` ABC) — called via `getattr(integration, name,
   None)` so the loop stays tool-agnostic, matching ADR-0022's own
   "zero tool-specific branching" principle; Compose/Helm integrations
   simply don't have them and are skipped automatically. `force` maps
   directly to `deploy()`'s existing `auto_approve` kwarg. TF_VAR
   injection left as an empty `env: dict[str, str] = {}` placeholder for
   phase 4. 10 new tests (`tests/strata/controllers/test_deploy_controller.py`,
   new file) — full lifecycle sequencing, `--stage`/`--scope` filtering,
   dependency ordering, `force`→`auto_approve`, preflight failure,
   plan-failure abort, and backend-token resolution, all against
   `run_command`/`shutil.which` stubs (no real `terraform` binary
   needed, same pattern `test_integrations_terraform.py` already uses).
   Full check suite green (1094 tests, mypy 106 files, 0 broken
   import-linter contracts).
4. ✅ **`TF_VAR_<KEY>` secret injection** — done 2026-09-27.
   `tf_var_env(resolved: ValueResolution) -> dict[str, str]`, a small
   named function in `deploy_controller.py` (mirrors ADR-0022 D1a's own
   call for a named `build_time_keys()`-style function rather than an
   easy-to-get-wrong inline filter): `{f"TF_VAR_{k}": v for k, v in
   resolved.values.items()}`. Wired into phase 3's `init`/`validate`/
   `plan`/`deploy` calls via their existing `env=` kwarg, replacing the
   empty-dict placeholder — applied uniformly per step regardless of
   tool (Compose/Helm integrations simply never read the extra
   `TF_VAR_*` vars; `transport.run_command()`'s merge-not-replace `env`
   semantics, confirmed in phase 3, meant nothing new was needed on the
   transport side). 3 new tests (unit coverage for `tf_var_env()` itself,
   plus an end-to-end `deploy_run()` test asserting every one of a real
   step's `init`/`validate`/`plan`/`deploy` calls receives the resolved
   secret as `TF_VAR_db_password`). Full check suite green (1097 tests,
   mypy 106 files, 0 broken import-linter contracts).
5. ✅ **Correct the apply mechanism to match v1's real `--force`
   semantics** (Remaining Work item 7) — done 2026-09-27. Replaced
   phase 3's `deploy(path, auto_approve=force, ...)` with the
   saved-plan-file mechanism: `plan(path, out_file=f"{step.name}.tfplan",
   ...)` then `deploy(path, plan_file=f"{step.name}.tfplan", ...)` —
   applying a saved plan needs no `-auto-approve`, matching v1's real
   `TerraformDeployer.apply()` exactly. `force` is now an inert
   parameter (kept in the signature/docstring, explicitly documented as
   such — no advisory gate exists yet to consume it). Compose/Helm are
   unaffected (`out_file`/`plan_file` are Terraform-specific kwargs,
   already accepted and ignored via their `**kwargs` catch-all). Test
   changes: replaced the two now-obsolete `force`→`auto_approve` tests
   with `test_deploy_run_plans_to_a_saved_file_and_applies_it` (asserts
   `plan`'s argv has `-out <step>.tfplan` and `apply`'s argv references
   that same file) and `test_deploy_run_apply_never_uses_auto_approve`
   (asserts `-auto-approve` never appears, `force=True` or not). Full
   check suite green (1097 tests — net even, 2 removed/2 added — mypy
   106 files, 0 broken import-linter contracts).
6. ✅ **Cross-step output context** (new section above) — done
   2026-09-27. `VALUE_TOKEN_PATTERN`/`VALUE_TOKEN_KINDS` widened to a 4th
   `output` kind (`${output:<step_name>.<key>}`) — `resolve_value_tokens()`
   stays kind-agnostic (reads `key` only, unchanged), the `key` regex
   already permitted `.` so `"step.key"` needed no pattern change beyond
   the kind alternation. `deploy_controller.py` gained two small named
   functions: `collect_step_outputs(integration, path, env)` (calls
   `getattr(integration, "output", None)`, parses Terraform's `{key:
   {value, ...}}` JSON; returns `{}` on a missing/incompatible `output()`
   — caught a real wrinkle here: `ComposeIntegration.output()` **does**
   exist, but it's `docker stack services` with an incompatible
   `namespace`-required signature, not a JSON key/value map, so the
   `getattr` check alone wasn't enough — wrapped the call in
   `try/except TypeError` too) and `_upstream_step_names(step_name,
   steps)` (transitive `depends_on` reachability, dependency-scoping the
   references per the resolved design). The main loop precomputes
   `upstream_by_step` once, accumulates `step_outputs` per step, and
   merges each step's *visible* upstream outputs into the token dict
   passed to `provisioner.backend.configuration` resolution alongside
   `resolved.values` — refined one piece of the original pseudocode
   during implementation: outputs are **not** also folded into `env=`
   (`TF_VAR_<key>`) — a `"step.key"`-shaped name isn't a valid Terraform
   identifier, and ambient env injection was exactly the v1 flaw this
   design was chosen to avoid; `${output:}` tokens in explicit
   schema-defined string fields (Mechanism B) are the only consumption
   path. 8 new tests: end-to-end token resolution across a real
   dependency edge, dependency-scoping rejection (a non-dependency
   reference raises, matching `resolve_value_tokens_in_mapping()`'s
   existing "unresolved key is an error" precedent), `collect_step_outputs()`
   unit coverage (parses real Terraform JSON, empty for an integration
   without a compatible `output()`, empty on a failed call), and
   `_upstream_step_names()` transitivity. 2 pre-existing tests updated
   for the new `output` call every successful step now makes. Full check
   suite green (1103 tests, mypy 106 files, 0 broken import-linter
   contracts).
7. ✅ **CLI command** (`src/strata/commands/deploy_command.py`) — done
   2026-09-27. Thin glue over `deploy_controller.deploy_run()`, matching
   `build_command.py`'s shape exactly: `command_run()` for lifecycle,
   `open_solution(path).require_valid()` for the precondition,
   `deployment` as a positional argument (no `-f FILE`, same reasoning
   as `build`/`validate`/`values`), `--build-path` defaulting to the
   identical `build_dir(root, deployment)` `build run` itself uses,
   `--force`/`--dry-run`/`--stage`/`--scope`/`--output`/`--quiet`/
   `--verbose`. `--force`'s help text documents it as currently inert
   (phase 5's finding) rather than silently accepting a flag that does
   nothing unexplained. Registered in `cli.py` alongside
   `validate`/`values`/`build`. 10 new tests
   (`tests/strata/commands/test_commands_deploy.py`, a new file, mirrors
   `test_commands_build.py`'s structure): exit codes (success, unknown
   deployment, outside a solution, invalid solution, a step's plan
   failure), `--build-path` override, `--dry-run` makes zero
   `run_command` calls, `--stage` filtering (including rejecting an
   unknown stage name — zero steps run, still exit 0), `--force`
   accepted without ever adding `-auto-approve` to the real apply argv.
   Full check suite green (1113 tests, mypy 107 files, 0 broken
   import-linter contracts).
8. ✅ **`output.template`'s actual render** — done 2026-09-27.
   `strata/utils/templater.py` gained `render_template(source, context) ->
   str` (the deploy-time counterpart to `validate_template_references()`;
   the shared `_ENV` was switched to `StrictUndefined` so a reference
   that passed static validation — anything nested under a `None`-valued
   `known_names` root like `graph.*`, or a dynamic key — still raises
   loudly at render time instead of silently rendering empty, matching
   `resolve_value_tokens()`'s own "unknown reference is an error"
   precedent; has no effect on `validate_template_references()`'s own
   parse-only call). `InfraIntegration.render_output_template()` (new,
   base-implemented on the ABC like `prepare()`, ADR-0023 D5's "nothing
   about this field is tool-specific" reasoning) builds the identical
   `graph`/`variables`/`flags`/`secrets`/`properties`/`custom`/
   `provisioner` context shape `known_names` already checks against,
   valuing `variables`/`flags`/`secrets` from `resolved.values` (never
   from `graph.variable_refs[].value`, which stays build-time-safe-only)
   — writes the rendered file with a trailing `.j2`/`.jinja2`/`.jinja`
   stripped from the template's own basename. Wired into
   `deploy_controller.py`'s loop, before `init`, converting a render
   failure into a `Diagnostics.error()` + early return like every other
   per-step failure.

   **Found and fixed a real, pre-existing bug while wiring this in**:
   `deploy_run()`'s step directory was `build_path / step.name`, but
   `build_run()` actually materialises a sourced provisioner at
   `build_path / (source.target_path or source.source_path)`
   (`source_sync.sync_source()`'s own destination — keyed by the
   provisioner's *source location*, not by whichever step references
   it, since two steps can share one provisioner/source). Every prior
   phase's tests passed anyway because they fully stub `run_command`
   and never touch the real filesystem at that path — this phase's
   render is the first deploy-time code to actually write a real file,
   which is what surfaced it. Corrected `deploy_controller.py` to
   compute the identical path `build_run()` uses (source-less/sync-
   GitOps provisioners still key off `step.name`, matching build's own
   other branch) — a load-bearing fix, not scope creep: without it,
   `init`/`plan`/`deploy` would already have been running against a
   directory `build run` never actually wrote to.

   14 new tests: `render_template()` unit coverage (substitution,
   undefined-reference/syntax-error raises) in
   `tests/strata/utils/test_utils_templater.py`;
   `render_output_template()` coverage (writes/strips `.j2`, uses
   `resolved.values` not the ref's own value, raises on an unresolvable
   nested reference, leaves a non-`.j2` filename unchanged) in
   `tests/strata/integrations/test_integrations_capabilities.py`; two
   end-to-end `deploy_run()` tests (renders before `init` with real
   resolved values; a render failure is reported and never reaches
   `init`) in `test_deploy_controller.py`, plus fixture fixes for the
   path-resolution correction. Full check suite green (1122 tests, mypy
   107 files, 0 broken import-linter contracts).
9. ✅ **Full check suite + docs** — done 2026-09-27. This doc's status
   updated to `partially-implemented` (deferred items listed there, not
   repeated here); `v2-schema-overview.md`'s `strata deploy run` row
   corrected and its ADR count bumped; [ADR-0027](../decisions/0027-strata-deploy-run.md)
   (new) drafted from the settled design — same discipline as ADR-0026,
   one point-in-time decision record summarizing the orchestrator shape,
   the `--force`/saved-plan-file correction, and the cross-step output
   context, with this doc doing the living-history/evidence job as
   always. Full check suite reconfirmed green (1122 tests, mypy 107
   files, 0 broken import-linter contracts) — no code changed this phase.

## Related Decisions

- [ADR-0021](../decisions/0021-integration-layer.md) — the integration
  layer this consumes (`InfraIntegration.plan()`/`.deploy()`/
  `.destroy()`, `StoreIntegration.resolve()`), already implemented.
- [ADR-0022](../decisions/0022-strata-build-run.md) — D4 states the
  render-vs-execute split this whole doc builds on; also the origin of
  `ResolvedWorkspaceGraph`/`sync_source()`/`ordered_by_depends_on()`,
  reused unchanged here.
- [ADR-0023](../decisions/0023-build-output-rendering.md) — what `build
  run` already wrote to disk; `deploy run` executes against that output,
  never re-renders it.
- [build-command.md](build-command.md) — the sibling design doc for
  `build run`, whose own Remaining Work section confirms `build run`'s
  own scope is exhausted and everything left belongs here instead.
- [value-token-resolution.md](value-token-resolution.md) — the Value
  Supply Mechanisms A/B/C taxonomy; Mechanism B's deploy-time-only
  resolver is the direct blocker for item 4 above.
- [ADR-0027](../decisions/0027-strata-deploy-run.md) — the point-in-time
  decision record summarizing this doc's settled design (this doc remains
  the living history/evidence; the ADR is the immutable summary).

## Changelog

- 2026-09-27: Created. Triggered by finishing `build run`'s own gap
  analysis (see `build-command.md`'s Remaining Work) and confirming
  `modules`'s real motivating case was already served elsewhere
  (ADR-0026), leaving `deploy run` as the only substantial `build_run`-
  adjacent gap left. Investigation found `InfraIntegration.plan()`/
  `.deploy()`/`.destroy()` and full-featured `resolve_values()` already
  built and working, unused by anything — a materially smaller true gap
  than expected. Cross-checked both real repos' actual CI workflows
  directly (not just memory) and found one concrete missing schema
  field (`DeploymentStageModel.scope`) blocking the real, confirmed
  `--scope infra` flag.
- 2026-09-27: **Review pass — two corrections.** (1) `scope` is **not**
  a closed `infra`/`apps` enum — checked v1's real `deployment_model.py`
  directly: `scope: Optional[str]`, documented as a free-form CLI-filter
  label; haven's `infra`/`apps` are its own convention, not a schema
  vocabulary. Also found and flagged an unrelated, identically-named
  `DeploymentEnvironmentRef.scope` (promotion-system environment-file
  targeting) not to be confused with the stage-level one. Also flagged
  v1's real `topology`-direct stage binding (haven's actual data uses
  `topology: hetzner_hearth` on a stage, not a provisioner/step name) as
  worth re-confirming against v2's `step`-based simplification before
  implementing. (2) Confirmed `transport.run_command()`'s `env` kwarg
  already merges with the full process environment
  (`{**os.environ, **(env or {})}`) rather than replacing it — `TF_VAR_*`
  injection has nothing left to design on the transport side.
- 2026-09-27: **`scope` placement resolved: `ProvisioningStepModel`
  (workspace), not `DeploymentStageModel` (deployment).** Worked through
  the workspace/deployment separation-of-concerns question directly:
  whether a step is "infra" or "apps" is a fact about what the step does,
  not about which environment runs it, so it has no business varying — or
  being re-declared — per deployment. Direct precedent already in the
  codebase for this exact judgment: `ProvisioningStepModel`'s own
  docstring already treats "this step realizes topology X" as a *derived*
  fact, never declared (ADR-0011), for the identical drift-avoidance
  reason. Concludes the `topology`-binding open question from the
  previous review pass in the same direction: v1's direct `topology:`/
  `provisioner:` stage binding was the anti-pattern v2's `step`-only
  reference already correctly avoids, not a gap to fill. Added a worked
  example (workspace-level `scope`, deployment-level operational-only
  overrides, CLI usage) showing the same workspace deployed as `prd`
  vs. `dev` reusing an identical `scope` classification, differing only
  in `deployment.yaml`'s own stage overrides.
- 2026-09-27: **Added "Structural gotchas from v1" — process/safety
  patterns, not feature bloat.** Read v1's real `run_deploy_command.py`
  execution flow directly (not just its CLI surface). Confirmed
  `on_failure: "rollback"` has zero distinct implementation from `"stop"`
  anywhere in v1's real code (word appears only in comments). Found
  real, worth-deciding-deliberately process patterns: preflight-validate
  every stage's tooling/auth before acquiring any lock or running
  anything; filter stages *before* preflight-checking (a disabled stage's
  tooling is never required); `on_failure: continue` stages are still
  preflight-checked but downgraded to a warning; lock/cleanup always
  runs in a `finally`. Explicitly scoped these OUT of this phase per
  direction: locking and audit/SIEM logging are real, future concerns,
  not to be built now — but the orchestrator's loop (item 2 in Remaining
  Work) should be shaped with named extension points (a preflight pass,
  per-stage before/after hooks, an unconditional cleanup step) so adding
  them later doesn't require a rewrite. Also noted Terraform's own
  remote backends already provide real state-locking independent of
  strata, a fact worth handing to whoever designs locking later rather
  than reinventing it.
- 2026-09-27: **Design finalized — ready for development.** Added a
  concrete "Orchestrator shape" section (real pseudocode, not just
  prose) after confirming one more piece was already built:
  `TerraformIntegration.init()` (real `terraform init`, its own docstring
  already stating "the future deployer... calls this as its own step,
  same as v1's setup step") — confirms v1's real per-step sequence
  (`setup → check → plan → apply`) applies directly. Added a full,
  phased Implementation Plan (7 phases: `scope` field, deploy-time token
  substitution utility, orchestrator core loop, `TF_VAR_*` injection, CLI
  command, `output.template`'s actual render, final docs/ADR pass) —
  same phased/tested/full-check-suite-per-phase discipline as
  `build-time-value-categories.md`/`artifact-references.md`. Corrected
  one plan-writing error before it became implementation: no
  `validate_environment()` method exists on `Integration` — the real
  preflight primitives are `is_available()`/`ensure_version()`, both
  already built.
- 2026-09-27: **Phase 3 complete — `deploy_run()` core orchestrator
  implemented and tested.** `src/strata/controllers/deploy_controller.py`
  (new): resolve → filter (`--stage`/`--scope`) → preflight
  (`is_available()`) → per-step `init → validate → plan → deploy`
  (`init`/`validate` called via `getattr(..., None)` since both are
  Terraform-only extras, not part of the `InfraIntegration` ABC — keeps
  the loop tool-agnostic per ADR-0022). `force` maps directly to
  `deploy()`'s existing `auto_approve` kwarg (the `--force` CLI-semantics
  question itself, item 7 below, stays open — this is just the
  underlying plumbing). TF_VAR injection still an empty-dict placeholder,
  as planned, for phase 4. 10 new tests in a new
  `tests/strata/controllers/test_deploy_controller.py`, stubbing
  `run_command`/`shutil.which` rather than invoking a real `terraform`
  binary — same convention `test_integrations_terraform.py` already
  established. Full check suite green: 1094 tests, mypy clean across 106
  files, import-linter 1 kept/0 broken.
- 2026-09-27: **Phase 4 complete — `TF_VAR_<KEY>` secret injection
  wired in.** `tf_var_env(resolved: ValueResolution) -> dict[str, str]`
  added to `deploy_controller.py` — `{f"TF_VAR_{k}": v for k, v in
  resolved.values.items()}` — replacing phase 3's empty-dict placeholder,
  applied uniformly to every step's `init`/`validate`/`plan`/`deploy`
  calls regardless of tool (Compose/Helm simply never read the extra
  vars). 3 new tests: `tf_var_env()` unit coverage plus an end-to-end
  `deploy_run()` test confirming all four calls for a real step receive
  the resolved secret. Full check suite green: 1097 tests, mypy clean,
  import-linter 1 kept/0 broken.
- 2026-09-27: **`--force` semantics resolved; cross-step output context
  designed.** Read v1's real installed source directly (`uv tool`-
  installed `xyz-strata` v1.11.2) rather than relying on repo-usage
  inference alone: confirmed `--force` is *not* wired to terraform
  `-auto-approve` for a normal `deploy run` apply (that only happens for
  `deploy destroy`) — the real `run` path applies a saved plan file,
  which Terraform never prompts for regardless of flags. Phase 3's
  `auto_approve=force` wiring is therefore a known, tracked deviation
  (Implementation Plan phase 5) rather than the settled design. Also
  designed the previously-unaddressed cross-step output-passing gap (a
  step's Terraform outputs feeding a later, dependent step) — read v1's
  real `ResolvedValues.stage_outputs` mechanism, then compared it
  against GitHub Actions/GitLab CI/Azure Pipelines/Terraform remote
  state, all of which scope outputs to an explicit dependency and keep
  them namespaced (unlike v1's own global flat-pool behaviour, which
  proved to be the outlier, not the norm). Settled on a new
  `${output:step.key}` token kind, dependency-scoped, still translated
  to `TF_VAR_` only by the existing `tf_var_env()`, tracked as
  Implementation Plan phase 6.
- 2026-09-27: **Phase 5 complete — apply mechanism corrected.**
  `deploy_run()` now plans to a saved `<step>.tfplan` file
  (`plan(..., out_file=...)`) and applies that same file
  (`deploy(..., plan_file=...)`), removing the `auto_approve=force`
  wiring entirely — matches v1's real `TerraformDeployer.apply()`
  exactly (never reads `force`). `force` is now explicitly documented as
  inert in `deploy_run()`'s own docstring. 2 tests replaced (saved-plan
  argv shape; `-auto-approve` never appears regardless of `force`). Full
  check suite green: 1097 tests (net even), mypy clean, import-linter
  1 kept/0 broken.
- 2026-09-27: **Phase 6 complete — cross-step output context wired in.**
  New `${output:step.key}` token kind, dependency-scoped via a new
  `_upstream_step_names()` helper, collected via a new
  `collect_step_outputs()` helper. Found and fixed a real gap in the
  original design during implementation: `ComposeIntegration.output()`
  turned out to already exist (a `docker stack services` status listing,
  v1's own `output` step for Compose) with an incompatible signature —
  the plain `getattr(integration, "output", None)` check alone wasn't
  enough to stay tool-agnostic, so the call is now wrapped in
  `try/except TypeError` too. Also corrected the original pseudocode's
  `env = tf_var_env(resolved) | output_context.for_step(step)` sketch
  during implementation: outputs are resolved only via explicit
  `${output:}` tokens in schema fields, never folded into `env=` —
  ambient env injection was the exact v1 behaviour this design chose to
  avoid, and a `"step.key"`-shaped name isn't a valid Terraform variable
  identifier regardless. 8 new tests, 2 pre-existing ones updated for
  the new `output` call. Full check suite green: 1103 tests, mypy clean
  across 106 files, import-linter 1 kept/0 broken.
- 2026-09-27: **Phase 7 complete — CLI command shipped.**
  `src/strata/commands/deploy_command.py` (new), registered in `cli.py`.
  Matches `build_command.py`'s established shape exactly (positional
  `deployment`, `--path`, `--build-path` defaulting to the same
  `build_dir()` `build run` uses, `command_run()`/`open_solution(...)
  .require_valid()` lifecycle). `--force`'s help text is explicit about
  it currently being inert, rather than a silent no-op. 10 new tests in
  a new `tests/strata/commands/test_commands_deploy.py`, mirroring
  `test_commands_build.py`'s own structure. Full check suite green:
  1113 tests, mypy clean across 107 files, import-linter 1 kept/0
  broken.
- 2026-09-27: **Phase 8 complete — `output.template` actually renders at
  deploy time.** New `templater.render_template()` (switched the shared
  `_ENV` to `StrictUndefined` so an unresolvable reference raises rather
  than silently rendering empty) and
  `InfraIntegration.render_output_template()`, wired into
  `deploy_controller.py` before `init`. **Found and fixed a real,
  pre-existing bug in the process**: `deploy_run()`'s per-step directory
  was `build_path / step.name`, but `build_run()` actually materialises
  a sourced provisioner at `build_path / (source.target_path or
  source.source_path)` — every earlier phase's tests passed anyway
  because they stub `run_command` and never touch the real filesystem;
  this phase's render is the first code path to actually write a file,
  which is what surfaced the mismatch. Corrected to match `build_run()`'s
  real convention exactly. 14 new tests across three files. Full check
  suite green: 1122 tests, mypy clean across 107 files, import-linter
  1 kept/0 broken.
- 2026-09-27: **Phase 9 complete — docs pass, status updated,
  ADR-0027 drafted.** No code changed. While drafting the ADR's
  Consequences/Remaining Work, confirmed a real (not hypothetical) gap
  in Remaining Work item 8: `resolve_values()` has no `store: artifact`
  dispatch case at all, so it fails to resolve at deploy time today —
  upgraded from an open question to a tracked, deferred bug (zero real
  consumer yet, same as ADR-0026's own Path 1 deferral). Status changed
  to `partially-implemented`, reflecting the deliberately-deferred items
  (locking/SIEM/whole-run timeout/advanced `--force` gates/`store:
  artifact`) rather than `implemented`, which would overstate it.

