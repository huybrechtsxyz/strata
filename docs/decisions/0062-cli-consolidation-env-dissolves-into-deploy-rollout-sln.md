# CLI consolidation — `env` dissolves into `deploy`, `rollout`, and `sln`

- Status: completed
- Date: 2026-07-30

## Context and Problem Statement

[ADR-0060](0060-deploy-status-deprecation-and-env-command-clarity.md) documented a
narrower version of this problem one day earlier: `deploy output`/`env output` and
`deploy show`/`env show` answer overlapping questions under two different top-level
groups, and `env output`/`env status`/`env show` sound interchangeable from their
names alone. That ADR's Option 4 (consolidate the three `env` commands into one)
was **explicitly rejected** — "revisit only if Option 3 alone proves insufficient
in practice."

New evidence gathered since then makes the overlap worse, not better:

- **A `deploy drift` group now exists** (`deploy drift run` / `acknowledge` /
  `history`) that duplicates `env drift` outright — both run a non-destructive
  `terraform plan` per stage to detect drift, but `deploy drift run` is a superset
  (severity thresholds, baseline acknowledgement, AI explanation, history) with no
  equivalent reason for `env drift` to keep existing separately.
- **`strata deploy status` is gone from the CLI but the command class survives**
  (`status_deploy_command.py`, confirmed non-dead code — it backs the
  `deploy_status` MCP tool). Its *shape* is a live-outputs/plan-diff hybrid, which
  doesn't match what `env status` (`StatusEnvCommand`: resources, outputs, serial,
  cache-freshness, plus multi-deployment `--all`/`--path` scanning) actually does.
  There is no command today that reports "is this deployment up, and what does it
  look like" for a single deployment without reaching for `env status`.
- **`strata rollout`** was proposed in [ADR-0037](0037-mass-wave-deployment.md) as
  a fleet-level group (`rollout run`, `rollout status`) but has never been wired
  into `cli.py` — `env status --all`/`--path` is filling that gap today with
  workspace-wide scanning logic that belongs to the fleet concern, not the
  single-deployment `env` group.
- **`env info` and `sln status`** already report overlapping workspace identity
  data (`sln status`: `initialized`, `work_path`, solution id, repos, integrations;
  `env info`: solution, profile, version, work path) from two different command
  groups a user has no reason to guess between.

Put together, `env` no longer has a coherent, distinct reason to exist as a
separate top-level group: every one of its six commands (`info`, `output`, `show`,
`status`, `drift`, `doctor`) has a natural, single owner in one of the three groups
that already exist or are already designed (`deploy`, `rollout`, `sln`). This ADR
supersedes ADR-0060's Option 4 rejection with that new evidence and decides to
dissolve `env` entirely rather than patch it again.

## Command Mapping — Old → New

