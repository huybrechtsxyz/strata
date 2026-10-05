# `strata deploy run` Command — Design

- Status: partially-implemented — orchestrator, CLI, `TF_VAR_` injection,
  cross-step output context, and `output.template`'s actual render are all
  built and tested (see Implementation Plan below); locking, SIEM/audit,
  whole-run timeout, and `--force`'s real advisory-gate consumption are
  deliberately deferred (no real evidence forces them yet); a confirmed
  gap (not just a hypothetical) remains for `store: artifact` at deploy
  time (Remaining Work item 8); Helm/Compose deploy orchestration
  (`docs/work/gap_fit_v1.md` gap #13) is now implemented for both container tools;
  cross-invocation output access (a later, separate `deploy run` reading
  an earlier one's outputs) is designed but not implemented (new section
  below), including a static `SUPPORTS_OUTPUT` capability flag and two
  validate/build-time checks for a doomed or non-durable `${output:}`
  reference — **review (2026-09-29) found a real, confirmed prerequisite
  bug blocking it**: `strata validate` already rejects every
  `${output:...}` token unconditionally today, even gap #12's own
  already-working, already-tested claimed example (empirically
  reproduced) — fixing that is now step (0) of this design, ahead of the
  new capability/checks
- Last updated: 2026-09-29 (design review found `unresolved_value_tokens()`
  is stale post-gap-#12 — added as prerequisite step (0))

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
  - config-deploy's real (non-template) `deploy-spoke-z01-s01.yml`
    has **zero** manual `TF_VAR_*`/secret wiring at all — Azure OIDC
    login (`azure/login@v2`) authenticates the process, then `strata
    deploy run` alone must be resolving and injecting every
    `store: azure-keyvault`/`azure-appconfig` secret itself. (Its
    sibling `deploy.yml`, a generated template, shows 3
    commented-out *manual* alternatives — Key Vault CLI, Vault CLI,
    plain GitHub secrets — presumably scaffolding for repos without a
    supported store backend configured yet, not evidence against
    auto-injection.)
- **`--scope` is real but haven-specific** — config-deploy's two
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
  see the flow below and `docs/work/gap_fit_v1.md`'s gap #6 for why).
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

## Helm/Compose orchestration — avoiding tool-type branching (docs/work/gap_fit_v1.md gap #13, 2026-09-28)

The pseudocode above is Terraform-shaped (`init`/`validate`/`plan`/`deploy`
against one root module per step) and is genuinely all that's wired up
today — `docs/work/gap_fit_v1.md` gap #13 found that a Helm/Compose step through
`deploy_run()` would crash (`HelmIntegration.plan()`/`.deploy()` require
`release`/`namespace`/`chart`, which nothing in `ProvisionerModel` supplies
and nothing in the loop above passes). This section is the concrete design
for closing that gap **without** `deploy_run()` ever branching on tool type
— per explicit request ("the goal is to avoid if=helm or if=terraform
code"), checked against real precedent rather than invented from scratch.

### Real precedent, both in v1 and already in v2 today

**v1** (`e:\SourcesXYZ\strata\src\strata\deployers\`) solves this exactly
this way — confirmed by reading the real code, not assumed:
- `DeployerFactory.create(provisioner_type, ...)` (`factory.py`) is a pure
  lookup-table dispatch (`{"terraform": TerraformDeployer, "helm":
  HelmDeployer, "compose": ComposeDeployer, ...}`) — the only place a tool
  name is ever read, and it never branches on it, just returns the right
  class.
- Every concrete `BaseDeployer` subclass implements its own
  `validate_workspace()`/`apply()`/`plan()`/etc. **its own way** — the
  orchestrator (`base_deploy_command.py`'s `_create_deployer()`,
  `run_deploy_command.py`'s step loop) only ever calls
  `getattr(deployer, step_name)()` polymorphically. Zero `if
  provisioner_type == "helm"` anywhere in the orchestrator.
- `HelmDeployer.validate_workspace()` discovers **every** namespace+module
  combination for the whole stage up front (iterating
  `deployment_service.get_namespace_services()` → each namespace's
  `spec.modules`, filtered by `stage.helm_namespaces`/`--namespace`),
  builds a flat `List[HelmModuleTarget]` (release name + chart coordinates
  read back from `meta.yaml` — the same self-contained build artifact
  `prepare_namespace()` already writes, zero new schema fields needed),
  and every lifecycle step method (`apply`/`plan`/`destroy`/...) loops
  over that same list, running one `helm upgrade`/etc. **per module**.
  Compose doesn't need this at all — it already merges every module in a
  namespace into one file at build time, so it stays one release-
  equivalent per namespace.

**v2 already has the identical pattern, proven, at build time** — not a
new idea, just extending one that already ships:
`workload_controller.build_workload_modules()` groups a namespace's
modules `by_type` (`{"helm": [...], "compose": [...]}`), resolves the
integration for each type via `resolve_module_integration()` (the same
kind of factory `resolve_integration()`/v1's `DeployerFactory` are), and
calls `integration.prepare_namespace(namespace, group, resolved=resolved)`
polymorphically — that function has **zero** branching on `module_type`
anywhere. `TerraformIntegration` never implements `prepare_namespace()`
(it doesn't need to — Terraform never goes through the namespace/module
pipeline at all, only the provisioner pipeline `prepare()` serves);
`InfraIntegration.prepare_namespace()`'s own base implementation raises
`IntegrationError` by default, exactly the "not abstract, subclasses that
need it override it" shape `default_output()` also uses.

### The design: a `deploy_namespace()` deploy-time counterpart

Mirror `prepare_namespace()` exactly, one level later in the pipeline —
same split, same non-abstract/raise-by-default shape, same "Compose merges,
Helm doesn't" per-tool freedom:

```python
# InfraIntegration (capabilities.py) — new method, alongside prepare_namespace()
def deploy_namespace(
    self,
    namespace: NamespaceModel,
    modules: list[ResolvedModule],
    *,
    tokens: dict[str, str],
    dry_run: bool,
) -> Diagnostics:
    """Deploy every module in `modules` — all attached to `namespace`.
    Mirrors prepare_namespace()'s own "not abstract, raise by default"
    shape (base raises IntegrationError; Terraform never implements it,
    same reason it never implements prepare_namespace() either)."""
    del namespace, modules, tokens, dry_run
    raise IntegrationError(f"{self.name} does not support namespace-scoped module deployment.")
```

- **`HelmIntegration.deploy_namespace()`**: for each module in `modules`,
  read its already-written `meta.yaml` (release/namespace/chart — zero new
  schema, matching v1 exactly), call `resolve_module_values(item.module,
  tokens)` (already built, gap #9 Phase 4) to get the resolved
  `values.yaml` dict + secret-shaped dotted paths, rewrite `values.yaml`,
  build `--set-string <path>=<value>` args from the secrets map, then call
  `self.plan()`/`.deploy()` with the release/namespace/chart just read —
  one `helm upgrade` per module, exactly v1's proven shape.
- **`ComposeIntegration.deploy_namespace()`**: resolve the already-merged
  compose file's tokens in one pass (a Compose-equivalent of
  `resolve_module_values()`, not yet built — Phase 5), rewrite it, then one
  `self.deploy()` call with `namespace=<namespace.meta.name>` (the Swarm
  stack name) — no per-module loop needed, matches the merge-at-build-time
  design already in place.

### Orchestrator side — one polymorphic call, no tool-type branching

**Corrected twice now, 2026-09-28** — first for coupling the wrong data to
the wrong resolution step (see below), then for a deeper mismatch with
v1's own real mechanism, found by direct pushback: v1 never infers "this
is an app deployment" from namespace-linkage at all.
`DeployerFactory.resolve_type()` (`factory.py`) is uniform for **every**
tool, infra or app — every stage explicitly names a provisioner (directly,
or via a topology's own `provisioner` field), and that provisioner's own
declared type picks the deployer class. A real `provisioner: helm` entry
is exactly as explicit as `provisioner: terraform` — v1 never asks "does
this stage's targets include a namespace" to decide *what kind* of stage
it is. What namespace-linkage actually does, one level further in
(`HelmDeployer.validate_workspace()`), is decide **which modules** an
already-known-to-be-Helm stage should touch — filtering
`deployment_service.get_namespace_services()`'s modules down to
`module.spec.type == ServiceDeployerType.HELM`, silently skipping any
mismatched type. Namespace-linkage answers "which modules", never "is this
stage Helm".

v2 already has the *cleaner* version of the exact same "what kind of stage
is this" signal v1's provisioner-type check provides — one layer more
principled, since it's a real capability the class declares, not a string
a human could misspell: `InfraIntegration.CAPABILITIES`
(`TerraformIntegration = frozenset({Capability.INFRASTRUCTURE})`,
`HelmIntegration`/`ComposeIntegration = frozenset({Capability.CONTAINER})`
— `Capability` (`models/integration_model.py`) is the closed, `str`-backed
enum every core capability name lives on, added specifically so a dispatch
check names a member instead of a bare string literal — see that class's
own docstring for why it isn't `enum.StrEnum`,
`capabilities.py`'s own docstring: "the label says what *kind* of thing is
provisioned... for a human reading the document; the contract is
identical"). `resolve_integration(index, provisioner)` — the exact call
every step already makes today, unchanged — already resolves this. No
second, separate resolution is needed, and no namespace-inference either:

```
for step in steps:
    provisioner = find_provisioner(workspace, step.provisioner)
    integration = resolve_integration(index, provisioner)   # unchanged, today's exact call

    if Capability.CONTAINER in integration.CAPABILITIES:
        namespaces_targeted = [graph.namespaces[t] for t in step.targets if t in graph.namespaces]
        for namespace in namespaces_targeted:
            # mirrors HelmDeployer.validate_workspace()'s own
            # `if module.spec.type != HELM: continue` filter exactly —
            # the SAME integration already resolved above, not a second
            # resolve_module_integration() call per module.
            modules = [m for m in namespace's resolved modules if m.module.spec.type == integration.TYPE]
            diagnostics.extend(integration.deploy_namespace(namespace, modules, tokens=tokens, dry_run=dry_run))
    else:
        # today's existing Terraform-shaped sequence, unchanged
        integration.init(...); integration.validate(...); integration.plan(...); integration.deploy(...)
```

Both branches now share **one** integration resolution — `step.provisioner`
is never unused, resolving the previous draft's own open consequence.
`Capability.CONTAINER in integration.CAPABILITIES` is a **capability** check
(a class-level property, set once per integration, the same kind of thing
`prepare_namespace()`'s own base-vs-override split already keys off) —
never a string comparison against `"helm"`/`"compose"`/`"terraform"`
anywhere in the orchestrator's own control flow. A namespace with
**mixed-type** modules simply needs one step per type, each bound to a
provisioner of that type — matching v1's real per-stage, per-tool-type
shape exactly (haven's own `applications_forge` stage is uniformly Helm;
a workspace needing both Helm and Compose modules in one namespace would
declare two steps, each targeting that namespace with its own
correctly-typed provisioner).

### Superseded: the previous two drafts' mistakes, kept visible for the record

1. **First draft** resolved `integration` from `step.provisioner` once and
   reused it for every module regardless of the module's own type —
   wrong, a namespace's modules can be mixed-type.
2. **Second draft** "fixed" that by abandoning `step.provisioner` entirely
   — grouping modules `by_type` and calling `resolve_module_integration()`
   per group, dispatching on whether `step.targets` contained a namespace
   at all. That over-corrected: it matched `build_workload_modules()`'s
   grouping shape, but diverged from v1's real mechanism (which always
   keeps an explicit, stage-level provisioner/type binding) and left
   `step.provisioner` genuinely unused for that step's real behaviour.
3. **This draft** keeps one integration resolution (`step.provisioner`,
   unchanged from today), uses its `CAPABILITIES` to pick the branch, and
   its own `TYPE` to filter which same-typed modules in the targeted
   namespace(s) it touches — faithful to v1, no unused field, no second
   resolution call, no branch naming a tool.

### Open design points, not yet resolved

1. v1 scopes Helm's namespace filter **per stage** (`stage.helm_namespaces`/
   `--namespace`), not per individual module-owning step. v2's natural
   equivalent is a step's own `targets` (matches gap #12's precedent
   exactly) — simpler, no separate CLI flag needed, but means a v1
   `--namespace` override at deploy time has no direct v2 equivalent yet
   (would need to filter `namespaces_targeted` further, if ever evidenced
   as needed).
2. `resolve_module_values()`'s Compose equivalent isn't built (Phase 5 is
   still blocked on this same gap #13, see `docs/design/
   value-token-resolution.md`).
3. This is a real refactor of `deploy_run()`'s current step-loop body —
   the existing Terraform-shaped sequence needs to become the "else"
   branch above, not a rewrite of its own logic, matching how gap #12
   made an additive schema change (not a rewrite) to `target_names`.
4. Not designed here: how deploy-time namespace/module resolution builds
   its own `list[ResolvedModule]` per namespace at deploy time — build
   time's `build_workload_modules()` does this already; deploy time needs
   the identical resolution (probably reusable directly, since neither the
   module list nor its source paths change between build and deploy).
5. A mismatched-type module in a targeted namespace (e.g. a Compose module
   sitting in a namespace a Helm-typed step targets) is silently skipped
   by the filter above, mirroring v1's identical silent skip — worth a
   validate-time diagnostic ("namespace X has a module of type Y no
   targeting step handles") rather than silent staying silent forever,
   not designed here.

### Implemented 2026-09-28 — Helm only

Built exactly as designed above, with the concrete pieces filled in:

- **`InfraIntegration.deploy_namespace()`** (`capabilities.py`): the base,
  not-abstract/raise-by-default method, `**kwargs: Any` added to its
  signature (override-compatible with `plan()`/`.deploy()`/`.destroy()`'s
  own `**kwargs: Any` convention) so a subclass can accept extra
  integration-specific kwargs (Helm's `remotes=`) without breaking the
  Liskov contract for one that hasn't overridden it (mypy caught this: a
  subclass signature with a narrower kwarg set than the base is an
  incompatible override).
- **`HelmIntegration.deploy_namespace()`** (`helm.py`): reads each
  module's already-written `meta.yaml`, resolves the chart reference
  (below), calls `resolve_module_values()` (gap #9 Phase 4), rewrites
  `values.yaml` for non-secret leaves, and calls `self.deploy()` with a
  new `set_string=` kwarg for the secret-shaped ones. A missing
  `meta.yaml` or an unresolvable chart reference reports a `Diagnostics`
  error for that module and continues with the rest — never raises,
  matching the method's own contract.
- **Chart reference resolution** (`_resolve_chart()`, `helm.py`): no
  `chartName` in `meta.yaml` (git-based/local chart `source`) → the
  module's own `item.source_path` **is** the chart, `_resolve_chart()` is
  not even called. Otherwise, the `chartRemote` name is looked up in a
  `{name: SolutionRemoteModel}` dict (`deploy_controller.py` builds it
  identically to `build_controller.py`'s own `remotes` lookup — same
  `context.controller.solution.spec.remotes` source). `RemoteType.OCI` →
  `f"{url}/{chart_name}"` directly (no repo add — Helm resolves `oci://`
  natively). `RemoteType.HELM` → `helm repo add <alias> <url>` first
  (alias via `_sanitize_repo_name()`, ported verbatim from v1's real
  `helm_deployer.py`), then `f"{alias}/{chart_name}"`. `RemoteType.GIT`/
  `LOCAL` are rejected with a clear error (neither is a valid chart
  registry type).
- **`--set-string` delivery** (`plan()`/`.deploy()`, `helm.py`): new
  `set_string: Sequence[tuple[str, str]] | None` kwarg, rendered via
  `_set_string_args()` → `_escape_set_value()` (both ported verbatim from
  v1's real `helm_deployer.py`) — secret values are backslash-escaped for
  Helm's `--set` mini-language (`\ , . = { } [ ]`) so a value containing
  one of those characters survives as a literal string.
- **Deploy-time module resolution** (`resolve_namespace_modules()`,
  `workload_controller.py`): mirrors `build_workload_modules()`'s
  per-reference loop, grouped `by_type`, but never re-materialises a
  module's source (deploy time only deploys what `build run` already
  rendered) — resolves the answer to open design point 4 above.
- **Orchestrator wiring** (`deploy_controller.py`): the per-step loop now
  branches on `Capability.CONTAINER in integration.CAPABILITIES` exactly as
  designed — the container branch resolves `step.targets` against
  `graph.namespaces`, calls `resolve_namespace_modules()` per namespace,
  filters to `integration.TYPE`, and calls `deploy_namespace()`; the
  `else` branch is the pre-existing Terraform-shaped sequence, untouched.
  A container-capable step whose `targets` names no namespace is a clear
  preflight-style error, not a silent no-op.
- **Compose implemented the same week** (2026-09-28, Full Solution Phase 5):
  `ComposeIntegration.deploy_namespace()` reads the namespace's already-merged
  `docker-compose.yml` (one file per namespace, not per module — Compose
  merges at build time), resolves it via the new `resolve_compose_values()`
  (`compose.py`, wrapping `value_tokens.py`'s new
  `resolve_value_tokens_renaming_secrets()`), and calls one `docker stack
  deploy` for the whole namespace. Secret tokens are renamed to Compose's
  own bare `${KEY}` interpolation syntax and delivered via the subprocess's
  own environment — never written to disk. Open design point 2 (Phase 5's
  Compose value-resolution primitive) is now resolved.
- Open design points 1 (v1's `--namespace` CLI override) and 5
  (mismatched-type-module validate-time diagnostic) remain unresolved —
  neither blocks either container tool's own deploy path, both left for a
  future pass.
- **Verified:** 40 tests in `test_integrations_helm.py`, 6 in
  `test_integrations_compose.py`, 11 in `test_utils_value_tokens.py`, 4 new
  tests in `test_deploy_controller.py` (end-to-end dispatch for both Helm
  and Compose alongside an unchanged Terraform step, scope filtering,
  no-matching-target error); full check suite green (mypy 107 files, ruff
  clean, import-linter 1/0, pytest 1203 passed).


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

## Cross-invocation output access — a later, separate `deploy run` reading an earlier one's outputs

The section above solves output-chaining **within one `deploy run`
process** (dependency-scoped, in-memory `step_outputs`, discarded at exit
— confirmed directly in `deploy_controller.py`'s own `step_outputs: dict[str,
dict[str, str]] = {}`, a plain local variable). It does not solve a
different, real question: what does a step get when `${output:X.key}`
names a step `X` that did **not** run in *this* invocation — because
`--stage`/`--scope` filtered it out, or because it was a wholly separate,
earlier `strata deploy run` process? Design settled 2026-09-29, per direct
request ("how to 'store' terraform output for another deploy run... not
only terraform - but for all provisioners"), found by tracing what v1's
real production CI actually does for this exact case, not assuming a new
store is needed.

**v1 never solved this either.** `ResolvedValues` (ADR-0006's own v1
precedent) is built once per deploy run and mutated stage-by-stage for
that process's lifetime only — no disk/remote persistence anywhere in its
real source. And the real haven CI genuinely *is* split across separate
invocations on fresh, ephemeral runners with no shared disk: `10 - Infra -
haven` (`deploy-infra.yml`, Terraform apply) runs as one completely
separate GitHub Actions job from `21 - Hearth - Init`
(`deploy-hearth-init.yml`), which needs the VM Terraform just created.

**How the later job actually gets that VM's IP today is the key finding:
it does not go through strata's output/Context mechanism at all.**
`.github/actions/hetzner-ssh-open/action.yml` does a plain
`curl -H "Authorization: Bearer $HETZNER_API_TOKEN"
https://api.hetzner.cloud/v1/servers?name=haven-platform-hearth`, looked
up **by the server's well-known name** — bypassing `terraform output`,
`ResolvedValues`, and strata entirely. The durable store the later job
actually reads is the cloud provider's own API, not anything strata
persisted.

### Design: opportunistic live re-collection, not a new persisted store

Rejected building a strata-owned cache (a JSON file under `.strata/`, a
remote blob, etc.) as the primary mechanism for two reasons, both
evidence-based, not hypothetical:

1. **A local cache would not even survive the real topology it's meant
   for** — haven's separate deploy jobs run on fresh, ephemeral runners
   with no shared filesystem between them, so a `.strata/`-local cache
   written by `deploy-infra.yml`'s job would already be gone before
   `deploy-hearth-init.yml`'s job started.
2. **No real consumer needs anything beyond what the tool's own state
   already durably provides.** Terraform (and anything backed by real
   remote state — OpenTofu, Bicep via `az deployment show`) already
   persists its outputs remotely, in the state backend
   `provisioner.backend.configuration` already names — re-reading them
   later needs no new strata machinery, just permission to ask again.

Instead: when `${output:X.key}` names a step `X` **not** among this
invocation's filtered `resolved_steps`, look up `X`'s `ProvisionerModel`
from `workspace.spec.provisioners` (always available regardless of
`--stage`/`--scope` — filtering only narrows *execution*, never the
workspace's own declarations) and its already-`build run`-rendered
directory at `build_path/X.name` (`build_run()` renders every declared
provisioner unconditionally, not just ones a later `deploy run` happens
to filter to — so this directory already exists on disk before `deploy
run` is ever invoked). Resolve that integration
(`resolve_integration(index, provisioner)`, the same call already used
for every step), call its `init()` (safe/idempotent — reads the same
remote backend, mutates nothing) then its `output(path,
json_format=True, env=env)` — exactly `collect_step_outputs()`'s existing
logic, called opportunistically for a *not-currently-running* step
instead of only a just-deployed one. Merge the result into `step_outputs`
before token resolution, so a later, separate invocation resolves
`${output:X.key}` identically to how a same-invocation dependent step
already does.

### A formal `SUPPORTS_OUTPUT` capability, not just duck-typing

`collect_step_outputs()` today decides per-instance, at the moment it's
called, via `getattr(integration, "output", None)` — fine for *runtime*
dispatch (exactly the same pattern already used for `init`/`validate`),
but insufficient for what's needed next: knowing, **before any step
runs** (at `strata validate`/`build run` time), whether a given
`${output:X.key}` reference could *ever* resolve. A `getattr` check can't
run then — no integration instance necessarily exists yet, and the point
is to catch the mistake before spending a deploy attempt on it.

Add a class-level capability, mirroring the exact convention
`ENV_VAR_PREFIX` already established on `Integration` (`base.py`) for the
identical shape of problem ("a fact about what this tool can do, knowable
statically, defaulting to the safe/absent case"):

```python
class Integration(ABC):
    ...
    #: Whether this integration can report already-applied outputs after
    #: the fact (`output(path, json_format=True)`), independent of
    #: whether *this* deploy run's step actually ran — true only for tools
    #: backed by real, independently-durable state (Terraform's remote
    #: backend: re-querying needs nothing this run itself produced).
    #: `False` (default) for tools with no native "outputs" concept at all
    #: (Helm, Compose) — confirmed, not merely unbuilt, per the "Cross-step
    #: output context" section above (no native outputs concept in either
    #: real reference repo).
    SUPPORTS_OUTPUT: ClassVar[bool] = False
```

`TerraformIntegration.SUPPORTS_OUTPUT = True`; every other integration
keeps the base class's `False` default, with no per-class override
needed unless a future integration (OpenTofu, Bicep via
`az deployment show`) earns one the same way. `collect_step_outputs()`
and the opportunistic re-collection above both switch from
`getattr(integration, "output", None)` to checking `SUPPORTS_OUTPUT`
first (still calling `getattr` for the actual method — the flag says
*whether*, the method is still *how*) — no behavior change for either,
just a name a validator can also ask *before* any integration is
instantiated for real.

### Static validation — catching a doomed `${output:}` reference at validate/build time, not deploy time

This is the concrete answer to "we can already in validate or build find
out that certain things would not work with warning or error" — two
distinct, independently-checkable failure modes, both knowable from the
schema alone (no cloud call, no subprocess):

1. **Error — the referenced step's tool has no output mechanism at
   all.** `${output:X.key}` where `X`'s resolved integration has
   `SUPPORTS_OUTPUT = False` (a Helm/Compose/Ansible step, or any custom
   plugin that hasn't opted in) can **never** resolve, in any invocation,
   same-process or not — this is not a "might not work later" case, it's
   already wrong today.
2. **Warning — the referenced step's tool supports output, but this
   specific instance has nothing durable to re-query later.** A
   Terraform provisioner with no `backend:` configured keeps its state
   **local to that one `build_path/X.name` directory** — real within the
   *same* invocation (the directory still exists, `terraform output`
   still works), but gone the moment a later, separate invocation starts
   from a fresh checkout/fresh `build_path` (exactly haven's real
   ephemeral-runner topology). Same check, second condition: `X`'s
   integration has `SUPPORTS_OUTPUT = True` but `X`'s `ProvisionerModel.
   backend` is `None` → warning, not error (it demonstrably works
   *today*, within one invocation — gap #12's own worked example proves
   that path already — the risk is only realized if a future invocation
   ever needs it split across processes, which the schema alone cannot
   rule out or confirm).

#### Blocking prerequisite found on review (2026-09-29): `strata validate` already rejects EVERY `${output:...}` token today, even ones that already work

Re-reading `unresolved_value_tokens()` (`environment_service.py`, the
function `strata validate`'s Phase 2 pass actually calls, via
`_check_deployment_value_tokens()` in `semantic_checks.py`) before
writing the two checks above as new code, its `kind not in declared`
branch still carries this exact comment, unchanged since gap #11:

> `${output:}` is not yet wired into any of the fields this function
> checks (DNS/network/firewall/module), pending Context (ADR-0006).

That is no longer true — gap #12 wired real, tested, step-owned
`${output:...}` resolution into exactly DNS/network/firewall documents,
in `deploy_controller.py`. But `unresolved_value_tokens()` was never
updated to match: it still unconditionally emits
`unsupported_value_token_kind` for **every** `${output:...}` token,
regardless of whether a step claims it. **Confirmed empirically, not just
read** — ran `unresolved_value_tokens()` directly against the identical
DNS document gap #12's own passing test
(`test_deploy_run_resolves_output_token_in_dns_via_owning_step_targets`)
uses, with `${output:provision-hearth.public_ip}` claimed by a real
`apply-dns` step:

```
ok: False
spec.zones[0].records[0].value: '${output:provision-hearth.public_ip}'
tokens are not supported in this field yet (pending Context, ADR-0006).
[unsupported_value_token_kind]
```

That test only calls `open_solution(root)` (Phase 1 only) before calling
`deploy_run()` directly — it never calls `context.resolve()` (the real
`strata validate` Phase 2 path), so this inconsistency was never caught:
**a real, correctly-authored, already-deploy-time-working document is
rejected by `strata validate` alone today.** This is a genuine,
independent gap — not a consequence of anything proposed above, and worth
fixing regardless of whether the rest of this design is ever built.

**Consequence for this design**: checks 1/2 above are unreachable as
additive new code — `unresolved_value_tokens()`'s current blanket
rejection fires first, for every `${output:...}` token, claimed or not.

#### Expanded finding (2026-09-29): the same bug also affects Modules, not just DNS/network/firewall

`unresolved_value_tokens()` has no per-document-kind special-casing at
all — it walks `model_dump()` generically, so the blanket rejection above
applies identically to a Module's `services[].environment[].value`. Gap
#12's own "modules are already safe... no new design work needed there"
note is about **deploy-time** dependency ordering (a module's namespace
being a valid `targets` entry forces step ordering via
`validate_provisioning_steps()`) — it says nothing about **validate-time**
acceptance, and does not touch `unresolved_value_tokens()` at all. So a
module already correctly resolving `${output:X.key}` at deploy time
(`HelmIntegration.deploy_namespace()`'s own `tokens = {**resolved.values,
**visible_outputs}`) is *also* rejected by `strata validate` today, for
the identical reason as the DNS case above — confirmed by inspection of
`_documents_reachable_from_workspace()` (`semantic_checks.py`): it
flattens every document (dns/network/firewall/**module**/workspace/
resource/provider/topology) into one plain `list[PlatformBaseModel]` with
no per-document memory of which step (if any) claims it, before handing
each to the same `unresolved_value_tokens()` call uniformly.

### Concrete implementation plan (design only, not yet built)

1. **`WorkspaceService.claimed_document_names()`** (new method,
   `workspace_service.py`) — a pure function of this workspace's own
   declarations, no other loaded document needed. Generalizes gap #12's
   dns/network/firewall-only claiming to the *same* `target_names` set
   `workspace_model.py`'s own `validate_execution()` already validates
   against (`resources ∪ namespaces ∪ dns_zones ∪ networks ∪ firewalls`)
   — a module reached through a targeted Namespace is exactly as claimed
   as a directly-targeted DNS document, using the schema's own existing
   target vocabulary rather than a second, narrower one:
   ```python
   def claimed_document_names(self) -> set[str]:
       """Every dns_zones/networks/firewalls/namespaces name claimed by
       some execution step's own `targets` — the ownership half of
       `${output:...}` resolution (docs/work/gap_fit_v1.md gap #12, generalized
       to namespaces so a module inside a targeted namespace is covered
       too, per the 2026-09-29 review finding this also affects Modules).
       """
       if self.model is None:
           return set()
       spec = self.model.spec
       claimable = set(spec.dns_zones or []) | set(spec.networks or []) | set(spec.firewalls or []) | set(spec.namespaces or [])
       claimed: set[str] = set()
       for step in spec.execution or []:
           claimed |= set(step.targets) & claimable
       return claimed
   ```
2. **`_documents_reachable_from_workspace()`** (`semantic_checks.py`) —
   change its return type from `list[PlatformBaseModel]` to
   `list[tuple[PlatformBaseModel, bool]]` (document, is_claimed), computed
   once via step 1's new method before the existing walk: a dns/network/
   firewall document's claim comes from its own name; a module's claim
   comes from the **namespace it was reached through** (the existing
   `for name in spec.namespaces` branch already has that name in scope —
   just needs to thread the resulting bool alongside each
   `module_entry.model` it appends instead of a bare append). Every other
   document kind in the walk (workspace/resource/provider/topology, and
   topology-attached modules) is never claimable today — `is_claimed =
   False` unconditionally for those, unchanged behavior.
3. **`unresolved_value_tokens()`** (`environment_service.py`) — new
   `output_claimed: bool = False` parameter. When checking a token of
   kind `"output"`: `output_claimed=True` → accept it (key existence
   still unvalidatable — matches ADR-0006's own admission that Context
   "does not solve output validation" — this only fixes the false
   rejection, not add new key-checking); `output_claimed=False`
   (default, every existing caller unaffected) → today's exact
   `unsupported_value_token_kind` message, unchanged wording, still
   correctly fires for a genuinely-unclaimed document.
4. **`_check_deployment_value_tokens()`** (`semantic_checks.py`) —
   iterate the new `(document, claimed)` pairs and pass
   `output_claimed=claimed` through to `unresolved_value_tokens()`
   instead of today's flat `for document in documents`.

Deliberately **not** in this step: checks 1/2 (`SUPPORTS_OUTPUT`/backend
durability) themselves — those need `output_claimed=True` to be reachable
first, and are their own, separate follow-on step per the Status section
below, not bundled into this fix.

This means `_contains_output_token()`/the claim-computation currently
inline inside `deploy_run()` (`claimed_by_category`) are a **separate,
narrower, already-correct** mechanism (per-category, feeding `TF_VAR_`
delivery) that this fix does not need to touch or reuse — step 1 above is
a new, independent, coarser (claimed-or-not, no category) computation
purpose-built for the validate-time question, not a refactor of the
deploy-time one. Both can coexist; unifying them is not required for this
fix and is not proposed here.

Both checks are purely structural (provisioner + integration type lookup,
no execution) — they belong in `strata validate`/`build run`'s existing
Phase 2 pass, not deploy time, matching gap #10's own "catch it before it
ever reaches a real deployment" framing.

This is generic across provisioners by construction, not by special-
casing Terraform: dispatch is `SUPPORTS_OUTPUT`/`getattr(integration,
"output", None)`-based (`collect_step_outputs()`), so a Helm/Compose
upstream step contributes nothing here too — matching the confirmed real
precedent above that those tools' cross-invocation facts are the
caller's problem (a Hetzner API lookup, an `az deployment show`,
whatever fits the target platform), not something strata's own output
plumbing was ever asked to solve.

### Worked example

```yaml
# workspace.yaml (abridged) — two steps, no shared invocation required
spec:
  provisioners:
    - name: core_iac        # provisions the VM, has a real remote backend
      tool: terraform
      source: {source_path: infra/terraform}
      backend:
        type: azurerm
        configuration: {resource_group_name: ..., storage_account_name: ..., key: infra.tfstate}
    - name: app_config      # a later, separate step — e.g. Ansible/Helm
      tool: ansible
      source: {source_path: infra/ansible}

  execution:
    - name: provision-infra
      provisioner: core_iac
      scope: infra
      targets: [vm_resx]
    - name: configure-app
      provisioner: app_config
      scope: apps
      targets: [vm_resx]
      depends_on: [provision-infra]
      # references ${output:provision-infra.vm_ip} in its own config
```

- **Day 1, CI job A**: `strata deploy run deployment --scope infra` runs
  `provision-infra` only. `step_outputs["provision-infra"]` is populated
  from a real `terraform apply`, used immediately if anything else in
  *this* invocation needs it, then the process exits — nothing persisted.
- **Day 2, CI job B, a different runner, fresh checkout**:
  `strata deploy run deployment --scope apps` runs only `configure-app`.
  `provision-infra` is not in `resolved_steps` this time — but
  `${output:provision-infra.vm_ip}` is still referenced. Per this design:
  resolve `core_iac`'s integration, find `build_path/provision-infra`
  (already rendered by job B's own preceding `build run`), run
  `terraform init` (idempotent, points at the same `infra.tfstate` blob)
  then `terraform output -json` — no re-apply, just a read against the
  state job A already wrote remotely — and use that value to resolve the
  token for `configure-app`, exactly as if both steps had run in one
  process.

**Two variations on the same example showing the new static checks:**

- **Error variant**: the check fires on the **named** step (`provision-infra`,
  the `X` in `${output:X.key}`), not the referencing step — if
  `provision-infra` used `tool: helm` instead of `terraform` (its
  integration has `SUPPORTS_OUTPUT = False`), `strata validate`/`build run`
  reports it immediately, before any step ever runs: `"'configure-app'
  references '${output:provision-infra.vm_ip}', but provisioner 'core_iac'
  (tool 'helm') has no output mechanism — this can never resolve."
  [output_reference_unsupported]`
- **Warning variant**: if `core_iac` above had no `backend:` block at all
  (local Terraform state only), `strata validate` reports:
  `"'configure-app' references '${output:provision-infra.vm_ip}' — "
  "provisioner 'core_iac' has no 'backend', so this only resolves within "
  "the same deploy run that provisions it; a later, separate 'deploy run' "
  "will not see it." [output_reference_not_durable]`, a warning (not an
  error — `--strict` would upgrade it, matching every other warning in
  v2's validate design, ADR-0019).

### Status

Design only, not yet implemented — four parts now, in dependency order:
(0) **prerequisite, independently valuable on its own, and now confirmed
to affect Modules too, not just DNS/network/firewall**: add
`WorkspaceService.claimed_document_names()`, make
`_documents_reachable_from_workspace()` return claiming alongside each
document, and give `unresolved_value_tokens()` an `output_claimed`
parameter — see "Concrete implementation plan" above for the exact
4-step breakdown. This alone fixes a confirmed real bug (`strata
validate` currently rejects gap #12's own already-working, already-tested
DNS example, and would reject an equally-valid claimed Module example the
same way); (1) the `SUPPORTS_OUTPUT` capability flag on `Integration`;
(2) the two Phase 2 static checks (`output_reference_unsupported` error,
`output_reference_not_durable` warning), layered on top of (0)'s now-
claiming-aware check; (3) the opportunistic live-re-collection itself in
`deploy_run()`. (0)-(2) need no new deploy mechanism to exist first and
are independently useful even
before (3) is built. `docs/work/gap_fit_v1.md` was not updated with a new numbered
gap for (1)-(3) — a genuine capability *extension* beyond both v1 and v2's
current design (v1 never solved cross-invocation output access via strata
either). (0) **is** exactly gap-shaped (a real, confirmed, currently-wrong
behavior) and should get its own numbered entry in `docs/work/gap_fit_v1.md` when
this is implemented, separate from the (1)-(3) extension.

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
   comment) and confirmed universally relied upon (config-deploy's
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
- 2026-09-28: **Designed Helm/Compose orchestration** (`docs/work/gap_fit_v1.md`
  gap #13), per direct request to look at how v1 avoided
  `if tool == "helm"`/`if tool == "terraform"` branching and design v2's
  fix the same way. Read v1's real `deployers/` package directly:
  `DeployerFactory.create(provisioner_type, ...)` is a pure lookup-table
  dispatch, and `HelmDeployer` discovers every namespace+module
  combination for the whole stage up front (`validate_workspace()`),
  building a flat target list every lifecycle step method then loops
  over — one `helm upgrade` per module, release/chart read back from
  `meta.yaml` (self-contained build artifact, zero extra schema).
  Confirmed v2 already has the identical pattern, proven, at build time:
  `workload_controller.build_workload_modules()` groups a namespace's
  modules `by_type`, resolves the integration via
  `resolve_module_integration()`, and calls
  `integration.prepare_namespace(...)` polymorphically — zero
  `module_type` branching anywhere. Designed a `deploy_namespace()`
  deploy-time counterpart mirroring `prepare_namespace()`'s own
  "not abstract, raise-by-default" shape exactly, consuming gap #9
  Phase 4's already-built `resolve_module_values()` for Helm. The
  orchestrator dispatches on **data** (does this step's `targets`
  include a namespace?), never on tool type — a Terraform step's
  `targets` never contains a namespace, so it always falls through to
  today's unchanged sequence without any branch naming Terraform.
  Design only — nothing implemented; flagged as a real refactor of
  `deploy_run()`'s current step-loop body, not a pure addition.
- 2026-09-28: **Validated the Helm/Compose orchestration design against
  literal-name coupling before implementing, per request** ("let's
  validate the design is not linked to literal provisioner names").
  Found a real bug in the first draft's orchestrator pseudocode: it
  resolved `integration` once from `step.provisioner`'s own `.tool` and
  reused it for every module in a targeted namespace — wrong, since a
  namespace's modules can be mixed-type, and `build_workload_modules()`
  (the real precedent this design leans on) never resolves a module's
  integration from any `ProvisionerModel` at all — it groups `by_type`
  and calls `resolve_module_integration()` per group, independent of any
  step. Corrected the pseudocode to do the identical grouping at deploy
  time. Confirmed separately that `resolve_integration()`/
  `resolve_module_integration()` themselves were already clean — both
  dispatch purely on type strings (`.tool`/`module.spec.type`), never on
  any document's `.name`. Flagged one new open consequence: `step
  .provisioner` becomes unused for a namespace-targeting step's actual
  deploy action under this correction — still a required schema field
  with no conditional exemption. Design only, still nothing implemented.
- 2026-09-28: **Corrected the orchestration design again**, per direct
  pushback ("so also in v1 there was this split? its not a compose
  provisioner or helm provisioner it is the link to the namespace that
  makes it either helm/compose"). Re-checked v1's real
  `DeployerFactory.resolve_type()` directly: it is uniform for every tool,
  infra or app — every stage explicitly names a provisioner (or a
  topology naming one), and that provisioner's own declared type picks
  the deployer class. v1 never infers "this is an app deployment" from
  namespace-linkage — that only decides *which modules* an
  already-known-to-be-Helm stage touches, one level further in. Corrected
  the previous draft (which dispatched on whether `step.targets` contained
  a namespace, abandoning `step.provisioner` entirely) to instead branch
  on `integration.CAPABILITIES` (`"infrastructure"` vs `"container"`) —
  the exact "what kind of stage is this" signal v1's provisioner-type
  check provides, already resolved by the same `resolve_integration()`
  call every step makes today. `step.targets` now only decides which
  namespaces a container-capability step concerns; `integration.TYPE`
  filters same-typed modules within them, mirroring
  `HelmDeployer.validate_workspace()`'s own `if module.spec.type != HELM:
  continue` filter exactly. This resolves the previous draft's own open
  consequence (`step.provisioner` unused) — one integration resolution,
  shared by both branches, `step.provisioner` always meaningful. Both
  earlier drafts' mistakes kept visible in a new "Superseded" subsection
  rather than silently overwritten. Design only, still nothing
  implemented.
- 2026-09-28: **Implemented the CAPABILITIES-based design (Helm only)**.
  Added `InfraIntegration.deploy_namespace()` (`capabilities.py`,
  `**kwargs: Any` needed for a Liskov-compatible override — mypy caught
  the narrower subclass signature), `HelmIntegration.deploy_namespace()`
  (`helm.py`, reads `meta.yaml`, resolves chart ref, calls
  `resolve_module_values()`, delivers secrets via a new `set_string=`
  kwarg on `plan()`/`.deploy()`), chart-reference resolution
  (`_resolve_chart()`/`_sanitize_repo_name()`/`_escape_set_value()`/
  `_set_string_args()`, the latter three ported verbatim from v1's real
  `helm_deployer.py`), a deploy-time module-resolution helper
  (`resolve_namespace_modules()`, `workload_controller.py`, mirrors
  `build_workload_modules()` without re-materialising sources), and the
  `deploy_controller.py` orchestrator branch exactly as designed. Genuine
  new sub-problem surfaced mid-implementation (not anticipated by the
  design): `meta.yaml`'s `chartRemote` is a *name*, not a URL — resolving
  it needed a real `{name: SolutionRemoteModel}` lookup (rebuilt in
  `deploy_controller.py` identically to `build_controller.py`'s own) and
  an OCI-vs-HTTP-registry branch on `RemoteType`, since v2's remotes are
  typed distinctly (`git`/`oci`/`helm`/`local`) rather than v1's
  URL-prefix-sniffing. Compose left unimplemented (Phase 5 still
  blocked). 40 tests in `test_integrations_helm.py`, 3 in
  `test_deploy_controller.py`. Full check suite green: mypy 107 files,
  ruff clean, import-linter 1/0, pytest 1185 passed.
- 2026-09-28: **Replaced bare capability string literals with a `Capability`
  enum**, per direct request ("this should at least be an enum right we do
  not want string values in ifs"). Added `Capability(str, Enum)`
  (`models/integration_model.py`, not `enum.StrEnum` — needs Python 3.11+,
  this project's floor is 3.10) covering the closed core vocabulary
  (`variables`/`secrets`/`features`/`infrastructure`/`container`/`sources`);
  `VALID_INTEGRATION_CAPABILITIES` now derives from it
  (`frozenset(Capability)`) so the two can never drift. Every real
  integration class's `CAPABILITIES` declaration
  (`terraform.py`/`helm.py`/`compose.py`/the three store resolvers) and
  `capabilities.py`'s `CAPABILITY_ABCS` dict now use `Capability.X`
  members instead of string literals; the flagged
  `if "container" in integration.CAPABILITIES:` line in
  `deploy_controller.py` is now `if Capability.CONTAINER in
  integration.CAPABILITIES:`. `IntegrationSpecModel.capabilities`/
  `Integration.CAPABILITIES` deliberately stay `set[str]`/`frozenset[str]`
  — an `x-`-prefixed plugin capability (real, tested:
  `test_integrations_registry.py`'s `"x-ticketing"`) has no enum member by
  design, so the field/ClassVar type can't be narrowed to `Capability`
  without breaking that legitimate open-extension case; `Capability <: str`
  makes plain-string membership checks/dict lookups against it work
  unchanged either direction. Initially built as a hand-rolled `str, Enum`
  mixin (project floor was still declared 3.10) with an explicit `__str__`
  override to work around a real formatting gotcha — a hand-rolled mixin
  does NOT get `str`'s own `__format__`/`__str__` for free, unlike
  `enum.StrEnum` (confirmed empirically: an f-string like
  `f"'{capability}'"` rendered `'Capability.CONTAINER'` instead of
  `'container'`). **Superseded same day**: per direct question ("you can
  assume 3.13+ no?"), confirmed the rest of the project already assumes
  3.13 (`.cruft.json`, both CI workflows already pin `PYTHON_VERSION:
  "3.13"`) while `pyproject.toml`'s `requires-python`/mypy/ruff still
  claimed a stale `>=3.10` floor — corrected all three to `3.13`, then
  simplified `Capability` to a plain `enum.StrEnum` (no `__str__` override
  needed — `StrEnum` gets the plain-value formatting natively). No test
  changes needed — full check suite green: mypy 107 files, ruff clean,
  import-linter 1/0, pytest 1185 passed.
- 2026-09-28: **Implemented Compose's `deploy_namespace()`** (Full Solution
  Phase 5), closing gap #13 for both container tools. `ComposeIntegration.
  deploy_namespace()` mirrors Helm's shape but is genuinely simpler in one
  way (one `docker stack deploy` per namespace, since Compose already
  merged every module into one file at build time) and needs a different
  resolution primitive in another: `resolve_value_tokens_renaming_secrets()`
  (`value_tokens.py`) resolves per-token, not per-leaf, since Compose has
  no whole-path override mechanism the way Helm's `--set-string` does —
  a `${secret:KEY}` token is renamed to Compose's own bare `${KEY}`
  syntax and the real value delivered only via the `docker stack deploy`
  subprocess's own environment (`Integration.run()`'s existing per-call
  `env` merge — no `.env` file needed, simpler than v1's real
  `os.environ`-mutating `inject_compose_env()` context manager). 11 new
  tests in `test_utils_value_tokens.py`, 6 in `test_integrations_compose.py`,
  1 new end-to-end dispatch test in `test_deploy_controller.py` mirroring
  the existing Helm one. Full check suite green: mypy 107 files, ruff
  clean, import-linter 1/0, pytest 1203 passed.
- 2026-09-28: **`tf_var_env()`/dns-networks-firewalls/Phase-6-configuration
  delivery now name their env var via `integration.ENV_VAR_PREFIX`, never
  a hardcoded `"TF_VAR_"` literal**, per direct question ("why would we
  always use TF_VAR while terraform might not even be in the project for
  others?"). New `Integration.ENV_VAR_PREFIX: ClassVar[str | None] = None`
  (`base.py`) — `TerraformIntegration` is the only class that overrides it
  (`"TF_VAR_"`, Terraform's own real CLI contract, not a strata
  convention). Confirmed a generic `STRATA_<name>` alternative genuinely
  can't work uniformly: Helm has no env-var substitution mechanism at all
  (only `-f`/`--set*` CLI flags); Compose uses unprefixed `${KEY}`
  interpolation, a different mechanism, not "an empty prefix" — each tool
  already has its own real delivery mechanism (Phases 2/4/5), and none of
  the three could read a fourth, invented prefix. This also fixed a real,
  if harmless, pre-existing leak: `tf_var_env(resolved)` ran unconditionally
  before the container-capability branch, so a Helm/Compose step's own
  `deploy_namespace()` call previously received `TF_VAR_`-prefixed secrets
  meant for Terraform in its `env=` kwarg — `tf_var_env()` now returns `{}`
  for a `None` prefix, so that stops. The two delivery loops inside the
  Terraform-shaped branch are now also guarded by `integration.
  ENV_VAR_PREFIX is not None`, both to avoid the same leak for any future
  non-container infra tool and to avoid an f-string literally inserting
  the text `"None"` into an env var name. 2 new tests
  (`test_tf_var_env_empty_when_prefix_is_none`,
  `test_deploy_run_does_not_leak_tf_var_env_into_container_capable_steps`),
  plus a `TerraformIntegration.ENV_VAR_PREFIX == "TF_VAR_"` /
  `HelmIntegration`/`ComposeIntegration.ENV_VAR_PREFIX is None` assertion
  added to each class's existing contract test. Full check suite green:
  mypy 107 files, ruff clean, import-linter 1/0, pytest 1214 passed.

