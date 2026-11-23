# Post-Deploy Verification — Design

- Status: current
- Last updated: 2026-11-23

## Overview

strata checks a great deal **before** a deploy: validation, the integration preflight, `image_tag_policy`,
`cve_policy`, `checkov_policy`, the release and image gates. Verification is the check **after** it: does what was
deployed work? A tool's success means "the platform accepted the change", not "the workload runs". The case that
prompted the feature: a web app was deployed, `apply` succeeded, and the app never started because a configuration
setting was missing. The audit record said `success` for a deployment that was down.

A deployment declares **checks**. They run after a step is applied (or destroyed), wait for the workload to come
up, and decide whether the run succeeded. Their answers go into the audit trail like everything else the run did.
`strata deploy verify` runs the same checks on their own, against what is deployed now.

The user-facing references are [deployment.md](../config/deployment.md) ("Verifying a deploy") and the how-to
[verify-a-deploy.md](../how-to/verify-a-deploy.md). The section labels D1 to D12 below are cited from the code.

## Current Design

### What each tool guarantees when its deploy returns

| Tool      | What "success" means                                                                                                                                                                                                                |
| --------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Terraform | The provider's API accepted every change. Nothing about whether the workload runs.                                                                                                                                                  |
| Helm      | The release's resources became ready: `--wait=watcher --rollback-on-failure` (Helm 4) or `--wait --atomic` (Helm 3), rolled back if not ([helm-chart-deployment.md](helm-chart-deployment.md)). `type: helm` adds the chart's tests (D11). |
| Compose   | Every service converged: `docker stack deploy --detach=false` (D11).                                                                                                                                                              |
| GitOps    | The file is committed and pushed. The controller's sync and health are not read (D11).                                                                                                                                             |
| Script    | The script's exit code.                                                                                                                                                                                                             |

A check is what turns "accepted" into "works" for the first, fourth and fifth rows, and adds an application-level
answer to the others.

### Standards it follows

| Standard                                                     | Taken here                                                                                     |
| ------------------------------------------------------------ | ---------------------------------------------------------------------------------------------- |
| **Kubernetes probes** (startup, readiness, liveness)         | the timing fields, their names and their meaning; the startup-probe pattern for a slow start  |
| **Docker `HEALTHCHECK`**                                     | confirms the same knobs; `start_period` is the initial grace                                   |
| **Monitoring Plugins** (Nagios, Icinga, Naemon)              | the opt-in exit-code contract (0 OK, 1 WARNING, 2 CRITICAL, 3 UNKNOWN) and first-line message  |
| **Argo Rollouts analysis** (Successful, Failed, Inconclusive) | "could not tell" is its own outcome                                                            |
| **Helm test hooks**                                          | `type: helm` runs them                                                                         |
| **IETF health check response** (`application/health+json`)  | an `http` check reads its `pass`/`warn`/`fail`                                                 |

Exit `0` means pass everywhere; a `warn` between pass and fail is common; "unknown" is distinct from a failure in
the tools built for judging; and every tool separates an initial grace period, an interval, a per-attempt timeout
and a failure threshold.

The name is **verify**, its units are **checks**. `--smoke-test` already existed and means the opposite: a
zero-contact report of which steps *would* run ([deploy-plan-preview.md](deploy-plan-preview.md)).

### D1. Answers and reactions: the gates' semantics

A check answers **pass, warn, fail or unknown**. What the run does with the answer uses the vocabulary every gate
already has ([security-gates.md](security-gates.md)), so a user who knows `cve_policy` knows this:

| Answer  | Reaction                                                                                                                                        |
| ------- | ----------------------------------------------------------------------------------------------------------------------------------------------- |
| pass    | an info line                                                                                                                                    |
| warn    | a warning, always (degraded, but up); only a script with `exit_codes: monitoring` or an `http` check's `health+json` body answers it          |
| fail    | `enforcement: deny` (default): an error, the stage fails; `enforcement: warn`: a warning                                                       |
| unknown | `on_unknown: block` (default): an error; `warn`: a warning; `skip`: an info line, like a gate's `on_missing_data`                              |

The words are shared, the record is not: a check's result is a check record in the manifest (D9), not a
`policy_results` entry, because it judges the running deployment, not the documents.

### D2. Where checks are declared

- **`stages[].verify`**: checks for one step, run right after that step's apply (or destroy). A later step builds
  on a service known to run, and the manifest says which step's workload failed.
