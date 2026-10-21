# `helm lint` of the built chart is the container check phase

- Status: accepted
- Date: 2026-10-21
- Related: [docs/design/helm-chart-deployment.md](../design/helm-chart-deployment.md) (how it works),
  [docs/design/deploy-pipeline-stage-commands.md](../design/deploy-pipeline-stage-commands.md) (the `setup`/`check`/`plan`/`apply`
  split), [ADR-0022](0022-strata-build-run.md) (`build run` renders and never executes)

## Context and Problem Statement

Terraform steps have a check phase (`terraform validate`, run by `deploy check`, `plan` and `run`). A container-capable step
(Helm, Compose) was simply skipped by `deploy check`, and `deploy run` went straight to `helm upgrade`, so nothing validated the
chart that was about to deploy. v1's `HelmDeployer.check()` ran `helm lint` per local chart and failed the stage. haven lints its
source charts in its own CI.

What deploys is not the source chart: the module's `values.yaml` replaces the chart's own. In a haven run, a `configuration` typo
passed a lint of the source chart and failed the built chart with `nil pointer evaluating interface {}.repository`.

## Considered Options

- **Lint in `build run`.** Rejected: `build run` never executes anything ([ADR-0022](0022-strata-build-run.md)) and would start
  requiring the `helm` binary.
- **Leave it to each consumer's CI.** Rejected as the only answer: a source-chart lint cannot see what the module's configuration
  does to the chart, which is the failure that matters.
- **A Helm step in `deploy setup`.** Rejected: it reverses the rule that `setup` skips container steps, and `deploy apply` would
  still not run it.
- **A check phase for container steps**, linting the built chart. Chosen.

## Decision Outcome

Chosen: a new `InfraIntegration.check_namespace()` hook, implemented by Helm, run by `deploy check`, `deploy plan` and `deploy run`
(not `setup`, and not `apply`, matching Terraform's `validate`).

- It lints the built module directory with a temporary `-f` overlay of the resolved non-secret values; secret-shaped leaves stay as
  literal tokens, and an output not yet produced gets a placeholder, so a check needs no secret and no upstream infrastructure.
  Linting the built `values.yaml` with tokens left verbatim was considered and kept as the fallback, since it needs no resolution
  but would false-fail on a template that pipes a token through `int`.
- Only `[ERROR]` findings fail (lint exits non-zero on nothing else); info and warnings are ignored.
- Registry charts are skipped, because `helm lint` cannot lint a remote chart.
- **A lint error now fails `deploy run` before anything is deployed.** There is no opt-out, as Terraform's `validate` has none.
- An integration with no check phase (Compose) raises `CheckPhaseUnsupportedError` and is skipped; any other `IntegrationError`
  from a check is a failure, never a skip.

## Consequences

- Good: a chart that cannot render is caught before a deploy, including defects only the module's own configuration causes.
- Good: the same prefix model as Terraform, so `deploy check` is a meaningful CI gate for Helm too.
- Bad: a behaviour change for anyone running `deploy run` on a chart with a lint error that used to deploy. The evidence for haven
  is that all 19 local charts pass.
- Bad: Compose still has no check phase, which is asymmetric; a `docker stack config` check would be its own decision.
