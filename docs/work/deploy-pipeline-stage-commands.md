# Running `setup`/`check`/`plan`/`apply`/`destroy` as Separate Pipeline Stages — Work

- Status: draft
- Last updated: 2026-10-09

## Question being investigated

Can a CI pipeline invoke v2 once per phase — e.g. a `setup` job, then a
`check` job, then a `plan` job (publishing a plan artifact), then a later
`apply` job that consumes exactly that plan, then (separately) a
`destroy` job — the way v1's Tier 2 docs and several real IaC pipelines
(Terraform Cloud runs, Atlantis, plain `terraform plan` artifact ->
`terraform apply <file>` across two CI jobs) structure it? Investigated
directly against the real code (`deploy_command.py`/`deploy_controller.py`),
not from memory of the design docs.

**Short answer: no, not today.** `strata deploy run` is one atomic
`init -> validate -> plan -> deploy` sequence per invocation
(`deploy_controller.py`'s per-step loop, confirmed by direct read)．There
is no flag or subcommand that runs only one phase and exits, and no
mechanism to apply a plan file saved by an *earlier, separate*
invocation. `strata deploy destroy` does not exist at all yet. Full
breakdown below, phase by phase.

## Current state, phase by phase

### `setup` (`terraform init`)

No standalone entry point. `init()` always runs as the first thing inside
`deploy_run()`'s per-step loop (`deploy_controller.py`, `_timeout_kwargs(...,
"setup")` call site) — every invocation re-runs it, whether that
invocation is `--smoke-test` (skipped entirely, see below), `--dry-run`,
or a real apply. No way to run *only* init and stop.

### `check` (`terraform validate`)

Same story — `validate()` runs unconditionally right after `init()`,
every invocation that isn't `--smoke-test`. No flag stops after `check`
specifically; the closest is `--dry-run`, which runs `check` but doesn't
stop there either — it continues on into `plan`.

### `plan`

**Partially achievable today** via `--dry-run`: it runs `init` ->
`validate` -> `plan` for real (saved to `<step>.tfplan`) and stops before
`deploy()` (`deploy-plan-preview.md`'s documented mode). This is the one
phase that already has a dedicated, real flag.

What it does **not** give you: a plan produced by a `--dry-run` invocation
cannot be consumed by a *later, separate* `apply`-only invocation.
`deploy_run()` has no "skip straight to apply using an existing
`<step>.tfplan`" code path — every call always re-plans immediately
before applying, in the same process, using the plan result it just
computed (`deploy_controller.py`: `plan_result = integration.plan_or_warn(...)`
followed directly by `deploy_result = integration.deploy(path,
plan_file=f"{step.name}.tfplan", ...)` in the same loop iteration). A
`<step>.tfplan` file left on disk by an earlier `--dry-run` run is
ignored and silently overwritten by the next invocation's own fresh plan,
not read back.

### `apply`

**Not separately invokable.** There is no flag to run only `deploy()`
against an already-existing, previously-computed plan file — `apply`
only ever happens as the tail end of the same call that just computed the
plan it applies. A CI pattern of "plan stage uploads `<step>.tfplan` as a
build artifact, apply stage downloads it and applies exactly that
artifact" (the standard safeguard against "what gets applied might not
be what a human reviewed") is not supported.

### `destroy`

**Does not exist in v2 at all** — already fully investigated in
[deploy-destroy-command.md](deploy-destroy-command.md): no
`destroy_command.py`, no `destroy_run()`, and three existing code
comments (`audit_run.py`, `docs/design/gitops-integration.md`,
`docs/design/cli-timeout.md`) already flag the gap. That doc has the full
breakdown of what's already built and destroy-aware (the integration
layer's `InfraIntegration.destroy()`, the audit models' `"destroy"`
action literal) versus what's missing (the orchestrator, the CLI command,
`destroy_namespace()` for Helm/Compose). Nothing in this doc repeats that
investigation — see that doc directly for destroy specifically.

## What *is* available today, in terms of granularity

Two axes of filtering exist, and neither is phase-level:

- **`--stage NAME`** — restricts a `deploy run` invocation to the one
  workspace execution step (`ProvisioningStepModel.name`) named `NAME`.
  This still runs that one step's full `setup -> check -> plan -> apply`
  sequence, just for a single step instead of the whole graph.
- **`--scope LABEL`** — same, but by `ProvisioningStepModel.scope`
  (haven's real `infra`/`apps` split, per `deploy-command.md`'s evidence
  section), can match multiple steps.

These answer "which step(s)", never "which phase" — easy to conflate with
what this doc is about, worth calling out explicitly since the CLI's own
`--stage` option name overlaps with the informal English word "stage"
(as in pipeline stage/phase) used in this doc's title and the user's
original question.

The three real execution *modes* (`deploy-plan-preview.md`) are the
closest thing to phase granularity that exists:

| Mode                  | setup             | check | plan | apply |
| --------------------- | ----------------- | ----- | ---- | ----- |
| `--smoke-test`        | no (zero contact) | no    | no   | no    |
| `--dry-run`           | yes               | yes   | yes  | no    |
| *(neither, real run)* | yes               | yes   | yes  | yes   |

Every mode is a strict prefix of the next — there is no mode that is
"only check", "only plan without the setup/check that must precede it",
or "apply without re-planning first".

## Why this gap exists (not a bug — a real, confirmed design choice)

`ADR-0027` (`docs/decisions/0027-strata-deploy-run.md`) settled on one
atomic orchestrator per invocation; splitting `setup`/`check`/`plan` into
separately-invokable commands was never in scope for that decision.
Re-running `init`/`validate` every time is cheap and idempotent — the
real cost is `plan` not being separable from `apply`, since that is the
one phase pair real-world CI patterns legitimately want split across two
jobs/approval gates (a human or automated check reviews the plan
artifact; only then does a separate, later job apply exactly that
artifact — never a fresh one).

## Remaining Work / Open Questions (not started — this doc is investigation only)

1. **Apply a previously-saved plan file across two separate invocations**
   — the one phase split with genuine real-world demand (plan/apply
   approval gates). Would need: (a) `deploy_run()` to accept something
   like `--plan-only` (stop right after a successful `plan()`, same as
   `--dry-run` today — arguably `--dry-run` already *is* this, and only
   needs documenting as such) and (b) a new `--apply-saved-plan` (or
   similar) mode that skips `init`/`validate`/`plan()` entirely and calls
   `integration.deploy(path, plan_file=f"{step.name}.tfplan", ...)`
   directly against whatever `<step>.tfplan` is already on disk at
   `--build-path`, trusting that a prior `--dry-run` invocation (possibly
   in an earlier CI job, possibly even on a different machine if the
   `.tfplan` file is passed along as a build artifact) produced it.
   Terraform plan files are **not** portable across different `terraform
   init` backend configurations/providers versions by design — would need
   to document that constraint explicitly (same state/workspace, same
   provider lock file) rather than silently producing a confusing
   Terraform-level error if violated.
2. **Standalone `check`-only mode** — no confirmed real-world demand yet
   (unlike plan/apply splitting); `--dry-run` already exercises `check` as
   a side effect of previewing a plan. Would only be worth adding if a
   consumer wants IaC linting/validation as its own CI gate, independent
   of whether a plan can even be computed (e.g. `terraform validate`
   passing while `terraform plan` fails for an unrelated reason like
   missing credentials) — not yet evidenced in either reference consumer
   (`haven`/`config-deploy`, per `/memories/repo/v1-consumer-usage.md`).
3. **Standalone `setup`-only mode** — no evidenced real-world demand found
   so far; `init` is cheap/idempotent and always safe to re-run as a
   prefix of every other mode.
4. **`deploy destroy`** — fully separate, pre-existing investigation; see
   [deploy-destroy-command.md](deploy-destroy-command.md) for the real
   gap list (no CLI command, no orchestrator, no `destroy_namespace()`).
   Not re-derived here.
5. **Container-capable (Helm/Compose) steps don't have a `plan`/`apply`
   split to begin with** — `deploy_namespace()` is one call already
   internally branching on `dry_run`; the plan/apply separation problem
   above is Terraform-specific (only Terraform integrations implement a
   saved-plan-file handoff). Any future `--apply-saved-plan` mode would
   need to either no-op or hard-error for a `Capability.CONTAINER` step,
   not silently misbehave.

## Related Decisions / Files

- [deploy-command.md](deploy-command.md) — `deploy run`'s full design;
  this doc's "Current state" section is a phase-level lens on the same
  orchestrator, not a redesign.
- [deploy-destroy-command.md](deploy-destroy-command.md) — the `destroy`
  gap in full detail; this doc only cross-references it.
- [docs/design/deploy-plan-preview.md](../design/deploy-plan-preview.md)
  — `--smoke-test`/`--dry-run`/real-apply mode table this doc's own table
  is derived from.
- [ADR-0027](../decisions/0027-strata-deploy-run.md) — the one-atomic-
  invocation-per-call decision this doc's gap traces back to.
- `src/strata/commands/deploy_command.py` /
  `src/strata/controllers/deploy_controller.py` — the real source read
  directly to confirm every claim in this doc.

## Changelog

- 2026-10-09: Created, in response to the question "can we run
  setup/check/plan/apply/destroy as separate pipeline stages?". Confirmed
  by direct code read (not from memory of the design docs) that today's
  answer is no for all four non-destroy phases — `deploy run` is one
  atomic sequence per invocation, `--dry-run` is the only phase-aligned
  flag (a strict prefix stopping after `plan`), and there is no
  cross-invocation plan-file handoff for `apply`. Cross-referenced the
  pre-existing `deploy destroy` gap rather than re-investigating it.