| Old command                                                                       | New command                 | Disposition                                                                                                                                                                                                                                                                                                                                                              |
| --------------------------------------------------------------------------------- | --------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `strata deploy run`                                                               | `strata deploy run`         | Unchanged                                                                                                                                                                                                                                                                                                                                                                |
| `strata deploy destroy`                                                           | `strata deploy destroy`     | Unchanged                                                                                                                                                                                                                                                                                                                                                                |
| `strata deploy plan`                                                              | `strata deploy plan`        | Unchanged                                                                                                                                                                                                                                                                                                                                                                |
| `strata deploy show` + `strata env show`                                          | `strata deploy show`        | **Merge** — adds resolved vars/secrets/features/overrides (today's `env show` payload) to the existing remote-versions/workspace payload                                                                                                                                                                                                                                 |
| `strata deploy output` + `strata env output`                                      | `strata deploy output`      | **Merge** — unify flag surface (cache-first `--refresh`/`--version`/`--all-versions` from `deploy output`, plus `--provisioner`/`--raw`/`--json` from `env output`). Implementation note: the single-value filter kept `deploy output`'s existing `--key NAME` rather than adding a second, redundant `--name NAME` flag — `--raw` requires `--key` instead of `--name`. |
| *(removed)* `strata deploy status` + `strata env status` (single-deployment mode) | `strata deploy status`      | **Revive** — re-registered in `cli_deploy.py`, takes `-f FILE`, behavior sourced from `StatusEnvCommand` (resources, outputs, serial, cache-freshness) rather than the old plan-diff hybrid, which is redundant with `deploy plan`                                                                                                                                       |
| `strata deploy health`                                                            | `strata deploy health`      | Unchanged                                                                                                                                                                                                                                                                                                                                                                |
| `strata deploy drift ...` + `strata env drift`                                    | `strata deploy drift ...`   | **Merge** — `env drift`'s single-check behavior is a strict subset of `deploy drift run`; drop `env drift`, keep the existing subgroup (`run`/`acknowledge`/`history`)                                                                                                                                                                                                   |
| `strata deploy history`                                                           | `strata deploy history`     | Unchanged                                                                                                                                                                                                                                                                                                                                                                |
| `strata deploy list`                                                              | `strata deploy list`        | Unchanged                                                                                                                                                                                                                                                                                                                                                                |
| `strata deploy lock ...`                                                          | `strata deploy lock ...`    | Unchanged                                                                                                                                                                                                                                                                                                                                                                |
| *(not yet implemented)*                                                           | `strata rollout run`        | **New** — mass/wave deployment per ADR-0037, future                                                                                                                                                                                                                                                                                                                      |
| `strata env status --all` / `strata env status --path DIR`                        | `strata rollout status`     | **Move** — multi-deployment scanning is a fleet concern per ADR-0037, not a single-deployment `env` concern                                                                                                                                                                                                                                                              |
| `strata sln init`                                                                 | `strata sln init`           | Unchanged                                                                                                                                                                                                                                                                                                                                                                |
| `strata env info`                                                                 | `strata sln status`         | **Absorb** — `sln status` already reports overlapping workspace-identity data; extend it with the fields unique to `env info`. Implementation note: `sln status` already reported the active profile name — only `strata version` was actually missing, so that's the only field added.                                                                                  |
| `strata env doctor`                                                               | `strata sln doctor`         | **Move** — workspace-level tooling/health check belongs beside `sln status`, not under a per-deployment group                                                                                                                                                                                                                                                            |
| `strata sln clean`                                                                | `strata sln clean`          | Unchanged                                                                                                                                                                                                                                                                                                                                                                |
| `strata sln update`                                                               | `strata sln update`         | Unchanged                                                                                                                                                                                                                                                                                                                                                                |
| `strata sln deployment ...`                                                       | `strata sln deployment ...` | Unchanged                                                                                                                                                                                                                                                                                                                                                                |
| `strata sln export`                                                               | `strata sln export`         | Unchanged                                                                                                                                                                                                                                                                                                                                                                |

**Zero commands are left behind.** Every `env` subcommand maps to exactly one new
home. The `env` group is deleted outright — no top-level group is added purely to
hold overflow, since `deploy`, `rollout` (design already exists per ADR-0037), and
`sln` already cover every case.

## Decision Drivers

- ADR-0060 already tried the smaller fix (doc clarity + hiding a dead command) and
  the overlap grew anyway (`deploy drift` duplicating `env drift`) — clarity notes
  don't stop new duplicate commands from being added under the wrong group.
- Every `env` command answers a question that is really about *one* of: a specific
  deployment's execution/state (`deploy`), the whole fleet (`rollout`), or the
  workspace itself (`sln`) — never something `env` uniquely owns.
- No backwards-compatibility constraint applies here: `strata` has no external
  consumers beyond the repos and pipelines this team already controls, so a
  direct breaking change is acceptable — we absorb the fallout in the same
  change rather than carrying a deprecation shim.
- Minimize the number of top-level groups — reuse `deploy`/`rollout`/`sln` rather
  than inventing a fourth home for orphaned commands.

## Considered Options

### Option 1 — Keep patching `env` incrementally (continue ADR-0060's Option 3 path)

Add more clarity docs, hide more dead commands, but leave `env` as a standing
group.

- Con: doesn't address the root cause — new commands keep landing in the wrong
  group (`deploy drift` proves this happened again in two days). The group keeps
  needing "which one do I use" documentation forever instead of the ownership
  being obvious from the group name.

**Rejected.**

### Option 2 — Merge `env` into `deploy` only, keep `sln`/`rollout` out of scope

- Con: `env info` and `env doctor` are workspace-level, not deployment-level —
  forcing them into `deploy` (which always requires `-f FILE` semantics for most
  of its commands) is a worse fit than `sln`, which already has no-file-required,
  workspace-wide commands.

**Rejected.**

### Option 3 — Dissolve `env` completely across `deploy` / `rollout` / `sln` (RECOMMENDED)

Every command moves to its single natural owner per the mapping table above.
`rollout status` absorbs the multi-deployment scanning that `env status
--all`/`--path` does today; `rollout run` remains a future ADR-0037 deliverable,
unaffected by this decision's timeline.

- Pro: each of the three surviving groups has an unambiguous scope: `deploy` =
  one deployment's lifecycle, `rollout` = the fleet, `sln` = the workspace itself.
- Pro: fixes the root cause instead of re-documenting around it — no group is left
  as a grab-bag that new commands can be dropped into without a naming decision.
- Pro: `deploy status`'s revival closes the actual capability gap left by ADR-0060
  (a single-deployment "what's live" command with no plan-diffing overlap with
  `deploy plan`).
- Con: six commands move in one change, three of them merging into existing
  commands with expanded flag surfaces — accepted since there's no backwards-
  compatibility requirement forcing a staged rollout.

**This is the winning option.**

## Decision Outcome

Ship **Option 3**. The `env` command group is deleted. Its six commands are
redistributed per the mapping table: three merge into existing `deploy` commands
(`show`, `output`, `drift`), one is revived under `deploy` with corrected behavior
(`status`), one moves to the not-yet-implemented `rollout` group's `status`
command, and two move to `sln` (`info` → absorbed into `status`, `doctor` →
new `sln doctor`).

### Consequences

- Good: `deploy`, `rollout`, and `sln` each have a single, describable scope —
  "this deployment", "the fleet", "the workspace" — with no fourth group
  competing for the same territory.
- Good: closes the actual gap ADR-0060 left open (no clean single-deployment
  "what's live" command) by reviving `deploy status` with the right behavior
  instead of its old plan-diff hybrid.
- Good: `env drift` duplication is resolved by deletion rather than by a doc note
  telling users which of two near-identical commands to prefer.
- Good: no orphaned commands and no new top-level group invented to hold
  leftovers.
- Neutral: `rollout run` itself is unaffected — it remains a future deliverable
  per ADR-0037; only `rollout status` (absorbing `env status --all`/`--path`) is
  newly in scope here.
- Bad (accepted): every `env` invocation in scripts/pipelines/docs breaks the
  moment this ships — there is no deprecation window. Accepted because this ADR
  supersedes ADR-0060's non-breaking constraint: there's no external consumer to
  protect, so we cut over in one change and fix up the (small, known) set of
  internal call sites directly instead of carrying a compatibility shim.

### Migration strategy — direct cutover, no deprecation shim

Unlike ADR-0060 (which had to preserve backwards compatibility), this ADR makes
a clean break in a single change:

1. **No grace period, no deprecation warnings.** `env` and its six subcommands
   are deleted outright — they do not keep working, and there is no interim
   release where both old and new names resolve.
2. **Delete, don't hide.** `env` is removed from `cli.py` entirely (group
   registration, `cli_env.py`, and `src/strata/commands/envs/*`) in the same
   change that adds the new/merged commands — not staged behind `hidden=True`.
   `DoctorEnvCommand` is moved (not deleted) to `sln doctor`; the other five
   command classes are deleted once their behavior has a new home.
