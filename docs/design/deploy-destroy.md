# `strata deploy destroy` - Design

- Status: current
- Last updated: 2026-11-04

## Overview

`strata deploy destroy DEPLOYMENT` tears down what `deploy run` created: Terraform state, Helm releases, Compose
stacks and GitOps files, last step first. It is the v1 command of the same name, rebuilt on the v2 orchestrator.
`--dry-run` previews what would be removed and changes nothing; `--smoke-test` only reports which steps would be
removed, with no tool or output store contact; `--force` removes it. Exactly one of the three is required.

It is a variant of `deploy run`, not a second orchestrator: `deploy_run(..., destroy=True)` shares value resolution,
integration pre-flight, the `strata.auto.tfvars.json` rewrite and backend configuration with an apply, and only the
tail of each step differs. The original design lived in [deploy-command.md](deploy-command.md); the shape of
`deploy run` itself is described there.

## Current Design

### Command

```
strata deploy destroy DEPLOYMENT (--force | --dry-run | --smoke-test)
  [--path PATH] [--build-path PATH] [--stage NAME] [--scope LABEL] [--pin VERSION]
  [--output-max-age SECONDS] [--follow [console|ndjson]] [--change-* ...] [--output console|json]
```

- `--force` destroys for real and maps to Terraform's `-auto-approve`. It is the one place `deploy`'s `--force` is
  not inert. `--dry-run` previews. Zero, or more than one, of the three modes is a usage error (v1 only refused
  neither).
- `--dry-run` defaults `--follow` to `console`, like `deploy plan`, so the preview is visible.
- `--smoke-test` mirrors `deploy run --smoke-test`: values still resolve, then it reports "would destroy step X via
  Y" per step, last first, and stops. See D11.
- `destroy` cannot be combined with `stop_after` or `use_saved_plan`.
- Same `--path`, `--build-path`, `--stage`, `--scope`, `--pin`, `--output-max-age`, `--follow` and change-reference
  flags as `deploy run`, and the same exit codes: 0 success, 2 bad arguments or an unknown deployment, 3 a value
  that failed to resolve, a missing tool, a step that cannot be destroyed, or a failed destroy.
- Run `build run` first: a destroy reads what is already on disk and renders nothing.
- A filter that selects no step fails with `destroy_no_steps` (exit 3). `deploy run` only reports that no step
  matched, but a destroy that exits 0 would be audited as a successful teardown that touched nothing.
- `deploy_run()` itself also refuses a destroy that has none of `force`, `dry_run` and `smoke_test`, so a library
  caller cannot remove a Helm release or Compose stack without an explicit mode (only Terraform needs
  `-auto-approve`).

### Flow

```
deploy_run(destroy=True)
  |- image tag policy and security gates: skipped (D4)
  |- steps = reversed(ordered_by_depends_on(execution)), then --stage / --scope (D2)
  |- tool readiness, then _destroy_preflight(): unsupported steps, dependents left behind, ${output:} references
  |- for each step, last first
  |    |- container step (Helm, Compose): destroy_namespace() per namespace, reverse sorted order; no tokens
  |    |- --smoke-test: "would destroy step X", nothing else (D11)
  |    `- other step: tfvars rewrite, backend config, init, GitOps auth, then _destroy_step():
  |         --dry-run -> plan_destroy()    --force -> destroy(auto_approve=True)
  `- real run, fully successful, output store configured: clear the destroyed steps' recorded outputs (D8)
```

A destroy skips the image tag policy, the security gates, the `output.template` render, `validate`, the apply plan
and all output collection. It still runs value resolution, because Terraform evaluates every variable on a destroy.

### Decisions

The labels are stable: code comments cite them.

- **D1 - Shape.** A `destroy: bool = False` parameter on `deploy_run()`, the same pattern as `stop_after` and
  `use_saved_plan`. The divergent tail of a step lives in private helpers (`_destroy_step()`, `_destroy_preflight()`,
  `_step_payload()`), so the shared prelude is not copied and the function does not grow another large branch.
