# Deploy Pipeline Stage Commands (`setup`/`check`/`plan`/`apply`) — Design

- Status: current
- Last updated: 2026-10-16

## Overview

`strata deploy run` executes a deployment's whole `setup -> check -> plan
-> apply` sequence as one atomic invocation per step. For pipelines that
want finer CI control — a `plan` stage whose output a human or automated
gate reviews, followed by a separate, later `apply` stage that applies
*exactly* that reviewed plan, the same shape Terraform Cloud/Atlantis/a
plain two-job `terraform plan` -> `terraform apply <file>` CI pattern
already takes for granted — four additional subcommands exist, each a
narrower slice of the same orchestrator:

```
strata deploy setup DEPLOYMENT   # init only
strata deploy check DEPLOYMENT   # init -> validate
strata deploy plan  DEPLOYMENT   # init -> validate -> plan, saves <step>.tfplan
strata deploy apply DEPLOYMENT   # init -> applies the saved <step>.tfplan exactly
```

`deploy run` is **not replaced or deprecated** — it remains the one-shot
convenience path for the common case; these four are strictly additive.
`deploy destroy` is a fully separate, independently designed command —
see [deploy-command.md](deploy-command.md)'s "`deploy destroy` —
designed, not implemented" section.

**Internal evidence this split follows an existing seam, not a foreign
one**: `DeploymentStageTimeoutsModel` (`deployment_model.py`) already
carries per-step timeout overrides named exactly `setup`/`check`/`plan`/
`apply`, and `deploy_controller.py`'s per-step loop already called
`init()`/`validate()`/`plan_or_warn()`/`deploy()` as four distinct blocks
in that order before any of this existed — splitting the orchestrator
reorganized existing seams rather than inventing new ones.

## Current Design

### Command surface

| Subcommand                | Runs (in order)                                               | Stops after  | Prefix relationship                                                              |
| ------------------------- | ------------------------------------------------------------- | ------------ | -------------------------------------------------------------------------------- |
| `deploy setup DEPLOYMENT` | `init()`                                                      | `init()`     | shortest prefix                                                                  |
| `deploy check DEPLOYMENT` | `init()` -> `validate()`                                      | `validate()` | `setup` + validate                                                               |
| `deploy plan DEPLOYMENT`  | `init()` -> `validate()` -> `plan()`, saves `<step>.tfplan`   | `plan()`     | `check` + plan; same work `deploy run --dry-run` also does                       |
| `deploy apply DEPLOYMENT` | `init()` (re-run, cheap) -> `deploy(plan_file=<step>.tfplan)` | `deploy()`   | **not** a prefix of the others — deliberately skips `validate`/`plan`, see below |
| `deploy run DEPLOYMENT`   | all four phases, one process, no artifact handoff needed      | `deploy()`   | unaffected by this design                                                        |

All four accept the same `--stage`/`--scope`/`--pin`/`--output-max-age`/
`--path`/`--build-path` options `deploy run` has — this design only adds
*which phases run*, never *which steps* (`--stage`/`--scope` already
solve that axis, unchanged).

None of the four is a literal "just call one integration method in
isolation": every one shares `deploy run`'s own per-step preamble
(tool-availability preflight, step ordering/filtering, claimed-name
computation, durable-output merge, backend-config token resolution) —
`init()` itself takes the *resolved* `backend_config` as an argument, so
that resolution has to happen before `init()` is even callable. Only the
tail of the loop body differs by how far each subcommand goes
(`deploy_controller.deploy_run()`'s `stop_after`/`use_saved_plan`
parameters).

### Per-step behavior

- **`deploy setup`** — `terraform init` (or equivalent): downloads
  providers/modules, configures the backend. Always safe and cheap to
  re-run from scratch. A container-capable (Helm/Compose) step has no
  separate init concept and is reported as a graceful skip, never an
  error.
- **`deploy check`** — adds `terraform validate`: confirms the rendered
  config is internally well-formed (type/required-argument checks).
  Needs `init()` to have already fetched providers, but touches no real
  infrastructure. **Distinct from the existing top-level `strata
  validate [PATH]` command** — that one validates the whole solution's
  documents against their schemas, independent of any one deployment or
  IaC tool; `deploy check` only asks the underlying IaC tool whether one
  deployment's already-rendered config passes its own validation.
  Container-capable steps: same graceful skip as `setup`.
- **`deploy plan`** — adds `plan()`, saving `<step>.tfplan`. Streams the
  plan's own diff to the console **by default** (its own `--follow`
  option defaults to `"console"`, unlike every other deploy subcommand,
  which defaults it to `None`/opt-in) — deliberately mirrors running
  `terraform plan` directly, since showing the diff is the entire reason
  to run this command. `--output json` reroutes the stream to stderr
  instead (stdout stays one JSON document); there is currently no flag to
  silence it entirely. No extra summary needs building — Terraform's own
  streamed output already ends with its native "Plan: X to add, Y to
  change, Z to destroy." line. Read-only against real infrastructure,
  never needs `--force`. Container-capable steps:
  `deploy_namespace(dry_run=True)`, same streaming-by-default treatment.
- **`deploy apply`** — re-runs `init()` (cheap, needed in a fresh CI
  runner where `.terraform/providers` doesn't exist yet) then calls
  `integration.deploy(path, plan_file=f"{step.name}.tfplan", ...)`
  directly, **skipping `validate()`/`plan()` entirely** — the one
  subcommand that is not a strict prefix of the others, since its whole
  purpose is applying *exactly* the plan a human or automated gate
  already reviewed, never a freshly recomputed one. Hard-errors (a
  `Diagnostic`, never a crash or a silent fallback to a fresh plan) if
  `<step>.tfplan` is missing: "no saved plan found at '<path>' — run
  'deploy plan' first". Makes a real infrastructure change, so — unlike
  `setup`/`check`/`plan` — it threads the full audit trail
  (`finalize_and_distribute_deploy_audit()`, docs/design/audit-trail.md),
  including every `--change-*`/`--change-approved-*` flag `deploy run`
  itself accepts, so a pipeline split into plan/apply stages never
  silently loses audit coverage relative to a monolithic `deploy run`.
  `--force` stays the existing documented no-op.

### Naming: `check`, not `validate` (with a hidden alias)

A DevOps engineer mapping `setup`/`check`/`plan`/`apply` onto Terraform's
own `init`/`validate`/`plan`/`apply` vocabulary will reach for `validate`
first, by analogy — but a bare `validate` subcommand would genuinely
collide in *meaning*, not just spelling, with the existing top-level
`strata validate [PATH]` (solution-wide schema validation, unrelated to
Terraform): a user who already knows `strata validate` would reasonably
expect `deploy validate` to be the same kind of check, scoped to one
deployment, and be wrong.

Resolution: `check` is the sole canonical, documented name (all
`--help`/usage text); `validate` is registered as a **hidden** Click
alias of the identical command (`copy.copy(deploy_check_command)` with
`.name = "validate"` and `.hidden = True` — Click does not mutate
`cmd.name` on `add_command(cmd, name=...)`, so the copy's own `.name`
must be set explicitly, or its usage text would still read "check"; its
usage line correctly reads "deploy validate" regardless, since Click
derives that from the invocation context, not `cmd.name`). This gets
both properties: `strata deploy --help` only ever lists `check`, while
`strata deploy validate ...` out of Terraform muscle memory still works,
with zero behavioral difference between the two names.

v1 has an existing, differently-scoped `deploy plan` (an offline
*viewer* for an already-saved plan file). This design's `deploy plan`
computes one instead — and since it also shows the diff by default, it
is a strict superset of v1's viewer behavior for the common case. The one
gap relative to v1 — re-viewing an already-saved plan from a prior
invocation without recomputing it — is tracked separately in
[deploy-command.md](deploy-command.md)'s "Other `deploy` subcommands"
section, not resolved here.

### Cross-invocation state handoff

- **The `<step>.tfplan` file itself must travel from the `deploy plan`
  invocation to the `deploy apply` invocation.** Trivial if both run
  sequentially against the same filesystem (one job, several steps); if
  they run in separate CI jobs/runners, the file must be passed as a
  build artifact (upload after `plan`, download before `apply`).
- **Terraform plan files are not portable across differing backend
  config/provider lock files** (an inherent Terraform constraint, not
  strata-specific) — `deploy apply` lets Terraform's own apply-time error
  surface through verbatim rather than adding redundant strata-side
  validation.
- **Cross-step output dependencies can only partially plan ahead of an
  upstream apply.** If step B's plan references `${output:A.key}` and
  step A has only been planned (not applied) yet, the value may read
  "(known after apply)" in Terraform's own plan output — an inherent
  Terraform limitation whenever two stacks are planned independently, not
  something strata's output-token mechanism can paper over. Pipelines
  that split phases across dependent steps must apply in dependency
  order — the pipeline author owns getting the job order right across
  separate `deploy apply` invocations, same as any multi-stack Terraform
  CI/CD today.
- **Cross-invocation output handoff via `DeploymentOutputStore`
  ([ADR-0036](../decisions/0036-cross-run-key-value-output-store.md))
  already works correctly with no special-casing**: `deploy_controller.py`
  persists durable step outputs unconditionally after any successful real
  `deploy()` call, not gated by any mode flag, so a downstream step's
  `${output:...}` token resolves from the durable record regardless of
  whether the upstream step was applied via `deploy run` or a separate
  `deploy apply` invocation in an earlier CI job.

### Container-capable (Helm/Compose) steps — one real asymmetry

Helm/Compose have no saved-plan-file concept at all — `deploy_namespace()`
only ever takes a `dry_run` bool, never a prior artifact to replay. So
`deploy apply` against a container-capable step can only mean "do a real
deploy right now", with no guarantee it matches whatever an earlier
`deploy plan` showed. This is an acknowledged tool limitation, not
something strata can paper over.

## Related Decisions

- [deploy-command.md](deploy-command.md) — `deploy run`'s own full
  design (plus `deploy destroy`'s design and a survey of v1's other
  `deploy` subcommands); this doc only adds phase-level granularity on
  top of the same orchestrator, never redesigning it.
- [deploy-plan-preview.md](deploy-plan-preview.md) — `--smoke-test`/
  `--dry-run`/real-apply execution modes `deploy run` itself still has;
  `deploy plan` reuses `--dry-run`'s own computation under the hood.
- [ADR-0027](../decisions/0027-strata-deploy-run.md) — the one-atomic-
  orchestrator-per-invocation decision this design narrows one invocation
  at a time, without reopening.
- [ADR-0036](../decisions/0036-cross-run-key-value-output-store.md) —
  `DeploymentOutputStore`, the durable backend `deploy apply`'s
  cross-invocation output handoff relies on.
- `src/strata/commands/deploy_command.py` / `src/strata/controllers/deploy_controller.py`
  — the real implementation.

## History

- An initial investigation (2026-10-09) found no phase-level granularity
  existed at all: `deploy run` was one atomic sequence per invocation,
  `--dry-run` was the only phase-aligned flag (a strict prefix stopping
  after `plan`), and there was no cross-invocation plan-file handoff for
  `apply`. That finding motivated this whole feature.
- Considered bolting phase-stopping flags onto `deploy run` itself
  instead of adding dedicated subcommands; rejected in favor of first-class
  subcommands, matching how real CI pipelines (Terraform Cloud, Atlantis)
  structure plan/apply as genuinely separate stages/jobs, not flag
  variations of one command.
- `apply` could not be made a simple prefix of the other three (unlike
  `setup`/`check`/`plan`, which redo cheap, idempotent prior phases) —
  its entire purpose is applying exactly a previously reviewed plan, so
  it has to skip `validate()`/`plan()` outright rather than recompute
  them.
- `deploy apply` was deliberately given full audit-trail coverage (unlike
  `setup`/`check`/`plan`, which are non-mutating) since it makes a real
  infrastructure change — omitting this would have been a silent
  audit-coverage regression for anyone adopting the split pipeline over
  `deploy run`. This also justified retrofitting `deploy run`'s own
  `--change-*` options onto a new shared decorator (a deliberate
  exception to an earlier default of not touching `run` for purely
  ergonomic refactors) — two independently-maintained copies of the same
  compliance-relevant option set drifting apart was judged a worse risk
  than the one-time churn of touching a stable command.
- `_shared_deploy_options` became a decorator factory (not a fourth
  duplicate option block) once `deploy plan` needed a different
  `--follow` default than the other three commands; the shared
  non-mutating command body (`setup`/`check`/`plan`) similarly took
  explicit, correctly-typed `stop_after`/`dry_run` parameters rather than
  a generic `**kwargs` — mypy strict correctly rejected the `**kwargs`
  attempt, since `deploy_run()`'s own parameters aren't all the same
  type.
- One wrong assumption was caught by a real test failure during
  implementation: `--quiet` does not suppress `deploy plan`'s default
  console stream (it only suppresses the header/progress/summary; the
  line-callback path is independent) — only `--output json` reroutes it
  to stderr, matching `deploy run`'s own existing precedent. Both the
  test and the command's own docstring, which had repeated the same wrong
  claim, were corrected.