3. **Fix all internal call sites in the same change.** Grep the repo for
   `env info`, `env output`, `env show`, `env status`, `env drift`, `env doctor`
   (docs, scripts, tests, CI pipelines, MCP tool registrations) and update every
   hit as part of this work — there is no follow-up release relied upon to catch
   stragglers.
4. **MCP layer.** Update or remove any MCP tools referencing `env_*` commands
   (`src/strata/mcp/server.py`) in the same change — `env_info`, `env_output`,
   `env_show`, `env_status`, `env_drift`, `env_doctor` must be gone or repointed
   before this ships, not left for a later phase.

## Detailed Design

### `deploy show` (merge)

- File: `src/strata/commands/deploy/show_deploy_command.py` (`ShowDeployCommand`).
- Add the payload currently produced by `ShowEnvCommand`
  (`src/strata/commands/envs/show_env_command.py`) — meta, properties, values,
  overrides, stages — as additional sections alongside the existing remote
  versions/workspace metadata.
- Add `env show`'s `--stage NAME` option (filters secret visibility to a stage's
  allowlist) to `deploy show`'s flag surface — it does not exist there today.
- `cli_deploy.py`: add `@click.option("--stage", ...)` to the `deploy_show`
  Click command, threaded into `ShowDeployCommand`.