- **D2 - Ordering and selection.** Steps run in reverse dependency order, then `--stage` / `--scope` filter them.
  Within a container step, namespaces are destroyed in reverse sorted order. A destroyed step with a (transitive)
  dependent outside the selection gets a `destroy_leaves_dependents` warning naming those steps. It is never an
  error and never a flag: destroying an upstream step while its dependent stays strands the dependent, but strata
  cannot tell whether the dependent is still deployed, and a two-run teardown (`--scope apps`, then `--scope infra`,
  as `haven`'s CI splits them) must stay possible. Destroying a dependent and keeping its upstream is a normal
  partial teardown.
- **D3 - CLI.** As above. `--follow` defaults to `console` under `--dry-run`, and the shared option factory is reused
  unchanged: the default is applied in the command body.
- **D4 - What a destroy skips.** The image tag policy and the security gates would block the one operation that has
  to stay possible, so a `deny` policy or a failing CVE gate never stops a teardown. A destroy manifest carries no
  `policy_results`, because the policy file in the build directory belongs to the earlier deploy.
- **D5 - Unsupported tools.** An integration declares `SUPPORTS_DESTROY = False`, or, when the answer depends on the
  provisioner, overrides the `supports_destroy` property (the script integration: true only with `destroy_args`).
  A step that uses one is a pre-flight error naming the step and suggesting `--stage` / `--scope` to leave it out, before
  anything is destroyed; under `--dry-run` it is a warning. A step is never skipped silently: skipping would strand
  what it created. The flag is read from the integration, never from a tool name, so the orchestrator stays
  tool-agnostic.
- **D6 - Outputs.** Container steps (Helm, Compose) resolve no tokens on a destroy and never touch the output store:
  they need only a release or stack name. Every other step may reference `${output:STEP.KEY}` in its backend
  configuration or in a claimed dns, network or firewall payload, and neither can take a placeholder (a placeholder
  state key would point Terraform at an empty state, so a destroy could succeed having removed nothing; real modules
  validate their inputs). A destroy collects no outputs, so the durable output store is the only source.
  - The pre-flight scans every in-scope Terraform-style step for `${output:}`. A step that has any gets a
    `destroy_uses_outputs` warning naming the steps and keys and where they are read from. A reference with no
    usable record (no `output_store`, a missing record, a record past the trust window) is the error
    `destroy_output_unavailable`, raised before the first step is destroyed. The message says to configure an
    `output_store` and run `deploy run` once (a no-op apply still records outputs), or to pass `--output-max-age`
    when the record is stale.
  - The durable read is lazy, restricted to the upstream steps a step actually references, so a stale or unreachable
    record never blocks the teardown of a step that references nothing. `deploy run`'s eager merge would have, and
    its usual remedy (re-run the producing step) is not available when the aim is to remove it.
  - Collecting upstream outputs from live state instead (an `init` and `output -json` pre-pass) is not built. It
    would work without a store and is the only way to serve a sensitive output (which the store must not hold), but
    it needs `init` against upstream backends, including steps outside `--stage`, and a half-destroyed upstream may
    have lost outputs the store still holds. Build it when a real workspace uses `${output:}` in a destroyable step.
    No real consumer does today.
- **D7 - Previews (`--dry-run`).** Every tool that can show what a destroy would remove does, and one that cannot
  says so. Nothing is applied, and a destroy never writes or reads `<step>.tfplan`.

  | Tool      | Preview                                                                                                                                               |
  | --------- | ----------------------------------------------------------------------------------------------------------------------------------------------------- |
  | Terraform | `terraform plan -destroy`, no `-out`. Lists each resource "will be destroyed" and ends `Plan: 0 to add, 0 to change, N to destroy.`                   |
  | Helm      | `helm get manifest`, parsed into one `Kind/name` line per resource. A missing release is reported as "not installed, nothing to remove".              |
  | Compose   | The running services of the stack (`docker stack services`). Nothing tool-native previews a removal. An unreadable stack is a warning, not a failure. |
  | GitOps    | Whether the rendered file is present in the config repo checkout: "would remove `<file>`" or "not present". Never a diff.                             |
  | Script    | `would run: <destroy_args>`; the script is never executed. Without `destroy_args` the step is unsupported (D5).                                       |

  - *Contract.* `InfraIntegration.plan_destroy()` is not abstract and raises `IntegrationError` by default. It is a
    separate method, not `plan(destroy=True)`, because `plan(**kwargs)` swallows unknown keywords and GitOps would
    silently show its apply diff as a destroy preview. A step whose integration has no preview gets the warning
    `destroy_preview_unavailable` and the run goes on. Container steps preview through
    `destroy_namespace(dry_run=True)`.
  - *Helm is summarised, never printed.* A rendered manifest embeds Secret `data` in base64, which output redaction
    does not mask. `helm uninstall --dry-run` was rejected: it prints only `release "x" uninstalled`, with no
    resource list.
  - *Visibility.* The Terraform preview streams through `--follow` (and the existing secret redaction), which
    `--dry-run` turns on by default. The Helm, Compose and GitOps lines go through `on_step`, so they print without
    `--follow` and appear as progress events under `--follow ndjson`.
  - *Idempotence of a container destroy.* A retry after a partial failure re-runs steps that are already gone, so
    "already gone" must not fail. Helm: `uninstall` on a missing release exits 1 with `release: not found`; the code
    treats that stderr as success ("already uninstalled") and any other failure as an error. It does not use
    `--ignore-not-found`, because strata's Helm minimum is whatever the integration document declares and when the
    flag appeared was not verified. Docker: `docker stack rm` on a node that is not a swarm manager exits 1 and stays
    an error (a destroy pointed at the wrong `DOCKER_HOST`); on a swarm, a missing stack is not an error. `docker
    stack rm` defaults to `--detach=true`, so the integration passes `--detach=false`: a successful destroy means
    the tasks have stopped before the next step runs.
- **D8 - Output store.** After the whole run succeeded, and only for a real run, `DeploymentOutputStore.clear()`
  forgets the destroyed steps' records once. It is a read-modify-write that `put`s the record without those steps
  (an empty `{}` when none remain, because the store capability has `get` and `put` but no delete), and retries a
  version conflict on a fresh read so a concurrent writer's update is kept. Clearing once at the end, not per step,
  matters: on a retry after a mid-run failure, an already-destroyed dependent is re-run and still needs its
  upstream step's outputs. A failed clear is the warning `output_store_clear_failed` ("stale records may remain; a
  later deploy run could read them").
- **D9 - Audit.** `finalize_and_distribute_deploy_audit()` takes `action: "deploy" | "destroy"`, used for the
  manifest and the metrics dimensions. A real destroy is audited, including a failed one and one that stopped in
  pre-flight; a dry run writes nothing. The admitted sink event follows the action: `deployment.destroyed` instead
  of `deployment.completed`, so a sink must list it to receive it. `audit status` and `audit changes` show an ACTION
  column and an `action` field in their JSON, so a destroy never reads as a successful deploy.
- **D10 - Terraform call.** `destroy(auto_approve=force)`, as v1 does. There is no saved destroy plan. Within one
  `--force` invocation nothing is reviewed between a `plan -destroy -out` and the apply of that file, so
  `terraform destroy -auto-approve` is by definition "plan the destroy and apply it now". A saved plan would only add
  an approval gate across two invocations; no consumer calls `deploy destroy`, v1 has no such gate, and a re-plan
  on the second run is the safer behavior for something irreversible. If a pipeline ever wants one: `--dry-run`
  saves `<step>.destroy.tfplan` and a `--use-saved-plan` flag applies it, mirroring `deploy apply`.
- **D11 - Smoke test.** `--smoke-test` mirrors `deploy run --smoke-test`: the command stays zero-contact with every
  tool, so a pipeline can check a teardown's wiring before it has state access. Values still resolve first (a missing
  secret fails it, exactly as it does `deploy run --smoke-test`), then the offline pre-flight runs and each step is
  reported as "would destroy step X via Y", last first, and nothing else happens. It finds what a real run would
  refuse for reasons that need no contact: an unsupported step (an error, because a real destroy would stop there too,
  unlike `--dry-run`, where it is a warning), a filter that selects no step, and a `${output:}` reference nothing
  could serve (no `output_store`, or a step that is not upstream). It warns about dependents left behind. It does not
  read the output store, so whether a record exists and is fresh is only known by `--dry-run` or a real run; the
  `destroy_uses_outputs` warning says so. It never clears the store and writes no audit record.

