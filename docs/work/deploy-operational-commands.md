# Other `deploy` Subcommands (`status`/`history`/`health`/`lock`/`output`/`show`/`list`/`plan`/`drift`) — Work

- Status: draft — investigation only, nothing designed in detail yet
- Last updated: 2026-10-16

## Overview

Beyond `deploy run` ([deploy-command.md](deploy-command.md)) and
`deploy destroy` ([deploy-destroy-command.md](deploy-destroy-command.md)),
v1 ships ten more subcommands under `commands/deploy/`:
`status`/`history`/`health`/`lock` (status, presumably acquire/release)/
`output`/`show`/`list`/`plan`/`drift`/`drift_history`/
`acknowledge_drift`. All ten are classified Tier 2 in
`/memories/repo/v1-consumer-usage.md` — *"operational/observability
commands, not on the critical deploy path"* — confirmed again here:
neither real reference consumer (`haven`, `config-deploy`) invokes any of
them from CI. This doc catalogues what each one actually does (read
directly from v1's real source, `e:\SourcesXYZ\strata\src\strata\commands\
deploy\`), what v2 already has that overlaps, and what's a genuinely new
gap — so a future pass doesn't have to re-derive any of this, and doesn't
accidentally re-build something `strata values` already covers.

**The one finding that matters most**: four of these ten are **already
built in v2**, just under a different command name. `deploy list-values`/
`get-values`/`set-values`/`resolve-values` are deployment-scoped value
inspection — v2's existing `strata values list/get/set/resolve DEPLOYMENT
...` (all 8 phases of `docs/design/values-secrets-command.md`, shipped
2026-09-30) already take a `DEPLOYMENT` argument and do the identical
job. Nothing to design or build there.

## Survey — one-by-one, v1's real behavior

| Command                                          | What it does (read from v1's real source)                                                                                                                                                                                                                                                                                 | v2 overlap                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| ------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `deploy status`                                  | Per-stage live infra status: resource count (`terraform show -json`), output count/keys, last apply serial, cached-output freshness, backend reachability. `--offline` reads cache only.                                                                                                                                  | None. Genuinely new.                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| `deploy history`                                 | Reads `.strata/logs/` for `deploy_run`/`deploy_destroy` events, groups by execution ID, renders a table (when/operation/result).                                                                                                                                                                                          | None — v2 has no `.strata/logs/` execution-history mechanism at all (it has `audit_run.py`'s forward-looking audit *writing*, not a queryable history *reader*).                                                                                                                                                                                                                                                                                                                    |
| `deploy health`                                  | Runs `health_checks` (HTTP GET / TCP connect) against live Terraform outputs, per stage.                                                                                                                                                                                                                                  | **Closest gap** — `DeploymentHealthCheckModel`/`DeploymentStageModel.health_checks` already exist in v2's schema (confirmed: `deployment_model.py`), same shape as `InfraIntegration.destroy()` being built-but-uncalled before this session's `deploy destroy` work. Only the *command* that runs them is missing.                                                                                                                                                                 |
| `deploy lock status` (+ implied acquire/release) | Queries/manages a deployment-level state lock via a pluggable `BaseLockBackend`.                                                                                                                                                                                                                                          | None — matches the already-known, already-deferred locking gap (`deploy-command.md`'s "Structural gotchas from v1" item 6: deliberately deferred, no critical-path evidence). Not a new finding, just the same gap's CLI surface.                                                                                                                                                                                                                                                   |
| `deploy output`                                  | Shows Terraform outputs from a local cache (`<stage>.tf-outputs.json`, written as a side effect of `deploy run`), `--refresh` (live `terraform output -json`), or a durable outputs directory (`--version`/`--all-versions`).                                                                                             | Partial, different shape — v2's new `DeploymentOutputStore` ([ADR-0036](../decisions/0036-cross-run-key-value-output-store.md)) durably persists outputs for *programmatic* cross-step/cross-run consumption (`${output:step.key}` tokens); this v1 command is a *human-facing inspection* CLI with no v2 equivalent. Different consumer, same underlying data — a future `deploy output` could read `DeploymentOutputStore` directly rather than inventing a second cache.         |
| `deploy show`                                    | Prints resolved deployment config: per-remote effective reference (workspace default vs. environment override), workspace/environment files in use, stage list, full resolved environment (secrets masked). Gating is *disclosed*, never applied — a disabled stage is still listed, marked with why a run would skip it. | Partial overlap with `values list`, but adds deployment-wide context (remotes, workspace file, stage list) `values list` doesn't show. Also the one place v1 explicitly treats `enabled`-gating as **visible but inert for display** — relevant precedent if v2 ever builds `enabled` enforcement for `deploy run` (the pre-existing gap noted in the `destroy` work).                                                                                                              |
| `deploy list`                                    | Scans every `kind: deployment` YAML file in the repo (plain `yaml.safe_load`, no service/validation layer) and prints metadata (`name`/`tenant`/`workspace`/`spec.layers` promoted to top level) — built for CI matrix generation.                                                                                        | None. Zero real evidence either repo uses this for matrix generation (not mentioned in either's real workflows per the Tier 1/2 census) — speculative, not confirmed demand.                                                                                                                                                                                                                                                                                                        |
| `deploy plan`                                    | Shows a human-readable change summary (`terraform show -json <plan>`) from an already-saved `.tfplan` file produced by an earlier `deploy run --dry-run`. Entirely offline, no backend calls.                                                                                                                             | **Partially superseded** — v2's own `strata deploy plan` ([deploy-pipeline-stage-commands.md](../design/deploy-pipeline-stage-commands.md), shipped 2026-10-16) computes *and* streams a human-readable diff by default, a strict superset of this for the common case. The one gap left: v1's command can re-view an *already-saved* plan from a prior invocation without recomputing it; v2's `deploy plan` always recomputes. That narrower viewer behavior remains unaddressed. |
| `deploy drift`                                   | Runs `terraform plan -detailed-exitcode` without saving a plan file, classifies changes by severity, persists history.                                                                                                                                                                                                    | None. A whole new subsystem (`DriftController`, `DriftModel`, `DriftHistoryStore`) — confirmed via grep that v2 has zero drift-related code anywhere today.                                                                                                                                                                                                                                                                                                                         |
| `deploy drift history`                           | Reads `.strata/drift/{deployment}.drift.json`, renders past drift-check runs + acknowledged addresses.                                                                                                                                                                                                                    | None — depends entirely on `deploy drift` existing first.                                                                                                                                                                                                                                                                                                                                                                                                                           |
| `deploy drift acknowledge`                       | Suppresses a specific drifted resource address (with a reason), excluding it from future `deploy drift` severity/exit-code calculations.                                                                                                                                                                                  | None — same dependency as above.                                                                                                                                                                                                                                                                                                                                                                                                                                                    |

## Current Design

Nothing designed yet — this pass is purely the survey above. If any of
these gets picked up, each deserves its own focused work doc (matching
`deploy-destroy-command.md`'s precedent) rather than being designed
inline here, since each is an independent CLI surface with its own real
data source (`terraform show`/`.strata/logs/`/`DeploymentOutputStore`/a
lock backend/a drift store).

## Related Decisions

- [deploy-command.md](deploy-command.md) — `deploy run`'s settled design;
  `deploy status`/`show`/`output`/`plan` all read state this command
  already produces (saved plan files, Terraform outputs, resolved
  values) rather than computing anything new.
- [deploy-destroy-command.md](deploy-destroy-command.md) — the sibling
  gap this doc's survey follows the same "what's already built vs. what's
  a real gap" discipline from; `deploy health`'s "model exists, command
  doesn't" finding mirrors that doc's own `InfraIntegration.destroy()`
  finding exactly.
- [deploy-pipeline-stage-commands.md](../design/deploy-pipeline-stage-commands.md)
  — `deploy plan`'s real overlap with v1's differently-scoped command of
  the same name (v2 computes and shows; v1 only ever views an
  already-saved file) — see that doc's own "Naming" section for the full
  resolution.
- [docs/design/values-secrets-command.md](../design/values-secrets-command.md)
  — the already-shipped `strata values` command family that makes
  `deploy list-values`/`get-values`/`set-values`/`resolve-values` a
  non-gap.
- [ADR-0036](../decisions/0036-cross-run-key-value-output-store.md) —
  `DeploymentOutputStore`, the durable backend `deploy output`/`deploy
  show` could read from if ever built, rather than inventing a second
  cache mechanism.
- `/memories/repo/v1-consumer-usage.md` — the real-CI-usage census this
  doc's Tier 2 classification is grounded in.

## Remaining Work / Open Questions

1. **`deploy health`** — the closest gap to "just wire it up": schema
   already carries `DeploymentStageModel.health_checks`, nothing reads
   it. Would need: resolve live Terraform outputs per stage (reuse
   `collect_step_outputs()`'s existing mechanism), resolve each check's
   target (static field or an output key), run an HTTP GET/TCP connect,
   report pass/fail. No consumer evidence yet from either real repo —
   build only if asked, not speculatively.
2. **`deploy list-values`/`get-values`/`set-values`/`resolve-values`** —
   confirmed **not a gap**. `strata values list/get/set/resolve
   DEPLOYMENT ...` already does this job. Do not re-implement under a
   `deploy` subcommand.
3. **`deploy status`/`history`/`output`/`show`** — all read-only
   observability surfaces over data v2 already produces or could
   produce (Terraform state/outputs, `DeploymentOutputStore`, resolved
   values) but none has a CLI today. No real-CI evidence for any of the
   four; candidates for a future pass only if a real consumer asks.
4. **`deploy lock`** — not newly investigated here; same deliberately-
   deferred locking gap `deploy-command.md` already tracks. This doc adds
   only the confirmation that v1's CLI surface for it is three
   subcommands (status/acquire/release-shaped), not new reasoning about
   whether to build it.
5. **`deploy plan`** — **narrowed, not closed**, now that
   [deploy-pipeline-stage-commands.md](../design/deploy-pipeline-stage-commands.md)
   shipped a real `strata deploy plan`/`deploy apply` pair (2026-10-16):
   v2 can now compute, show, save, and later apply a plan across
   separate invocations — the cross-invocation handoff gap this item
   originally pointed at is resolved. What remains unbuilt is narrower:
   v1's specific "offline viewer of an arbitrary already-saved `.tfplan`
   file, without recomputing" behavior — v2's `deploy plan` always
   recomputes. Worth a small follow-up only if a consumer specifically
   wants to re-view a stale saved plan without a fresh `plan()` call; no
   such evidence yet.
6. **`deploy drift`/`drift_history`/`acknowledge_drift`** — a real,
   three-command subsystem with zero v2 equivalent (no model, no
   controller, no persisted history store). The largest single
   not-yet-scoped gap in this doc — would need its own dedicated design
   pass (severity classification scheme, history persistence shape,
   acknowledgement semantics) before any implementation, not a small
   wire-up like `health`. No real consumer evidence yet.

## Changelog

- 2026-10-16: Created. Per direct request to look at "the other possible
  deploy commands" after `deploy run`/`deploy destroy` were investigated.
  Read all ten remaining `commands/deploy/*.py` files in v1 directly.
  Main finding: four of the ten (`list-values`/`get-values`/`set-values`/
  `resolve-values`) are already fully covered by v2's existing `strata
  values` command family — confirmed by checking `values_command.py`
  directly, every subcommand already takes the same `DEPLOYMENT`
  argument v1's deploy-scoped versions do. Second finding: `deploy
  health` is the closest real gap, mirroring `deploy destroy`'s own
  "model/capability already exists, nothing calls it" shape —
  `DeploymentHealthCheckModel`/`DeploymentStageModel.health_checks`
  already exist in v2's schema. Confirmed via grep that `drift`/lock-
  backend machinery has zero v2 equivalent anywhere — the drift trio is
  the largest genuinely unscoped gap found. Investigation only — nothing
  designed in detail, nothing implemented.
- 2026-10-16 (same day, follow-up): Updated the `deploy plan` row,
  Related Decisions entry, and Remaining Work item 5 after
  [deploy-pipeline-stage-commands.md](../design/deploy-pipeline-stage-commands.md)
  graduated to `docs/design/` with a real, fully-implemented `deploy
  plan`/`deploy apply` pair — this doc's own earlier claim that v2 had
  "no cross-invocation handoff mechanism" was now stale and has been
  corrected; the only genuinely remaining gap is v1's narrower
  "view an already-saved plan without recomputing" behavior.