### `deploy output` (merge)

- File: `src/strata/commands/deploy/output_deploy_command.py`.
- Keep `deploy output`'s existing cache-first default (`--refresh` to go live,
  `--version`/`--all-versions` for stored artifacts).
- Add `env output`'s always-live scripting flags that `deploy output` lacks:
  `--name NAME` (single value), `--provisioner NAME` (filter by provisioner),
  `--raw` (bare value, requires `--name`), `--json` (bypass the strata envelope).
  `--refresh` becomes the equivalent of what `env output` did unconditionally.
- `cli_deploy.py`: add the four options above to the `deploy_output` Click
  command.

### `deploy status` (revive with corrected behavior)

- File: `src/strata/commands/deploy/status_deploy_command.py`
  (`StatusDeployCommand`) — re-register in `cli_deploy.py` as
  `@deploy.command(name="status", ...)`.
- **Do not reuse the old plan-diffing `--plan` mode** — that responsibility
  already belongs to `deploy plan` (reads the last saved `.tfplan`). Replace
  `StatusDeployCommand`'s implementation with the logic from
  `StatusEnvCommand` (`src/strata/commands/envs/status_env_command.py`):
  per-stage resources, outputs, serial, and cache-freshness, queried live from
  the Terraform backend.
- Carry over flags: `--stage NAME` (single stage), `--offline` (cached data
  only, no backend calls). Do **not** carry over `--path`/`--all` — those move
  to `rollout status` (see below).
- Update the `deploy_status` MCP tool docstring
  (`src/strata/mcp/server.py`) to reflect the corrected behavior — ADR-0060
  flagged this docstring as already misleading before this change; it must be
  fixed as part of this revival, not deferred again.

### `deploy drift` (merge, delete `env drift`)

- No code changes needed to `deploy drift run`/`acknowledge`/`history` — they
  already superset `env drift`'s behavior (severity thresholds, baseline
  acknowledgement, AI explanation, run history vs. a single unaugmented check).
- Delete the `env drift` command and `DriftEnvCommand`
  (`src/strata/commands/envs/drift_env_command.py`) outright, in the same change
  — no deprecation warning, no interim period where both exist.

### `rollout status` (new, absorbs `env status --all`/`--path`)

- Depends on the `rollout` group existing per ADR-0037 (`rollout run` /
  `rollout status` design, not yet wired into `cli.py`). If `rollout run` has not
  shipped by the time this ADR is implemented, `rollout status` can still land on
  its own — `cli.py` gains a new `rollout` group with only `status` registered
  initially.