### Integration contract

Additions to `InfraIntegration` (`integrations/capabilities.py`), all with defaults so existing integrations keep
working:

| Member                                                                 | Default                   | Implemented by                |
| ---------------------------------------------------------------------- | ------------------------- | ----------------------------- |
| `SUPPORTS_DESTROY: ClassVar[bool]`, `supports_destroy` property        | `True`                    | script: `destroy_args` is set |
| `plan_destroy(path, **kwargs) -> CommandResult`                        | raises `IntegrationError` | Terraform, GitOps             |
| `destroy_namespace(namespace, modules, *, dry_run, env, on_step, ...)` | raises `IntegrationError` | Helm, Compose                 |

A preview never changes anything and never writes a file. `destroy_namespace()` takes no `tokens`: a destroy reads
only release and stack names. `HelmIntegration.destroy()` and `ComposeIntegration.destroy()` take a `line_callback`
so `--follow` streams them; `get_manifest()` does not, because its stdout contains Secret data.

### Diagnostic codes

| Code                          | Severity                         | Meaning                                                                    |
| ----------------------------- | -------------------------------- | -------------------------------------------------------------------------- |
| `destroy_unsupported`         | error, warning under `--dry-run` | A step's integration cannot destroy (D5).                                  |
| `destroy_no_steps`            | error                            | `--stage` / `--scope` selects no step, so nothing would be destroyed (D2). |
| `destroy_leaves_dependents`   | warning                          | A destroyed step has dependents outside the selection (D2).                |
| `destroy_uses_outputs`        | warning                          | A step references `${output:}`; says where the values are read from (D6).  |
| `destroy_output_unavailable`  | error                            | A referenced output has no usable record (D6).                             |
| `destroy_preview_unavailable` | warning                          | The integration has no destroy preview; the run continues (D7).            |
| `output_store_clear_failed`   | warning                          | The record could not be cleared after a successful destroy (D8).           |

