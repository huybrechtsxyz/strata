# Configurable Deploy-Time Step Timeouts, Scoped to the Deployment

- Status: accepted
- Date: 2026-10-08
- Related: [ADR-0011](0011-topology-and-provisioning-decoupling.md) (Workspace/
  Deployment decoupling — the reason a Workspace recipe is reused across many
  Deployments, which this decision's placement choice depends on),
  [ADR-0027](0027-strata-deploy-run.md) (`deploy run`'s execution model),
  [docs/design/cli-timeout.md](../design/cli-timeout.md) (full inventory,
  v1 precedent evidence, and how the mechanism works today)

## Context and Problem Statement

Every external CLI subprocess `deploy run` invokes for a Terraform step
(`init`/`validate`/`plan`/`apply`) runs with a hardcoded Python timeout
(`TerraformIntegration`'s own method defaults) with no way to change it short
of editing source — confirmed by inventory, no CLI flag, config field, or
environment variable fed a timeout value into any of these calls. A real
`terraform apply` that legitimately runs longer than the hardcoded 30-minute
default (large cloud deployments aren't unusual) has no way to get more time.

## Considered Options

- **A new per-step schema field on `ProvisioningStepModel`** (the Workspace
  recipe side). Rejected after initial implementation: a Workspace's
  `execution` steps are deliberately reusable across many Deployments/
  Environments (ADR-0011) — a timeout override baked into the shared recipe
  would force every Deployment of that Workspace to inherit it, even though
  a timeout bump is usually an environment-specific need (e.g. production
  scale legitimately taking longer than dev for identical Terraform code),
  not a fact about what the step itself does.
- **A new field on the Deployment side, invented fresh.** Rejected once
  found unnecessary: `strata/models/deployment_model.py` already has
  `DeploymentStageModel.timeouts` (a `DeploymentStageTimeoutsModel` with
  `setup`/`check`/`plan`/`apply`/`destroy`, directly ported from v1's real
  `DeploymentStageTimeoutsModel`/`_get_timeout()`), sitting alongside other
  deploy-time runtime knobs (`health_checks`, `secrets`, `namespace`) on the
  same per-step, per-deployment model — unconsumed until this decision, but
  exactly the right shape and scope already.
- **A global CLI `--timeout` flag**, matching v1's real
  `run_deploy_command.py --timeout`. Rejected as the sole mechanism — that
  flag watchdogs the *entire deploy run* via a wall-clock deadline, a
  different mechanism (whole-run, not per-subprocess-call) solving a
  different problem; not ported here.
- **Schema field only, no environment variable escape hatch.** Rejected —
  a one-off, "just this run needs more time" bump (e.g. a transient slow
  cloud API) would otherwise require an actual document edit and commit,
  which is disproportionate for a single occasional run.
- **Schema field (`DeploymentStageModel.timeouts`) plus an environment
  variable override** (chosen) — the schema field covers a durable,
  reviewed, environment-specific timeout need; the environment variable
  covers the "just this run, right now" case with no document edit at all.

## Decision Outcome

Chosen: wire the existing, previously-unconsumed `DeploymentStageModel.
timeouts` into `deploy_controller.py`'s Terraform call sites
(`init`/`validate`/`plan_or_warn`/`deploy`), matched to each execution step
by `stage.step == step.name`, using that model's own field names (`setup`/
`check`/`plan`/`apply`/`destroy` — not v2's own method names, to avoid
inventing a second vocabulary for the same five phases). Add a
`STRATA_TIMEOUT_<STEP>_<PHASE>` environment variable (step name and phase,
upper-cased, non-alphanumerics replaced with `_`) that always wins over the
schema field when set and parses as an integer. Omitting both leaves the
integration method's own hardcoded default untouched — a timeout is never
set to `None` (which would mean "wait forever" instead of "use the
default").

Scoped to Terraform only: Helm/Compose dispatch through `deploy_namespace()`
instead, which has no equivalent per-phase breakdown to target — extending
this there needs its own shape decision, deliberately left out of scope.
`destroy` has no real call site in `deploy_run()` today; the model's
`destroy` field predates this decision (ported from v1 already) and simply
has no consumer yet.

## Consequences

- Good: closes a real, reported gap (no way to extend a legitimately
  long-running `terraform apply`) without a document edit required for a
  one-off case, and without a document edit *forced* onto every Deployment
  of a shared Workspace for a need that is usually environment-specific.
- Good: no new model needed — reuses a field that was already ported from
  v1 but had zero runtime consumer until now, rather than growing the
  schema with a second, overlapping timeout concept.
- Neutral: the field's `setup`/`check`/`apply` naming (v1's generic,
  deployer-type-agnostic vocabulary) doesn't match v2's own Terraform
  method names (`init`/`validate`/`deploy`) one-to-one — intentional, since
  the same model is meant to eventually serve Ansible/script-based stages
  too, not just Terraform.
- Bad: Helm/Compose steps have no timeout override mechanism yet, and
  Helm's own pre-existing dual-timeout footgun (Python subprocess timeout
  vs. Helm's own `--timeout` CLI flag disagreeing) remains unresolved —
  both explicitly out of scope for this decision.