- **`spec.verify`**: checks for the deployment as a whole (an end-to-end request through the front door, a smoke
  transaction), run once after every step.

Both have the same shape: `enabled`, the timing fields (D4), `enforcement`, `on_unknown` and `checks`. Through
`extends` the blocks deep-merge and `checks` merge by `name`, on the spec and inside a stage, so a base declares the
checks every customer deployment shares and a child changes one, or turns it off with `enabled: false` on the
check, without repeating the others.

A child names only what it changes: `{name: web, enabled: false}` or `{name: web, timeout: 60}`, its `type` and
other fields coming from the base's check of the same name. Two things make that work:

- **Each document of the chain is dumped as written** (`model_dump(exclude_unset=True)` in
  `deployment_resolution.py`, as for every deployment field), so the child's defaults (`type: script`,
  `method: GET`, `initial_delay: 10`) never overwrite what the base set. Validating the merged dump fills the
  defaults in again.
- **A check is judged as part of a deployment**, not on its own (`DeploymentVerifyCheckModel.problems()`, called by
  `DeploymentSpecModel.validate_verify_checks()`): a document with `extends` is exempt, like its missing
  `workspace`, and the merged deployment is judged in full. A disabled check needs no `run`, `url` or `host`.

```yaml
spec:
  stages:
    - step: apply_web
      verify:
        initial_delay: 30            # the web app needs time to start
        checks:
          - name: web-answers
            type: http
            url: "https://${output:apply_web.default_hostname}/health"
  verify:                            # after every step
    enforcement: warn                # report only, while the check is new
    checks:
      - name: checkout-flow
        run: [python, checks/checkout_flow.py]
        env:
          API_KEY: ${secret:SMOKE_API_KEY}
```

### D3. When checks run

| Command                       | Stage checks                                                 | Deployment checks                                                                         |
| ----------------------------- | ------------------------------------------------------------ | ----------------------------------------------------------------------------------------- |
| `deploy run`, `deploy apply`  | after each step's apply                                      | after all steps, when every step succeeded and none was left out by `--stage`/`--scope`   |
| `deploy destroy --force`      | after each step's destroy, the checks with `when: [destroy]` | after all steps, the checks with `when: [destroy]`, unless `--stage`/`--scope` left one out |
| `deploy verify DEPLOYMENT`    | all selected, against what is deployed now                   | all, unless `--stage`/`--scope` leaves a step out                                         |
| `--dry-run`, `--smoke-test`, `deploy plan` | not run; named as "would verify step 'X': check 'Y'" | same                                                                                 |
| `deploy setup`, `deploy check` | not run, not named                                          | same                                                                                      |

- **On by default when checks are declared.** `verify.enabled: false` turns a block off; `--no-verify` on
  `run`/`apply`/`destroy` skips every check for one run (a pipeline that verifies in its own later stage), and the
  manifest then says the run was not verified.
- **Deployment checks are skipped** when a step failed or a stage check ended in an error (the run stopped before
  them), and when `--stage`/`--scope` left a step out, with an info line (`verify_deployment_skipped`) pointing at
  `deploy verify`: an end-to-end check needs the whole deployment.