### Known limits

- A destroy covers what the workspace declares today. A step removed from the workspace since the deploy is not
  destroyed. v1 behaves the same.
- Step-level `enabled` does not exist in v2, so v1's "never gate a destroy on `enabled`" rule is a no-op. A disabled
  resource is ignored when a step's claims are computed, so a destroy never touches what only a disabled resource
  claims.
- Compose on a real swarm and the minimum Docker version that supports `--detach=false` were not run against a live
  swarm; the argv and the Docker CLI source were checked.
- Out of scope, each with its trigger: collecting upstream outputs from live state (D6); a saved destroy plan (D10);
  using a surviving output record to sharpen `destroy_leaves_dependents`; deployment locking.

## Related Decisions

- [ADR-0021](../decisions/0021-integration-layer.md) - `InfraIntegration.destroy()`.
- [ADR-0027](../decisions/0027-strata-deploy-run.md) - `deploy run`; destroy is a variant of it, so no new ADR.
- [ADR-0030](../decisions/0030-deploy-run-plan-preview-and-streaming.md) /
  [deploy-plan-preview.md](deploy-plan-preview.md) - the `--dry-run` and `--follow` behavior D7 builds on.
- [ADR-0032](../decisions/0032-deploy-time-step-timeout-overrides.md) - the `destroy` timeout phase, applied to the
  destroy itself; the preview uses the `plan` phase.
- [ADR-0036](../decisions/0036-cross-run-key-value-output-store.md) - the durable output store D6 and D8 use.
- [deploy-command.md](deploy-command.md) - `deploy run`, which this is a variant of.
- [deploy-pipeline-stage-commands.md](deploy-pipeline-stage-commands.md) - the `stop_after` / `use_saved_plan`
  precedent for D1.
- [audit-commands.md](audit-commands.md) - the ACTION column D9 adds.
- [ci-deploy-pipeline.md](../how-to/ci-deploy-pipeline.md) - tearing a deployment down from a pipeline.

## History

- The original design said destroy reuses every `deploy_run()` helper unchanged. There were none: the setup is
  inline in one 400-line function. A separate `destroy_run()` would have copied it or forced a refactor of the most
  heavily tested function in the repo, so destroy became a flag on `deploy_run()` (D1).
- The original design said a destroy needs only `init` plus `destroy`. Without the rewritten
  `strata.auto.tfvars.json`, `TF_VAR_strata_secrets` and the resolved backend config, `terraform destroy` fails on
  the undeclared `strata` variable or reads stale on-disk values. Only the `output.template` render and the
  plan and validate steps are skippable (D4).
- It also said `terraform destroy` has no saved-plan equivalent. `plan -destroy -out` followed by `apply` is
  standard; the decision not to use it stands for a different reason (D10).
- v1 runs destroy stages in declaration order and its own docstring admits that is wrong for a teardown; v2 reverses
  the dependency order. v1 refuses a destroy with neither `--force` nor `--dry-run`; "exactly one" is a v2 decision.
  Neither reference consumer's CI calls `deploy destroy`.
- A destroy dry-run that reused the apply path's plan call would have overwritten the reviewed `<step>.tfplan` with a
  destroy plan, and the next `deploy apply` would then have destroyed the infrastructure. Hence a destroy never
  writes or reads `<step>.tfplan` (D7, D10).
- Streaming Helm's `get manifest` (v1's own destroy preview step) would print Secret data, and `helm uninstall
  --dry-run` lists nothing, so Helm's preview is a parsed `Kind/name` summary (D7).
- Whether a tool can destroy was first thought detectable by tool name. The orchestrator is tool-agnostic
  (ADR-0022), so it is a class flag, the same device as `SUPPORTS_CAS` (D5).
- The audit read side and the sink event filter did not know a destroy: after one, `audit status` would have shown a
  successful deploy, and a sink listing only `deployment.destroyed` would have received nothing. A destroy manifest
  would also have claimed the earlier deploy's policy results, so it carries none (D4, D9).
- The `${output:}` question (D6) was settled by evidence, not assumption: no `${output:` appears in any YAML of the
  reference consumer repos that could be read, so the need is unproven, and the design fails early and precisely
  instead of building live collection on speculation. The `haven` consumer repo was not available, so its use is
  unchecked.
- Testing a GitOps destroy end to end exposed an older bug: `build run` never created `build/<step>` for a provisioner
  with no source, so `prepare()` crashed with a raw `FileNotFoundError` writing the GitOps sidecar. Every GitOps
  `build run` was affected, not only a destroy; `build run` now creates the directory.
