# Post-deploy verification is a phase of the deploy

- Status: accepted
- Date: 2026-11-22
- Related: [ADR-0067](0067-verify-checks-use-the-gates-semantics.md) (what a check's answer does),
  [ADR-0027](0027-strata-deploy-run.md) (`deploy run` and its phases),
  [ADR-0032](0032-deploy-time-step-timeout-overrides.md) (per-phase timeouts, which `verify` joins),
  [post-deploy-verification.md](../design/post-deploy-verification.md) (the design)

## Context and Problem Statement

strata checks a deployment before it changes anything: the image tag policy, the security gates, `strata
validate`. Nothing checks it afterwards. A web app was deployed whose `apply` succeeded, so strata reported
`success`, but the app never started: a configuration setting was missing. Terraform reports success when the
cloud API accepted the resource, not when the workload in it runs. Helm with `--wait` waits for Kubernetes
readiness and Compose can wait for health, but neither knows the application-level answer ("does the site
answer 200"), and Terraform has no such wait at all.

Where should the check that the deployed workload works live?

## Considered Options

- **A phase of the deploy.** `deploy run` and `deploy apply` run checks declared on the deployment: a stage's
  checks right after its step, the deployment's own (`spec.verify`) once after every step. A failed check fails the
  run. A stand-alone `deploy verify` runs the same checks later, changing nothing.
- **The pipeline's job.** A pipeline step after `deploy run` runs whatever scripts it likes; strata does nothing.
- **The tools' own readiness.** Rely on Helm `--wait`, Compose `--detach=false` and similar waits.
- **Only a stand-alone `deploy verify`**, never run by the deploy itself.

## Decision Outcome

Chosen: **a phase of the deploy, plus the stand-alone `deploy verify`**, because only the deploy can make its own
outcome mean "the deployment works": the run's status, its exit code and its audit manifest then say whether the
workload runs, not only whether the tools accepted the change.

- **Where the checks run.** A stage's `verify` runs after its step's apply, before the next step starts, so a later
  step builds on a service known to run and the manifest names the step whose workload failed. `spec.verify` runs
  once after every step, for an end-to-end check; it is skipped when `--stage`/`--scope` leaves a step out.
- **What the checks can use.** The run's resolved values and secrets, the outputs of the steps it just applied
  (`${output:STEP.KEY}`), and the run's secret masking: what a pipeline step after the deploy would have to
  rebuild.
- **Waiting.** Each check is retried with Kubernetes startup-probe timing (an initial delay, then a fixed period,
  up to a failure threshold), so a workload that takes minutes to start is the normal case, not a failure.
- **Opting out.** `verify.enabled: false` on a block, `enabled: false` on a check, and `--no-verify` for one run (a
  pipeline that verifies in a later stage with `deploy verify`).
- **Recording.** The manifest records each check's answer, per stage and for the deployment, and `verified: true |
  false`, left out when no check ran, so "not checked" is never read as "verified".
- **No rollback.** A failed check leaves the change applied, and the run is `partial`. strata cannot undo a
  Terraform apply.
- **`deploy verify`** runs every selected block against what is deployed now. It waits no initial delay and reads
  `${output:}` from the workspace's `output_store`. It writes no manifest and exits 3 when a check ends in an
  error.

The pipeline-only option leaves every deployment to reinvent the same retries, token resolution and masking, and
the deploy's record still says `success` for a workload that does not run. The tools' readiness waits stay useful
and are a later, complementary phase, but they cannot express an application-level check and do not exist for
Terraform. A stand-alone command alone gives no gate between two steps and no verdict in the deploy's own record.

### Consequences

- Good, because a deploy that leaves a broken workload fails, with the check's own message, instead of reporting
  `success`.
- Good, because the checks are declared with the deployment, merge through `extends` like the rest of it (by check
  `name`), and are validated by `strata validate` (their tokens, and which step outputs they may read).
- Bad, because a deploy takes longer: with the defaults, a workload that never comes up holds the run for about
  five minutes before the check fails. `failure_threshold`, `period` and `timeouts.verify` shorten it.
- Bad, because a failed check after a successful apply leaves a `partial` deployment that someone must fix and
  deploy again; there is no automatic way back.