- Behavior: workspace-wide (`--path DIR`) or full-solution (`--all`) scan of
  deployment manifests, summarizing each — the exact logic `StatusEnvCommand`
  runs today when `path`/`all_deployments` is set. Extract that branch into a
  dedicated command class (e.g. `StatusRolloutCommand`) rather than keeping it
  as a side-mode of the single-deployment `deploy status`.
- Flags: `--path DIR`, `--all` (mutually exclusive, same semantics as today's
  `env status`), plus whatever ring/wave/promotion filters ADR-0037 defines for
  `rollout run` if that lands first (kept independent otherwise).

### `sln status` (absorb `env info`)

- File: `src/strata/commands/status/show_status_command.py` (`StatusCommand`).
- Add the two fields `env info` reports that `sln status` doesn't today: active
  profile name and strata version. Everything else (`initialized`, `work_path`,
  solution id, repos, integrations) already overlaps.
- No new flags needed — `sln status` already works with and without an
  initialized workspace, matching `env info`'s documented behavior.

### `sln doctor` (new, moved from `env doctor`)

- Move `src/strata/commands/envs/doctor_env_command.py` (`DoctorEnvCommand`) to
  a new `src/strata/commands/sln/doctor_sln_command.py`, registered as
  `sln_group.add_command(doctor_command, name="doctor")` in `cli_sln.py`.
- Carry over all existing flags unchanged: `--file PATH` (optional, enables
  requirement-level derivation for tools), `--category
  {runtime,workspace,tools,config,auth}`, `--deep` (slow checks: backend
  reachability, auth validation), `--ai` (AI analysis of failed checks).
- No behavior change — this is a pure relocation. `env doctor`'s `--file` option
  already makes it deployment-aware when needed, so nothing is lost by hosting
  it under the workspace-level `sln` group.

## Implementation Plan

One change, no staged rollout — build the new/merged commands and delete `env`
in the same PR.

### 1. Build the new/merged commands

- `deploy show`: add resolved-environment payload (meta, properties, values,
  overrides, stages) from `ShowEnvCommand` + `--stage NAME` flag.
- `deploy output`: add `--name`/`--provisioner`/`--raw`/`--json` flags from
  `OutputEnvCommand`.
- `deploy status`: register in `cli_deploy.py`, rewrite
  `StatusDeployCommand`'s implementation to source from `StatusEnvCommand`
  (resources, outputs, serial, cache-freshness; `--stage`/`--offline`), fix the
  `deploy_status` MCP tool docstring.
- `sln status`: add active profile + strata version fields from `InfoEnvCommand`.
- `sln doctor`: new file `src/strata/commands/sln/doctor_sln_command.py`, move
  `DoctorEnvCommand`'s code, register in `cli_sln.py`.
- `rollout status`: new command (and `rollout` group if not already present),
  extracting the `--path`/`--all` scanning branch out of `StatusEnvCommand`
  into a dedicated `StatusRolloutCommand`.

### 2. Delete `env` in the same change

- Remove the `env` group registration from `cli.py`, delete `cli_env.py`, and
  delete every `src/strata/commands/envs/*` command class whose behavior has
  migrated (`InfoEnvCommand`, `OutputEnvCommand`, `ShowEnvCommand`,
  `StatusEnvCommand`, `DriftEnvCommand`). `DoctorEnvCommand` is moved, not
  deleted (see above).
- Remove or repoint any `env_*` MCP tool registrations in
  `src/strata/mcp/server.py` (`env_info`, `env_output`, `env_show`,
  `env_status`, `env_drift`, `env_doctor`).
- Update every doc/script reference to `env ...` commands in the same change:
  `docs/help/deployment.md`, `docs/guides/deploying.md`,
  `docs/platform/commands.md`, and any other hit from a repo-wide grep for
  `env info|env output|env show|env status|env drift|env doctor`.
- Update or delete tests under `tests/strata/` that exercise `cli_env.py` /
  `commands/envs/*`, moving relevant coverage to the new command locations.

## Implementation Notes

Implemented in full in a single change, as planned. Deviations found during
implementation (kept intentionally, not treated as bugs):