- **`deploy verify`** takes `--stage`, `--scope`, `--pin`, `--output-max-age`, `--follow` and `--output json` (a
  `VerifyReport` with the same check records as the manifest). It writes no deploy manifest, since it changed
  nothing, and exits `0` when nothing failed, `3` when a check failed or was unknown with `block` (strata's "not
  ready" code, as in `integration check`), `2` for bad arguments, `1` for a system failure. It waits no
  `initial_delay` (nothing was just started) but keeps the retries, runs only the `apply` checks, and runs every
  block even after one failed. Its `${output:}` values are read from the `output_store`, only for the steps the
  checks name.
- **Order within a step:** verification runs after the step's outputs are recorded, so a check can use this step's
  `${output:}`; the next step starts only after it passed.

### D4. Waiting: the Kubernetes probe model

Each attempt runs the check once. The timing fields, set on a block and overridable per check, are Kubernetes' in
snake_case, in seconds:

| Field               | Meaning                                                      | Default | Kubernetes default |
| ------------------- | ------------------------------------------------------------ | ------- | ------------------ |
| `initial_delay`     | wait before the first attempt (start, initialise, migrate)   | **10**  | 0                  |
| `period`            | seconds from one attempt's start to the next                 | **10**  | 10                 |
| `timeout`           | one attempt's limit; a script is killed after it             | **30**  | 1                  |
| `success_threshold` | consecutive passes needed                                    | **1**   | 1                  |
| `failure_threshold` | consecutive fails or unknowns before giving up               | **30**  | 3                  |

- The defaults wait like a Kubernetes *startup* probe: a deploy just started the workload, so a slow start is the
  normal case. About five minutes for a web app to come up before the check fails. A 1-second `timeout` suits an
  in-cluster probe, not a script calling a cloud API, hence 30.
- `fail` and `unknown` are retried; `pass` counts towards `success_threshold`; `warn` ends the attempts at once (a
  degraded answer is an answer). After `failure_threshold` attempts the last answer stands. An answer that cannot
  change ends the attempts, a pass included (a script that cannot start, a token that does not resolve,
  `helm test`).
- A check whose answer keeps flipping reaches neither threshold, since each pass resets the fail count and each
  fail the pass count. Its attempts are capped at `failure_threshold + success_threshold - 1`, the most a check
  needs that fails up to the limit and then passes enough times. Reaching the cap answers fail ("the answer keeps
  changing", with the last answer). Without the cap such a check would never end: `spec.verify` has no phase
  timeout by default.
- Several checks in a block run one after the other, each with its own attempts. The block's `initial_delay` is
  waited once, before the first; a check's own `initial_delay` before that check.
- `stage.timeouts.verify` bounds the whole phase, like the other phases. Its default is what the fields allow:
  `initial_delay + failure_threshold × period` plus the last attempt's `timeout`.

### D5. The `script` check

```yaml
- name: web-answers
  type: script                                 # the default
  run: [pwsh, -File, checks/http-ok.ps1, "${output:apply_web.default_hostname}"]
  env: {TOKEN: "${secret:HEALTH_TOKEN}"}
  when: [apply]                                # apply (default), destroy, or both
  exit_codes: binary                           # binary (default) or monitoring
  timeout: 60                                  # any D4 field may be set per check
```

- **`run`** is an argument list, never a shell string: no quoting rules differ between Windows and Linux, and no
  token can inject a command. `run[0]` is found on `PATH`, or is a path; a relative path is resolved against the
  solution root, which is also the working directory.
- **Tokens** in `run` and `env` resolve as in a script provisioner's `args`: `${var:}`, `${feature:}`, `${secret:}`,
  `${deployment:}`, `${artifact:}`, and `${output:STEP.KEY}` for the stage's step and the steps it depends on
  (directly or through others), from this run or the `output_store`. A deployment check may read any step.
- **Environment:** the runner's; then the deployment's target contexts (`verify_environment()`: what the Helm and
  Compose integrations set for their steps, `target_context_env()`, so `HELM_KUBECONTEXT` and `DOCKER_CONTEXT`,
  plus `STRATA_KUBE_CONTEXT` and `STRATA_DOCKER_CONTEXT`), so `kubectl`, `helm` and `docker` reach the cluster the
  steps deployed to, even when no step of the deployment uses Helm or Compose; then `env`; then `STRATA_DEPLOYMENT`, `STRATA_STEP` (empty for a deployment
  check), `STRATA_CHECK`, `STRATA_ACTION` (`apply`, `destroy`, or `verify` under `deploy verify`) and
  `STRATA_ATTEMPT` (1-based). `${deployment:kube_context}` and `${deployment:docker_context}` pass the same
  contexts as arguments ([deployment-tokens.md](deployment-tokens.md)).
- **Exit codes:**

  | Exit                                        | `binary` (default) | `monitoring`  |
  | ------------------------------------------- | ------------------ | ------------- |
  | 0                                           | pass               | pass          |
  | 1                                           | fail               | warn          |
  | 2                                           | fail               | fail          |
  | 3                                           | fail               | unknown       |
  | any other                                   | fail               | fail          |
  | killed by `timeout`                         | unknown, retried   | same          |
  | could not start (not found, not executable) | unknown, not retried | same        |

- **Output:** the first non-empty line of stdout (else stderr) is the check's message, shown with the answer and
  recorded in the manifest; the rest goes to the console with `--follow`. All of it is masked with the run's secret
  values before it is shown or recorded.

### D6. Destroy

A check with `when: [destroy]` (or both) runs after its step's destroy, with `STRATA_ACTION=destroy`. The script
decides what "gone" means (the site no longer answers, the resource group is empty, DNS no longer resolves) and
exits `0` when it is; an `http` or `tcp` check uses `expect: down`. A destroy whose checks fail is `partial`, like
any failed run that changed something, and the finding says "The step was destroyed, but the check says what it
removed is not gone".

A destroy check runs when what it addresses is gone, so its `${output:}` values (the destroyed app's host name) come
from the `output_store`, read before the first step is destroyed. The records the destroy's own preflight already
read are not read again, and only enabled blocks are read for. A check never stops a destroy before it starts:
without a store (`verify_outputs_unavailable`) or with a record that is stale or unreadable
(`verify_outputs_unusable`, a warning here and an error under `deploy verify`), the check that reads it answers
unknown, and its `on_unknown` decides. `--no-verify` works on `deploy destroy` too.

### D7. What the run does next

- A check that ends in an **error** (fail with `deny`, unknown with `block`) fails its stage: the run stops, as for
  any failed step, and is `partial`, since the change is applied. A failed deployment check fails the run, not a
  stage.
- **No rollback.** strata cannot undo a Terraform apply; Helm rolls back on its own readiness failure.
- A **warning** never fails the run; it is in the console, the manifest and the event's findings.
- The error's hint carries the check's message and what to do: fix the workload, or set `enforcement: warn` /
  `on_unknown: warn` while the check is being introduced.

### D8. Secrets

`${secret:}` resolves in `run`, `env`, an `http` check's `url`, `headers` and `body`, and a `tcp` check's `host`. It
is resolved at deploy and never written anywhere. `env` is preferred over `run`: an argument is visible in the
process list. Every secret value is masked in the console and the manifest, and a check's message and URL are
masked before they are recorded.

### D9. Recording

- **Manifest, per stage:** a `verify` phase in `stages[].steps[]` (success, duration) and `stages[].checks[]`
  (`ManifestCheckModel`: name, answer, attempts, duration, message).
- **Manifest, deployment:** `checks[]` with the same fields for `spec.verify`, and `verified`: `true`, `false` when
  any check that ran failed or was unknown (whatever its `enforcement` made of it), left out when no check ran, so
  a reader can tell "verified" from "not checked".
- **Metrics:** `verification_state()` turns `verified` into one bounded word, `verified`, `verification_failed` or
  `not_verified`, used by everything below so they cannot disagree. The metrics record carries it as
  `dimensions.verification`, with `measures.checks_total` and `checks_failed` (left out when no check ran).
  `error_category: verification` marks a run whose every error is a check answer (`verify_check_failed`,
  `verify_check_unknown`): every step applied, the workload does not work. A check that could not be set up
  (`verify_outputs_unavailable`) is not one.
- **Events:** both carry `labels.verification`, so a SIEM rule tells a deploy that worked from one that only
  applied.
- **Handover document:** the audit summary in the `output_store` records `verification`, and the built-in template
  says "Verified" or "Not verified" with why. A summary stored by an earlier strata says nothing.

### D10. Built-in check types

| Type     | Answer                                                                                                                                                       | Fields                                                                                                                  |
| -------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------- |
| `script` | exit code (D5)                                                                                                                                               | `run`, `env`, `exit_codes`                                                                                              |
| `http`   | a status in `expect_status` (default `200`, one or a non-empty list, each 100-599) passes; an `application/health+json` body's `status` decides when there is one; no answer fails | `url`, `method` (GET, HEAD, OPTIONS, POST with `body`), `headers`, `ca_file` or `insecure`, `expect: up \| down`      |
| `tcp`    | a connection passes                                                                                                                                          | `host`, `port`, `expect: up \| down`                                                                                   |
| `helm`   | `helm test`'s exit code (D11)                                                                                                                                | `release`, `namespace`, `tests`, `env`                                                                                  |

- A field of another type is refused, judged by value since a resolved deployment is dumped with its defaults.
  `token_fields()` lists every field whose tokens resolve, for the runner, `output_steps()` and `strata validate`.
- **`http`** follows redirects and reads the body up to 64 KiB for `application/health+json` (`pass`/`ok`/`up`,
  `warn`, `fail`/`error`/`down`, with its `output` in the message). Under `expect: down` no answer passes, and a
  status in `expect_status` fails ("it still answers"). A URL that is not `http(s)://` is unknown and not retried.
- **TLS:** the system's trust store by default; `ca_file` trusts only that PEM bundle's authorities (relative to
  the solution root; the host name is still checked); `insecure` checks nothing and the message says so. The two
  exclude each other. A certificate that is not trusted fails, also under `expect: down` (something still answers),
  with a message pointing at `ca_file`. A `ca_file` that cannot be read is unknown and not retried.
- PUT, PATCH and DELETE are not offered: a check must not change what it checks.

### D11. Tool-native waiting

- **Compose:** `docker stack deploy --detach=false`, so a step succeeds once its services converged, as
  `docker stack rm` already waited for removal. Unconditional, like Helm's `--wait`; a stack that never converges
  runs into `timeouts.apply`.
- **Helm:** `type: helm` runs `helm test RELEASE --namespace NS --timeout Ns [--filter name=TEST ...]` in the verify
  phase, with the deployment's kube context, so the chart's tests get the checks' semantics (`enforcement`,
  `--no-verify`, `deploy verify`, the manifest) instead of failing the apply. Opt-in: running every chart's test
  hooks unasked would start pods nobody chose to run. Never retried (Helm waited for readiness, and each attempt
  starts the test pods again); refused with `when: [destroy]`. The process gets 15 seconds past `--timeout` to
  report.
- **GitOps:** no built-in check. Reading an Argo CD Application's or a Flux Kustomization's status needs cluster
  credentials, which the GitOps integration never holds: it only pushes a file
  ([gitops-integration.md](gitops-integration.md)). A `script` check does it with the deployment's context:
  `[kubectl, --context, "${deployment:kube_context}", -n, flux-system, wait, kustomization/apps,
  --for=condition=Ready]`, or a script that waits until the controller applied the pushed `commit_sha` (the how-to,
  section 10).

### D12. What `strata validate` checks

Everything a check needs that is known before the deploy is judged by `strata validate`, so a gap shows up before a
step is applied instead of as an unknown check after it (`semantic_checks.py`):

- **Tokens.** Undeclared and malformed tokens are reported as in any other field. `${output:STEP.KEY}` is refused
  elsewhere in a deployment, but in a check it is judged by `_check_deployment_verify_outputs()`: in a stage check
  `STEP` must be the stage's step or one it depends on, directly or through others (the outputs `deploy run`
  guarantees it), in a deployment check any workspace step, and the reference must name a key. Anything else is
  `verify_output_unavailable`, with a hint listing what is available. `${deployment:kube_context}` or
  `docker_context` on a deployment that sets no such context is `unavailable_deployment_token`.
- **Files of the solution** (`_check_deployment_verify_files()`):
  - the script a `script` check runs: `run[0]` when it is a path, and the script an interpreter runs (the value
    after `-File` for `pwsh`/`powershell`; the first argument that is neither an option nor an option's value
    (`-o pipefail`, `-W ignore`) for `bash`, `sh`, `zsh`, `python`, `python3`, `py` and `node`, unless an inline
    program comes first: `-c`, `-m`, `-e`, `-Command`). Such an argument is judged only when it looks like a file
    (a directory part or an extension), so the value of an option strata does not know is not reported. One
    missing is `verify_script_missing`, located at the argument;
  - an `http` check's `ca_file`, loaded as the check will load it: missing, or holding no certificate, is
    `verify_ca_file_unusable`.

  Only relative paths without tokens are judged. A bare `run[0]` is found on `PATH`, and an absolute path (Linux
  or Windows style) names a file on the machine that deploys; neither need exist where `validate` runs.

A disabled check or block is judged by none of these (`enabled: false` is how a child drops a base's check), nor is
a partial base: the deployments that extend it are. That includes the token checks every field gets
(`undeclared_value_token`, `unavailable_deployment_token`, `unknown_artifact_token`, ...): they read the merged
deployment, and `_without_disabled_verify_checks()` drops their findings inside a disabled block or check. A child
that sets no `kube_context` can therefore turn off a base's `kubectl --context "${deployment:kube_context}"`
check. Which steps a check may read comes from
`dependency_order.upstream_of()`, the walk `deploy run` uses to give a step its upstream outputs, so the two
cannot disagree.

### Not built

Each of these is a decided boundary, to revisit when a real need shows up:

- **Parallel checks** within a block: checks run one after the other.
- **`on_failure: continue | rollback`** on a stage stays accepted and unused; a failed check stops the run like a
  failed step.
- **Scheduled verification** is the pipeline's job: a scheduled pipeline that calls `deploy verify`.
- **Check scripts from another repository:** a check's files are the solution's; a `path` remote's checkout is
  not searched.
- **A built-in GitOps check** (D11): needs the object's location, cluster credentials for strata, and a real
  controller to build it against.

## Related Decisions

- [ADR-0066](../decisions/0066-post-deploy-verification-is-a-deploy-phase.md) — verification is a phase of the
  deploy, plus the stand-alone `deploy verify` (D2, D3).
- [ADR-0067](../decisions/0067-verify-checks-use-the-gates-semantics.md) — a check answers pass, warn, fail or
  unknown and reacts with the gates' `enforcement`/`on_unknown` (D1).
- [ADR-0068](../decisions/0068-remove-health-checks-in-2-9.md) — `health_checks` removed in 2.9, the release that
  adds `http` and `tcp` checks.
- [ADR-0027](../decisions/0027-strata-deploy-run.md) — `deploy run`, its phases and `--smoke-test`.
- [ADR-0032](../decisions/0032-deploy-time-step-timeout-overrides.md) — per-phase step timeouts, which `verify`
  joins.
- [ADR-0006](../decisions/0006-context-shared-stage-runtime-store.md) — why v1's `output_key` was not ported; step
  outputs have since become readable at deploy time, which checks rely on.

## History

- **Script exit codes are `binary` by default.** The Monitoring Plugins contract was the first proposal, but under
  it the `1` that Kubernetes exec probes, `curl -f`, `test` and most CI steps exit with on failure reads as a
  warning and lets a broken deployment pass. The four codes are the opt-in `exit_codes: monitoring`, for an
  existing Nagios or Icinga plugin.
- **Startup-probe defaults (about five minutes)** rather than Kubernetes' liveness defaults: a deploy has just
  started the workload, and the web app that prompted the feature needed minutes to come up.
- **Plain pass/fail probes were rejected** for the gates' four answers: they cannot express "report, don't block"
  (a new check, a non-critical endpoint) or "the check itself is broken", both of which the gates had needed and
  solved.
- **`when`, not `on`:** PyYAML reads YAML 1.1, where an `on:` key loads as the boolean `true` (the trap GitHub
  Actions workflows are known for).
- **`health_checks` was removed, not deprecated** (ADR-0068). It was accepted and validated but never ran, so
  removing it changes no behaviour. It was not converted automatically into `verify.checks`, since that would start
  probes nobody had seen run; a document that sets it fails to load with the entries to write.
- **`type: helm` is opt-in and never retried**, rather than running every chart's test hooks after each upgrade:
  test pods are workloads someone must choose to run, and each retry starts them again after Helm already waited
  for readiness.
- **GitOps health is a script check, not built in:** the GitOps integration deliberately holds no cluster
  credentials, and no real controller was available to build against. The `${deployment:kube_context}` token was
  added so such a check needs no script.
- **`${output:}` was first refused in checks by `strata validate`**, which treated it like any deployment field, so
  the intended use failed validation. Judging it against the stage's step and its upstream steps fixed that; on the
  way, `Diagnostics.extend(source=...)` was found to drop every finding's `hint` and was fixed.
- **Dropping a base's check did not work at first:** the child document was validated on its own, so
  `{name: web, enabled: false}` was refused for lacking `run`. Had it loaded, its dumped defaults (`type: script`)
  would have overwritten a base `http` check in the merge. Both were found while making `validate` skip disabled
  checks. The rules moved to the deployment. The dump was first narrowed on the verify models alone; an audit of
  every deployment field then found the same overwrite for `locking.enabled`/`strategy` and a stage's `enabled`
  and `on_failure`, so the chain dump itself now writes only what each document set.
- **The attempt cap came from a review**, not from the probe model: Kubernetes resets its counts the same way, but a
  probe runs forever by design, while a check must end. Hitting it answers fail rather than unknown: the check did
  answer, just not the same way twice in a row.
- **A destroy is not stopped by a check's inputs:** reading a stale record for a destroy check first stopped the
  whole destroy before any step ran, stricter than the destroy's own preflight. The check answering unknown keeps
  the decision with `on_unknown`, where a check's other failures already are.
- **`validate` judges only relative, token-free paths:** the solution root is where checks run, so its files are
  the ones `validate` can see. An absolute path or a bare command names the deploying machine, which may not be the
  machine that validates. A `/`, even inside an argument of another program, is not taken as a path
  (`kubectl wait kustomization/apps`): only `run[0]` and an interpreter's script are.
