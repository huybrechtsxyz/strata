# External CLI Timeouts (Terraform, Helm, Compose)

- Status: current
- Last updated: 2026-10-08

## Overview

Every external CLI subprocess `strata` runs (`terraform`, `helm`, `docker
compose`) has a timeout, applied by `src/strata/utils/transport.py`'s
`run_command()`. For Terraform's per-step execution
(`init`/`validate`/`plan`/`apply`), that timeout is configurable per
Deployment stage, with an environment variable escape hatch for a one-off
override — see [ADR-0032](../decisions/0032-deploy-time-step-timeout-overrides.md)
for the decision and why it's scoped the way it is. Helm/Compose remain
fixed at their hardcoded defaults (see "Other CLI integrations" below).

## Current Design

### Terraform (`src/strata/integrations/terraform.py`)

| Method                                                   | Default timeout | Notes                                                                                               |
| -------------------------------------------------------- | --------------- | --------------------------------------------------------------------------------------------------- |
| `version` (via `Integration.get_version()` in `base.py`) | 10s             |                                                                                                     |
| `init`                                                   | 300s (5 min)    | overridable — stage phase `setup`                                                                   |
| `validate`                                               | 60s             | overridable — stage phase `check`                                                                   |
| `plan`                                                   | 600s (10 min)   | overridable — stage phase `plan`; also backs v1's `plan_destroy`/`drift`, not separate methods      |
| `deploy` (`terraform apply`)                             | 1800s (30 min)  | overridable — stage phase `apply`                                                                   |
| `output`                                                 | 60s             | not overridable                                                                                     |
| `show`                                                   | 60s             | not overridable                                                                                     |
| `destroy`                                                | 1800s (30 min)  | schema field exists (phase `destroy`) but `deploy_run()` has no destroy call site yet — no consumer |

### Overriding a timeout

Two combined mechanisms (ADR-0032), both scoped to the four Terraform call
sites above:

1. **`DeploymentStageModel.timeouts`** (`src/strata/models/
   deployment_model.py`) — a `DeploymentStageTimeoutsModel` with optional
   `setup`/`check`/`plan`/`apply`/`destroy` fields, ported directly from
   v1's real `DeploymentStageTimeoutsModel`/`_get_timeout()`. Declared per
   stage, matched to an execution step by `stage.step == step.name`:

   ```yaml
   spec:
     stages:
       - step: apply_infra
         timeouts:
           setup: 120
           plan: 900
   ```

   Lives on the Deployment side, not the Workspace recipe
   (`ProvisioningStepModel`) — a Workspace's steps are reused across many
   Deployments/Environments (ADR-0011), and a timeout bump is usually an
   environment-specific need (e.g. production legitimately taking longer
   than dev for identical Terraform code), not a fact about what the step
   itself does. Same classification as this model's sibling
   `health_checks`/`secrets`/`namespace` fields.

2. **`STRATA_TIMEOUT_<STEP>_<PHASE>` environment variable** — step name and
   phase, upper-cased, non-alphanumerics replaced with `_` (e.g.
   `STRATA_TIMEOUT_APPLY_INFRA_SETUP`). Always wins over the schema field
   when set and parses as an integer — a one-off override for "this run
   only" with no document edit.

Both are computed by `_timeout_kwargs(step, stage, phase)` in
`deploy_controller.py`, returning `{"timeout": N}` or `{}` — never
`{"timeout": None}`, which would mean "wait forever" instead of "use the
method's own default" — spread with `**` into each `init`/`validate`/
`plan_or_warn`/`deploy` call.

### Other CLI integrations, for comparison (not overridable)

- **Helm** (`src/strata/integrations/helm.py`): `deploy_namespace`/
  `uninstall` use a Python `timeout=600`/`300` *in addition to* Helm's own
  `--timeout 5m` CLI flag (a separate `deploy_timeout: str` param baked
  into argv) — two independent timeout layers that can disagree.
  `lint`/`repo_update` = 60s. Not wired to either override mechanism:
  `deploy_namespace()` has no per-phase breakdown the way Terraform's
  discrete `init`/`validate`/`plan`/`deploy` methods do, so extending this
  design there needs its own shape decision — deliberately out of scope.
- **Compose** (`src/strata/integrations/compose.py`): `plan` (`stack
  config`) = 60s, `deploy`/`destroy` = 300s, `services` = 60s. Same
  no-per-phase-breakdown reasoning — out of scope.
- **Generic `Integration.run()`/`.request()`** (`src/strata/integrations/
  base.py`): base-class defaults are `run()` = 300s, `request()` = 30s —
  only matter when a subclass doesn't override.

### Enforcement mechanics (`src/strata/utils/transport.py`)

`run_command()` dispatches to one of two paths depending on whether a
`line_callback` was given:

- `_run_buffered()` (no streaming) — `subprocess.communicate(timeout=...)`,
  catches `TimeoutExpired`.
- `_run_streaming()` (the real deploy path, `line_callback` set) — polls
  reader-thread joins against a `time.monotonic()` deadline, since
  `communicate()` isn't in play while lines are being drained live.

Either way, on expiry the child is `.kill()`ed (never left orphaned) and the
result comes back as `CommandResult(returncode=124, timed_out=True)` —
`run_command()` never raises for a timeout, matching this module's general
"failure is an outcome, not an exception" contract. `CommandResult.
is_successful` checks both `returncode == 0` and `not timed_out`, since a
killed process can still report exit 0 on some platforms.

## History

- v1's real installed package (`xyz-strata` v1.11.2) has this exact shape
  already: `DeploymentStageTimeoutsModel` (`setup`/`check`/`plan`/`apply`/
  `destroy`) on `DeploymentStageModel.timeouts`, read via
  `base_deployer.py`'s `_get_timeout(step, default)`. v1 has **no**
  environment-variable override anywhere — that part is new, not a port.
  v1 separately has an unrelated top-level CLI `--timeout` flag
  (`run_deploy_command.py`) that watchdogs the *entire* deploy run via
  `concurrent.futures.TimeoutError` — a different mechanism (whole-run, not
  per-subprocess-call), not ported.
- First implementation built a brand-new `ProvisioningTimeoutsModel` on
  `ProvisioningStepModel` (the Workspace recipe side) without first
  checking whether `deployment_model.py` already had an equivalent field —
  it did (`DeploymentStageModel.timeouts`, above), already correctly scoped
  to the Deployment rather than the shared Workspace recipe. Caught before
  release; see ADR-0032 for why Deployment-scoping is correct. Lesson: grep
  the whole `src/strata/models/` tree for a v1 class/field name before
  adding a new one, not just the one model file that seems like the
  obvious home.
- No test coverage exists yet for the timeout-expiry path itself
  (`timed_out=True`, returncode 124) on any real integration — only
  `transport.py`'s unit-level behavior has been traced from source.