- **`deploy output`** kept the existing `--key NAME` flag as the single-value
  filter instead of adding a second, near-duplicate `--name NAME` flag —
  `env output`'s `--name` and `deploy output`'s pre-existing `--key` served the
  identical purpose, and carrying both would have reintroduced exactly the kind
  of "two flags, one meaning" confusion this ADR exists to remove. `--raw`
  requires `--key`; `--provisioner` and `--json` were added as designed.
- **`deploy show`** merges the resolved-environment payload under a new
  `environment_detail` JSON key (`name`, `labels`, `annotations`, `properties`,
  `custom`, `variables[]`, `secrets[]`, `features[]`, `overrides{}`), alongside
  a new top-level `stages[]` key — additive, no existing `deploy show` keys
  changed shape. The ported `--stage NAME` flag is stored but does not filter
  secret visibility, carrying forward a pre-existing no-op in `env show`
  unchanged; fixing that behavior is out of scope for this ADR.
- **`sln status`** turned out to already report the active profile name before
  this change — code review found only `strata version` was actually missing
  from `env info`'s field set, not both fields as originally assumed. Only
  `version` was added.
- **`sln doctor`** is a straight file move plus a rename for consistency:
  `DoctorEnvCommand` → `DoctorSlnCommand` (`src/strata/commands/sln/doctor_sln_command.py`),
  `OPERATION` changed from `env_doctor` to `sln_doctor`. No behavior change.
- **`rollout status`** landed as designed, in a new `src/strata/commands/rollout/`
  package (`StatusRolloutCommand`) plus `cli_rollout.py`. `rollout run` was
  **not** implemented — it remains a future ADR-0037 deliverable, unaffected by
  this change, exactly as the fallback plan above anticipated.
- **`deploy_status` MCP tool** was updated with a corrected docstring and a new
  `offline` parameter, matching the revived command's flag surface.
- **No MCP tools referenced `env_*` operations** — the only MCP tool touched
  was `deploy_status` (already existed, pointed at the wrong command shape).
- **Docs updated:** `docs/platform/commands.md` (env section replaced by a
  `rollout` section; `sln`/`deploy` sections updated), `docs/help/deployment.md`,
  `docs/help/ai_agent.md`, `docs/guides/deploying.md`, `docs/guides/features.md`,
  `docs/platform/workflow.md`. `docs/guides/environment-command-group.md` was
  replaced by `docs/guides/deployment-inspection.md` (and `docs/index.rst`'s
  toctree updated to match) rather than edited in place, since its entire
  premise (a dedicated `env` guide) no longer applies.
- **Tests:** `tests/strata/commands/test_commands_env.py` deleted; its
  multi-deployment scanning coverage moved to a new
  `tests/strata/commands/test_commands_rollout.py`; `tests/strata/commands/test_cli.py`
  updated (`env` removed / `rollout` added to the top-level group assertion,
  `doctor` added to the `sln` subcommand assertion).
- **Verification:** full test suite green after implementation — 5036 passed,
  16 skipped, 0 failed.

## References

- [ADR-0060: Deploy status deprecation and env command clarity](0060-deploy-status-deprecation-and-env-command-clarity.md) —
  the narrower predecessor to this ADR; its Option 4 rejection is superseded here
  with new evidence (the `deploy drift`/`env drift` duplication that emerged
  after it was written).
- [ADR-0037: Fleet rollout — multi-deployment wave execution](0037-mass-wave-deployment.md) —
  source of the `rollout run`/`rollout status` design that `rollout status`
  depends on for its group placement. `rollout run` remains unimplemented.
- [ADR-0058: Cross-deployment dependency gating](0058-cross-deployment-dependency-gating.md) —
  established `env status` (now `deploy status`) as the most reliable
  cross-machine "is it deployed" signal; that property carries over to the
  revived `deploy status`.
- [ADR-0008: Infrastructure drift detection](0008-infrastructure-drift-detection.md) —
  original design for drift checking, now consolidated solely under
  `deploy drift`.
