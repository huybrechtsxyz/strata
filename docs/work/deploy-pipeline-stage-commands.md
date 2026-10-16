# Running `setup`/`check`/`plan`/`apply`/`destroy` as Separate Pipeline Stages — Work

- Status: draft — Implementation Plan Phase 5/6 done (2026-10-16)
- Last updated: 2026-10-16

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

## Proposed Design — dedicated `setup`/`check`/`plan`/`apply` subcommands (2026-10-16)

Revisited per a direct ask: instead of bolting phase-stopping flags onto
`deploy run`, give each phase its own first-class subcommand, so a
pipeline author can structure CI stages/approval gates around real `deploy
setup`/`deploy check`/`deploy plan`/`deploy apply` invocations — the same
shape Terraform Cloud/Atlantis/a plain two-job `terraform plan` ->
`terraform apply <file>` CI pattern already takes for granted. `deploy
run` is **not replaced** — it stays exactly as-is, the one-shot
convenience path for the common case (confirmed no consumer evidence of
anyone wanting the split today, same as this doc's original finding);
the new subcommands are strictly additive, for consumers who do want
pipeline-stage gates.

**Internal evidence this is a natural seam, not a foreign idea**: v2's own
`DeploymentStageTimeoutsModel` (`deployment_model.py`) already carries
per-step timeout overrides named exactly `setup`/`check`/`plan`/`apply`
— the codebase's own mental model already treats these as four distinct
named phases; `deploy_controller.py`'s per-step loop already calls
`init()`/`validate()`/`plan_or_warn()`/`deploy()` as four clearly
separated blocks in that order, using those same `_timeout_kwargs(step,
deployment_stage, "setup"|"check"|"plan"|"apply")` phase names. Splitting
the orchestrator is reorganizing existing seams, not inventing new ones.

### Command surface

| Subcommand                | Runs (in order)                                                      | Stops after  | Prefix relationship                                                              |
| ------------------------- | -------------------------------------------------------------------- | ------------ | -------------------------------------------------------------------------------- |
| `deploy setup DEPLOYMENT` | `init()`                                                             | `init()`     | shortest prefix                                                                  |
| `deploy check DEPLOYMENT` | `init()` -> `validate()`                                             | `validate()` | `setup` + validate                                                               |
| `deploy plan DEPLOYMENT`  | `init()` -> `validate()` -> `plan()`, saves `<step>.tfplan`          | `plan()`     | `check` + plan; same work `--dry-run` already does today                         |
| `deploy apply DEPLOYMENT` | `init()` (re-run, cheap) -> `deploy(plan_file=<step>.tfplan)`        | `deploy()`   | **not** a prefix of the others — deliberately skips `validate`/`plan`, see below |
| `deploy run DEPLOYMENT`   | unchanged — all four phases, one process, no artifact handoff needed | `deploy()`   | unaffected by this design                                                        |

All four new subcommands accept the same `--stage`/`--scope`/`--pin`/
`--output-max-age`/`--follow`/`--path`/`--build-path` options `deploy run`
already has, unchanged in meaning — this design only adds *which phases
run*, never *which steps* (that axis is already solved).

### Per-step design

Each subcommand shares one preamble with `deploy run` that's easy to
under-estimate: resolving `backend_config`/`tokens` (upstream durable
outputs merged in, `${output:...}` substitution, the output pre-flight
check) happens **before** `init()` is even callable, because `init()`
itself takes the *resolved* `backend_config` as an argument. So none of
these four is a literal "just call one integration method in isolation"
— each one runs the same per-step preamble (tool availability preflight,
step ordering/filtering, claimed-name computation, durable-output merge,
backend-config token resolution) that `deploy run` already runs, and
only the *tail* of the loop body differs by how far it goes. This is the
main reason the later implementation item below proposes a shared `phase`
parameter on one function rather than four independent code paths —
duplicating that preamble four times would be a real maintenance hazard
(four copies to keep in sync against `deploy_controller.py`'s evolving
payload-building/token-resolution logic), not just more verbose.

#### `deploy setup DEPLOYMENT`

- **Purpose**: pre-stage the working directory — `terraform init`
  (download providers/modules, configure the backend). The one phase
  that's always safe and cheap to re-run from scratch.
- **Runs**: the shared preamble, then `init()` only.
- **Stops at**: a successful `init()` for every resolved step (or the
  first failure, which aborts the whole command — same fail-fast
  diagnostics pattern `deploy run` already uses, not a partial/best-effort
  run).
- **Container-capable (Helm/Compose) steps**: no-op, graceful skip —
  these tools have no separate init concept (see below).
- **Output**: the existing `_step("running step 'X' via Y")`-style
  progress lines, plus `init`'s own streamed tool output under `--follow`.

#### `deploy check DEPLOYMENT`

- **Purpose**: ask the underlying IaC tool whether the rendered config is
  internally well-formed — `terraform validate` (type/required-argument
  checks). Needs `init()` to have already fetched providers, but touches
  no real infrastructure and needs no state/backend access beyond that.
- **Runs**: the shared preamble, then `init()` -> `validate()`.
- **Explicitly not the same thing as `strata validate`** (the existing
  top-level command) — that one is solution-wide schema/semantic
  validation of every document (`validate_command.py`), independent of
  any one deployment or IaC tool; `deploy check` is narrowly "does this
  one deployment's already-rendered Terraform/Helm config pass the
  tool's own validation", nothing more. Worth stating this difference
  explicitly in the command's own `--help` text, not just in this doc,
  given the question below.
- **Container-capable steps**: no-op, graceful skip (same reasoning as
  `setup`).

#### `deploy plan DEPLOYMENT`

- **Purpose**: mirror real `terraform plan`'s own interactive UX as
  closely as possible — a DevOps engineer typing `strata deploy plan
  my-deployment` should see the same kind of human-readable add/change/
  destroy diff they'd get from typing `terraform plan` directly, not a
  silent "saved to file" message they then have to go find another
  command to inspect.
- **Runs**: the shared preamble, then `init()` -> `validate()` ->
  `plan_or_warn()`, saving `<step>.tfplan` — identical computation to
  today's `--dry-run`.
- **Default output differs from `deploy run`/`--dry-run` on purpose**:
  `deploy run` defaults `--follow` to off (it's one invocation potentially
  driving many steps/tools, where raw subprocess noise isn't always
  wanted); `deploy plan` should default `--follow` to `console` instead —
  streaming the plan's own diff is the entire reason to run this command.
  `--follow ndjson`/`--quiet`/`--output json` all still override this
  default exactly as they do today (console text auto-rerouted to stderr
  under `--output json`, so stdout stays one JSON document); a pipeline
  that wants quiet, structured output already has the knobs to ask for
  it, but the interactive default should look like running the tool
  directly.
- **No extra summary needs building** — Terraform's own streamed plan
  output already ends with its native "Plan: X to add, Y to change, Z to
  destroy." line; reusing that verbatim (by streaming it) is simpler and
  more trustworthy than strata re-deriving counts from `terraform show
  -json` itself.
- **Read-only against real infrastructure, safe to re-run freely** — no
  `--force` needed, matching Terraform's own `plan` semantics.
- **Container-capable steps**: `deploy_namespace(..., dry_run=True)`,
  same streaming-by-default treatment.
- **Naming note**: v1 has an existing, differently-scoped `deploy plan`
  (an offline *viewer* for an already-saved plan file, `terraform show
  -json <plan>`, catalogued in
  [deploy-operational-commands.md](deploy-operational-commands.md)).
  This design's `deploy plan` computes one; since it also *shows* the
  diff by default (above), it is a strict superset of v1's viewer
  behavior for the common case, not a true behavioral conflict — the one
  gap left relative to v1 is re-viewing an *already-saved* plan from a
  prior invocation without recomputing it, tracked separately in
  [deploy-operational-commands.md](deploy-operational-commands.md)'s
  Remaining Work rather than resolved here.

#### `deploy apply DEPLOYMENT`

- **Purpose**: apply *exactly* a previously reviewed `<step>.tfplan`,
  never a freshly recomputed one.
- **Runs**: the shared preamble, then `init()` (re-run, cheap — needed in
  a fresh CI runner where `.terraform/providers` doesn't exist yet) ->
  `integration.deploy(path, plan_file=f"{step.name}.tfplan", ...)`
  directly, skipping `validate()`/`plan_or_warn()` entirely.
- **Hard-errors, never silently re-plans**, if `<step>.tfplan` is missing
  at the resolved build path ("no saved plan found for step 'NAME'; run
  `deploy plan` first") — silently falling back to a fresh plan would
  defeat the reason this command exists.
- **`--force`**: stays a no-op, same existing rationale `deploy run`
  already documents (bypasses approval gates not yet built; applying a
  saved plan file never needs `-auto-approve` regardless).
- **Container-capable steps — a real, acknowledged asymmetry**:
  Helm/Compose have no saved-plan-file concept at all (`deploy_namespace()`
  only ever takes a `dry_run` bool, never a prior artifact to replay) —
  `deploy apply` against a container-capable step can only mean "do a
  real `dry_run=False` deploy right now", with no guarantee it matches
  whatever `deploy plan` showed earlier (Helm/Compose's own
  tools don't support that guarantee the way `terraform apply <planfile>`
  does). Document this honestly as a tool limitation, not something
  strata can paper over.

### Naming decisions (direct answer to "would a DevOps engineer expect `check` or `validate`?")

Both instincts are legitimate, and the design below keeps both:

- **The terraform-mirroring instinct is correct and shouldn't be
  dismissed** — a DevOps engineer deliberately mapping `setup`/`check`/
  `plan`/`apply` onto `init`/`validate`/`plan`/`apply` will reach for
  `validate` first, by analogy, before ever discovering `check` exists. Naming it
  `check` purely for internal consistency (matching
  `DeploymentStageTimeoutsModel.check`) at the cost of that real,
  reasonable expectation would be optimizing for the codebase's own
  convenience over the actual user's.
- **But a bare `validate` verb genuinely collides in meaning, not just in
  spelling**, with the existing top-level `strata validate [PATH]`
  (solution-wide schema/semantic validation, unrelated to Terraform) —
  listing `strata deploy validate` and `strata validate` side by side in
  documentation/`--help` output, doing two unrelated things under the
  identical verb, is a worse trap than a slightly-less-obvious name: a
  user who already knows `strata validate` would reasonably expect
  `deploy validate` to be the same kind of check, scoped to one
  deployment — and be wrong.
- **Resolution: `check` is the canonical, documented name; `validate` is
  a hidden alias to the identical command**, registered a second time
  under the `deploy` group with `hidden=True` (Click supports this
  directly — same callback, two registered names, only one shown in
  `--help`). This gets both properties at once: `strata deploy --help`
  only ever lists `check` (no ambiguity for anyone reading the docs or
  command list), while a DevOps engineer who types `strata deploy
  validate` out of Terraform muscle memory still gets exactly what they
  expect instead of a confusing "no such command" error. No behavior
  difference between the two names whatsoever — same function, same
  options, same output.

### Why `apply` is the odd one out (not a simple prefix)

Every other subcommand is a strict prefix of the next, exactly like
today's `--smoke-test`/`--dry-run`/real-run table — re-running
`init()`/`validate()` is cheap and idempotent, so each command simply
redoes the cheap prior phases rather than needing cross-process state
tracking of "did setup already happen". `apply` cannot work this way: its
entire purpose is to apply **exactly** the plan a human or automated gate
already reviewed, so it must skip `validate()`/`plan()` outright, never
recompute them. Concretely: `deploy apply` re-runs `init()` (needed in a
fresh CI container/runner where `.terraform/providers` doesn't exist
yet, and harmless if it does) and then calls `integration.deploy(path,
plan_file=f"{step.name}.tfplan", ...)` directly, the same call `deploy
run`/`deploy run --dry-run`'s prior plan step already feeds into today —
no new integration method needed, just a new call site that skips
straight to it.

### Cross-invocation state handoff (the real hard part)

- **The `<step>.tfplan` file itself must travel from the `deploy plan`
  invocation to the `deploy apply` invocation.** Trivial if both run
  sequentially against the same filesystem (one job, several steps); if
  they run in separate CI jobs/runners, the file must be passed as a
  build artifact (upload after `plan`, download before `apply`) — the
  same constraint already flagged in this doc's original Remaining Work
  item 1, now attached to a concrete command instead of a hypothetical
  flag.
- **Terraform plan files are not portable across differing backend
  config / provider lock files** (already-known Terraform constraint,
  not strata-specific) — `deploy apply` should let Terraform's own
  apply-time error surface through verbatim rather than add redundant
  strata-side validation; worth documenting the constraint for pipeline
  authors (same state/workspace, same `.terraform.lock.hcl`) so a failure
  reads as expected rather than mysterious.
- **Cross-step output dependencies can only partially plan ahead of an
  upstream apply.** If step B's plan references `${output:A.key}` and
  step A hasn't been applied yet (only planned), the value may be
  "(known after apply)" in Terraform's own plan output — a real,
  inherent Terraform limitation whenever two stacks are planned
  independently, not something strata's output-token mechanism can paper
  over. Pipelines that split phases across steps with real dependencies
  must apply in dependency order (`ordered_by_depends_on()` already
  enforces this ordering within one invocation; across separate `deploy
  apply` invocations per step, the pipeline author owns getting the job
  order right — same as any multi-stack Terraform CI/CD today). Worth
  documenting explicitly rather than discovering it as a confusing
  surprise.
- **`deploy apply` must hard-error, not silently re-plan, if
  `<step>.tfplan` is missing** at the resolved build path — e.g. "no
  saved plan found for step 'NAME'; run `deploy plan` first". Silently
  falling back to computing a fresh plan would defeat the entire reason
  phase-splitting exists (what gets applied must be exactly what was
  reviewed, never a surprise newer plan).

## Remaining Work / Open Questions (design above is proposed, not started — nothing implemented yet)

1. **Implement the four new subcommands** — superseded by the concrete,
   phased [Implementation Plan](#implementation-plan) below; no longer an
   open question, just not started yet.
2. **Resolve the `deploy plan` naming collision** (see above) —
   confirm v2's `deploy plan` means "compute", and track v1's "view a
   saved plan" meaning as a separate, smaller item in
   [deploy-operational-commands.md](deploy-operational-commands.md).
3. **Register `validate` as a hidden Click alias for `check`** — same
   callback added to the `deploy` group twice (`hidden=True` for the
   `validate` registration), per the naming-question resolution above.
   Small, mechanical, but easy to forget since it's invisible in
   `--help` once done — flagging explicitly so it isn't dropped during
   implementation.
4. **Default `--follow` to `console` specifically for `deploy plan`**
   (and only `deploy plan`, not `setup`/`check`/`apply`/`run`) — a
   one-line behavioral difference from how `--follow` defaults
   everywhere else in `deploy_command.py` today (opt-in, off by
   default); worth a code comment at the option definition explaining
   why this one command's default differs, so a future reader doesn't
   "fix" it back to matching the others.
5. **`DeploymentOutputStore` interaction for split `apply`** — today's
   single-process `deploy run` accumulates `step_outputs`/
   `step_sensitive_outputs` in memory across the whole loop; a `deploy
   apply` invoked as a separate process per step must instead read
   upstream outputs back from the durable `DeploymentOutputStore`
   ([ADR-0036](../decisions/0036-cross-run-key-value-output-store.md))
   exclusively — confirm this already works today for any multi-step
   `--stage`-filtered `deploy run` invocation (it should, since durable
   merge already happens via `_merge_durable_outputs()` regardless of
   process boundaries), rather than assuming it does.
6. **`deploy destroy`** — fully separate, pre-existing investigation; see
   [deploy-destroy-command.md](deploy-destroy-command.md) for the real
   gap list (no CLI command, no orchestrator, no `destroy_namespace()`).
   Not re-derived here; this phase-splitting design does not change
   destroy's own design.
7. **Container-capable (Helm/Compose) `setup`/`check` graceful-skip
   messaging** — exact wording/reporting shape for "this step has no
   init/validate phase" not yet drafted, just decided to be a skip, not
   an error (per-step design section above, "Container-capable steps"
   notes under `setup`/`check`).

## Implementation Plan

Phased the same way `deploy-command.md`'s own Implementation Plan was —
small, independently-testable, full check suite after each. Confirmed via
direct read that `deploy_controller.py` already persists durable step
outputs unconditionally after any successful real `deploy()` call
(`collect_step_outputs()` -> `output_store.record(...)`, not gated by
`dry_run`/any new phase flag) — so Remaining Work item 5's concern
(`DeploymentOutputStore` interaction for split `apply`) needs a
verification test in Phase 3 below, not new production code; the
existing mechanism already covers it.

1. ✅ **`deploy_controller.py`: add `stop_after` to `deploy_run()`** —
   done 2026-10-16. New parameter `stop_after: Literal["setup", "check"]
   | None = None` (required a new `Literal` import). Early-returns
   `diagnostics` right after `init()` succeeds (for `"setup"`) or right
   after `validate()` succeeds (for `"check"`), for every resolved step,
   before reaching `plan_or_warn()`. A container-capable
   (`Capability.CONTAINER`) step reports a graceful skip message (not an
   error) and `continue`s immediately, before `deploy_namespace()` is
   ever called, matching `plan_or_warn()`'s existing graceful-skip
   precedent. `dry_run`/`smoke_test` keep their current meaning
   unchanged. 5 new tests in
   `tests/strata/controllers/test_deploy_controller.py`: stops exactly
   after `init()`/`validate()` for a Terraform-capable step (one test
   each), graceful skip for a container-capable (Helm) step under both
   values (two tests, confirming zero `helm` calls), `stop_after=None`
   behaves identically to today (already covered by every pre-existing
   test, no new regression test needed). Full check suite green: mypy
   (159 files, 0 issues), ruff (clean), pytest (2646 passed), lint-imports
   (1 contract kept, 0 broken).
2. ✅ **`deploy_command.py`: `deploy setup`/`deploy check` CLI commands**
   — done 2026-10-16. Two new thin commands sharing one `_run_partial_deploy()`
   body, built on a new `_shared_deploy_options` decorator (factors
   `--path`/`--build-path`/`--follow`/`--stage`/`--scope`/`--pin`/
   `--output-max-age` into one reusable function so `setup`/`check` — and
   later `plan`/`apply` — cannot drift from `run`'s own option
   definitions; `run` itself was deliberately left with its options
   declared inline, unchanged, to avoid touching a stable, already
   well-tested command for a pure internal refactor). Also extracted
   `run`'s inline `--follow console`/`--follow ndjson` streaming glue into
   a shared `_line_follow_callbacks()` helper, reused by all three
   commands (pure refactor, `run`'s own behavior unchanged). Neither
   `setup` nor `check` threads the audit trail (`finalize_and_distribute_
   deploy_audit()`) — both are non-mutating (setup only touches the local
   working dir/provider cache, check is read-only), unlike `run`/the
   future `apply`. `check`'s callback is registered a second time under
   the name `validate` via `copy.copy(deploy_check_command)` +
   `.name = "validate"` + `.hidden = True` (Click does not mutate
   `cmd.name` on `add_command(cmd, name=...)`, so the alias's own `.name`
   has to be set explicitly, or its usage/help text would still read
   "check" — confirmed this does NOT happen: `deploy validate --help`'s
   usage line correctly reads "deploy validate", since Click derives it
   from `ctx.info_name`, not `cmd.name`). 9 new tests in
   `tests/strata/commands/test_commands_deploy.py`: setup-only/check-only
   command behavior, unknown-deployment/step-failure exit codes for
   `check`, `--stage`/`--scope` forwarding, `--help` lists `check` but not
   `validate` (checked line-by-line, not a bare substring match — the
   word "validate" legitimately appears inside `check`'s own help text),
   the hidden `validate` alias behaves identically to `check`, and a
   container-capable (Helm) step is gracefully skipped under `deploy
   setup` via the CLI (not just the controller, closing the loop on
   Phase 1's controller-only coverage). Full check suite green: mypy (159
   files, 0 issues), ruff (clean), pytest (2654 passed), lint-imports (1
   contract kept, 0 broken).
3. ✅ **`deploy_controller.py`: add `use_saved_plan` to `deploy_run()`
   (feeds `deploy apply`)** — done 2026-10-16. New parameter
   `use_saved_plan: bool = False`. The per-step loop now branches right
   after `init()`/the `stop_after == "setup"` check: when
   `use_saved_plan`, it skips `validate()`/`stop_after == "check"`
   entirely and instead checks `(path / f"{step.name}.tfplan").exists()`,
   hard-erroring via `diagnostics.error(...)` (not a crash) if missing —
   "no saved plan found at '<path>' — run 'deploy plan' first"; when not
   set, the existing `validate()`/`stop_after` logic is unchanged.
   Further down, the `plan_or_warn()` call and its dry-run/hard-error
   handling are now wrapped in `if not use_saved_plan:`, so `use_saved_plan`
   falls straight through to the existing `integration.deploy(path,
   plan_file=f"{step.name}.tfplan", ...)` call unmodified — no new
   integration method needed, matching the design. Container-capable
   steps need no code change at all: `deploy_namespace(..., dry_run=dry_run)`
   already resolves to a real deploy when `dry_run` is `False` (its
   default), so the acknowledged Helm/Compose asymmetry (no saved-plan
   replay) falls out of the existing call shape for free. Deliberately
   did **not** add a runtime mutual-exclusivity guard against
   `dry_run`/`smoke_test`/`stop_after` inside `deploy_run()` itself —
   matches the existing precedent that `dry_run`/`smoke_test`'s own
   mutual exclusivity is "enforced by `deploy_command.py`, not here"; the
   real CLI surface (Phase 4's `deploy apply`) will only ever pass
   `use_saved_plan=True` alone, so this is a non-issue for any real
   invocation. 3 new controller tests: missing-plan-file hard error
   (confirms only `init` ran, nothing else), successful apply from a
   pre-created dummy `<step>.tfplan` (confirms the command sequence is
   exactly `["init", "apply", "output"]` — `validate`/`plan` genuinely
   never called, visible directly in the captured command list, no mock
   `assert_not_called()` needed), and — directly resolving Remaining Work
   item 5 — a real two-invocation scenario: one `deploy_run(use_saved_plan=True,
   stage="apply_infra")` call followed by a wholly separate
   `deploy_run(use_saved_plan=True, stage="apply_apps")` call against the
   same fake durable backend, confirming the downstream step's
   `${output:apply_infra.vm_ip}` token resolves correctly purely from the
   durable record the first call wrote, with no in-memory state shared
   between the two calls. Full check suite green: mypy (159 files, 0
   issues), ruff (clean), pytest (2657 passed), lint-imports (1 contract
   kept, 0 broken).
4. ✅ **`deploy_command.py`: `deploy apply` CLI command** — done
   2026-10-16. Thin wrapper around `deploy_run(use_saved_plan=True)`,
   same shared options as `setup`/`check` (`_shared_deploy_options`) plus
   `--force` (kept as the existing documented no-op, identical text to
   `run`'s own). **Design addition made during this phase, not in the
   original plan above**: `deploy apply` performs a real infrastructure
   change (the same "apply" tail `deploy run` itself makes), so — unlike
   `setup`/`check`, which are genuinely non-mutating — it threads the
   audit trail (`finalize_and_distribute_deploy_audit()`) exactly like
   `deploy run` does, including the full `--change-*`/`--change-approved-*`
   flag set. Omitting this would have been a real regression for anyone
   switching from monolithic `deploy run` to the split plan/apply
   pipeline: the deployment would silently lose audit coverage. Because
   these 8 change-reference options feed `ChangeReferenceModel`
   (compliance-relevant, docs/design/audit-trail.md), and now TWO
   commands need them, factored them into a `_change_reference_options`
   decorator plus a `_validate_change_reference_flags()` helper — and,
   breaking the Phase 2 "don't touch `run`" precedent on purpose,
   retrofitted `run` itself onto both: two independently-maintained
   copies of the same compliance-sensitive option set drifting apart
   (a typo'd help string, a missing metavar) is a worse outcome here
   than for the purely ergonomic `_shared_deploy_options` set, so the
   drift risk outweighs the "don't touch a stable command" caution that
   applied in Phase 2. `apply`'s own `dry_run` passed to the audit call
   is always `False` (never zero-contact/dry, unlike `run`). 6 new CLI
   tests: missing-plan-file error exits 3 cleanly (not a traceback,
   message visible in CLI output), successful apply from a pre-created
   saved plan, `--force` still inert, `--stage`/`--scope` forwarding,
   change-reference flags populate the manifest (reusing `run`'s own
   `layout.audit_dir()`-based assertion convention), and the
   partial-change-reference-flags usage error. Full check suite green
   (mypy 159 files, ruff clean, 2663 pytest passed, lint-imports 1
   contract kept).
5. ✅ **`deploy_command.py`: `deploy plan` CLI command with
   console-by-default `--follow`** — done 2026-10-16. Thin wrapper
   around `deploy_run(dry_run=True)` (identical computation to
   `deploy run --dry-run`). `_shared_deploy_options` became a decorator
   **factory** (`_shared_deploy_options(follow_default: str | None =
   None)`) rather than adding a 4th near-duplicate option block — its
   `--follow` option's `default=` is now parameterized, with an inline
   comment explaining why `plan` alone passes `follow_default="console"`;
   `setup`/`check`/`apply`'s own call sites just gained an empty `()`.
   `_run_partial_deploy()` (the shared non-mutating body setup/check
   already used) gained two explicit, correctly-typed optional
   parameters — `stop_after: Literal["setup", "check"] | None` and
   `dry_run: bool = False` — replacing what would otherwise have needed a
   generic `**kwargs` (tried first; mypy strict correctly rejected it,
   since `deploy_run()`'s own parameters aren't all the same type) so
   `plan` could reuse the same shared body via `dry_run=True` without
   inventing a fourth near-identical function. 7 new CLI tests: full
   `init`/`validate`/`plan` sequence (never `apply`), `<step>.tfplan` is
   genuinely saved, **the default streams console output with no
   `--follow` flag at all** (the one behavior this phase exists for),
   explicit `--follow ndjson` still overrides the console default,
   unknown-deployment/plan-failure exit codes. One assumption corrected
   via a real test failure: `--quiet` does **not** suppress the default
   console stream (it only suppresses the header/progress/summary; the
   line-callback path is independent) — only `--output json` reroutes it
   to stderr, matching `deploy run`'s own existing precedent; fixed both
   the test and the command's own docstring, which had repeated the same
   wrong assumption. Full check suite green (mypy 159 files, ruff clean,
   2670 pytest passed, lint-imports 1 contract kept).
6. ☐ **Docs** — update `deploy-command.md`'s own command-surface summary
   to cross-reference the four new subcommands (brief pointer, not a
   re-description — same cross-referencing style already used for
   `deploy destroy`); update this doc's Status header to reflect
   implementation progress per phase; once all five phases are done,
   evaluate whether this work doc graduates to `docs/design/` (matching
   `terraform-variable-precedence.md`'s precedent) or stays linked from
   `deploy-command.md` as a permanent sibling (destroy's own doc is the
   closer precedent, having stayed a `docs/work/` doc past its own
   "fully designed" milestone) — decide at that time, not now.

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
- [deploy-operational-commands.md](deploy-operational-commands.md) —
  v1's real, differently-scoped `deploy plan` (a saved-plan *viewer*),
  the naming collision this doc's proposed design flags and defers to
  that doc to resolve separately.
- `src/strata/commands/deploy_command.py` /
  `src/strata/controllers/deploy_controller.py` — the real source read
  directly to confirm every claim in this doc, including the proposed
  design's internal-evidence section (`DeploymentStageTimeoutsModel`'s
  existing `setup`/`check`/`plan`/`apply` field names, the per-step
  loop's existing four-block structure).

## Changelog

- 2026-10-09: Created, in response to the question "can we run
  setup/check/plan/apply/destroy as separate pipeline stages?". Confirmed
  by direct code read (not from memory of the design docs) that today's
  answer is no for all four non-destroy phases — `deploy run` is one
  atomic sequence per invocation, `--dry-run` is the only phase-aligned
  flag (a strict prefix stopping after `plan`), and there is no
  cross-invocation plan-file handoff for `apply`. Cross-referenced the
  pre-existing `deploy destroy` gap rather than re-investigating it.
- 2026-10-16: Added a full proposed design for dedicated `deploy
  setup`/`check`/`plan`/`apply` subcommands, in response to a direct
  "let's think like a senior DevOps architect" ask to split the pipeline
  for finer CI control. Grounded in real code read directly
  (`deploy_controller.py`'s per-step loop, `DeploymentStageTimeoutsModel`)
  rather than assumption. Resolved two real naming collisions: `check`
  instead of `validate` (collides with the existing top-level `strata
  validate` schema-validation command), and flagged (but deliberately did
  not resolve) `deploy plan`'s conflict with v1's differently-scoped
  existing command of the same name, deferring that to
  [deploy-operational-commands.md](deploy-operational-commands.md).
  Identified `apply` as the one non-prefix command (must skip
  `validate`/`plan` entirely, never recompute them) and worked through
  the real cross-invocation state-handoff hazards (plan file portability,
  cross-step known-after-apply values, container-capable steps having no
  init/validate concept). Design only — nothing implemented, per explicit
  instruction ("design in the doc only").
- 2026-10-16 (same day, follow-up): Added a dedicated per-step design
  subsection for each of `setup`/`check`/`plan`/`apply`, per a direct
  request. Changed `deploy plan` to default `--follow` to `console`
  (unlike every other subcommand) so it mirrors real `terraform plan`'s
  interactive UX — show the diff by default, not just silently save a
  file. Directly answered the "would a DevOps engineer expect `check` or
  `validate`?" question: both instincts are legitimate, resolved by
  keeping `check` as the sole documented/canonical name (avoids a real
  meaning-collision with the existing top-level `strata validate`
  schema-validation command) while registering `validate` as a hidden
  Click alias to the identical command, so Terraform muscle memory still
  works without creating a second documented, confusingly-different
  `validate`. Also documented that none of the four phases is a literal
  single-method call in isolation — all share `deploy run`'s existing
  per-step preamble (backend-config token resolution, durable-output
  merge) before the phase-specific tail differs, which is the real
  argument for implementing these as one parameterized function rather
  than four duplicated code paths.
- 2026-10-16 (same day, second follow-up): Added a phased Implementation
  Plan (6 phases: `stop_after` controller param -> `setup`/`check` CLI
  commands -> `use_saved_plan` controller param -> `apply` CLI command ->
  `plan` CLI command with console-by-default `--follow` -> docs), per a
  direct request now that every step's design was settled. Confirmed via
  direct read that `deploy_controller.py` already persists durable step
  outputs unconditionally after any successful real `deploy()`
  (`collect_step_outputs()` -> `output_store.record(...)`) — resolved
  Remaining Work item 5's open question in the process: the existing
  mechanism already covers cross-invocation output accumulation, so
  Phase 3 only needs a verification test, not new production code.
  Nothing implemented — plan only.
- 2026-10-16 (same day, third follow-up): Implemented Phase 1 —
  `deploy_run()` gained `stop_after: Literal["setup", "check"] | None`,
  stopping right after `init()`/`validate()` for every resolved step,
  with a graceful (not hard-error) skip for container-capable steps. 5
  new tests; full check suite green (mypy 159 files, ruff clean, 2646
  pytest passed, lint-imports 1 contract kept). No CLI surface yet —
  that's Phase 2.
- 2026-10-16 (same day, fourth follow-up): Implemented Phase 2 — added
  `strata deploy setup`/`strata deploy check` CLI commands plus the
  hidden `strata deploy validate` alias, on top of Phase 1's
  `stop_after`. Factored shared option declarations and the `--follow`
  streaming glue out of `deploy run` into reusable helpers (pure
  refactor, `run`'s own behavior/options left untouched). 9 new CLI
  tests; full check suite green (mypy 159 files, ruff clean, 2654 pytest
  passed, lint-imports 1 contract kept). Confirmed by direct invocation
  that the hidden alias's own `--help` usage line correctly reads
  "deploy validate", not a stale "check", since Click derives it from
  the invocation context rather than the command object's own name.
- 2026-10-16 (same day, fifth follow-up): Implemented Phase 3 —
  `deploy_run()` gained `use_saved_plan: bool`, skipping `validate()`/
  `plan_or_warn()` and applying the exact saved `<step>.tfplan`,
  hard-erroring if it's missing. No change needed for container-capable
  steps — `deploy_namespace(..., dry_run=dry_run)`'s existing call shape
  already does the right thing. 3 new tests, including a real two-call
  cross-invocation test that resolved Remaining Work item 5 (confirmed
  the durable `DeploymentOutputStore` write/read already works correctly
  across separate `deploy_run()` calls, with no new production code
  needed for that part). Full check suite green (mypy 159 files, ruff
  clean, 2657 pytest passed, lint-imports 1 contract kept). No CLI
  surface yet — that's Phase 4.- 2026-10-16 (same day, sixth follow-up): Implemented Phase 4 — the
  `strata deploy apply` CLI command. Added a design decision not in the
  original plan: `apply` threads the audit trail (unlike `setup`/`check`),
  since it makes a real infrastructure change; factored the 8
  `--change-*` options and their validation into shared helpers and
  retrofitted `run` onto them too (a deliberate, explained exception to
  Phase 2's "don't touch `run`" rule, justified by compliance-sensitive
  drift risk between two copies of the same option set). 6 new CLI
  tests; full check suite green (mypy 159 files, ruff clean, 2663 pytest
  passed, lint-imports 1 contract kept).
- 2026-10-16 (same day, seventh follow-up): Implemented Phase 5 — the
  `strata deploy plan` CLI command, streaming the diff by console by
  default (its own `--follow` option defaults to `"console"`, every
  other command's stays `None`). Turned `_shared_deploy_options` into a
  decorator factory and `_run_partial_deploy()`'s `stop_after`-only
  parameter into two explicit optional parameters (`stop_after`/
  `dry_run`) rather than a generic `**kwargs` (mypy strict correctly
  rejected that first attempt). 7 new CLI tests, including one that
  caught and corrected a wrong assumption in the command's own docstring
  (`--quiet` does not suppress the console stream; only `--output json`
  reroutes it to stderr). Full check suite green (mypy 159 files, ruff
  clean, 2670 pytest passed, lint-imports 1 contract kept).