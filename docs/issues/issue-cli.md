<!-- PARENT ISSUE — strata env Command Group
  This is the umbrella design doc for the `strata env` command group.
  Each subcommand has its own child issue file with detailed design.

  Child Issues:
    x-strata-cli-info.md    — strata env info (context summary)
    x-strata-cli-doctor.md  — strata env doctor (health check)
    x-strata-cli-output.md  — strata env output (terraform outputs)
    x-strata-cli-state.md   — strata env state (terraform state inspection)
    x-strata-cli-status.md  — strata env status (multi-deployment overview)
    x-strata-cli-drift.md   — strata env drift (drift detection)

  Origin (z-strata files that are absorbed by this design):
    z-strata-commands.md  — Authoritative command surface (build/deploy/env split)
    z-strata-doctor.md    — Environment health check
    z-strata-whoami.md    — Current context summary
    z-strata-status.md    — Multi-environment aggregate state
    z-strata-output.md    — Terraform outputs
    z-strata-drift.md     — Per-deployment drift detection

  Resolution: All six origin files propose commands that answer variations of
              "tell me about my environment". This parent unifies them under
              a single `strata env` command group with clear subcommand
              boundaries.
-->

# Parent Issue: `strata env` — Unified Environment Inspection Group

## Problem

Six separate design files propose inspection commands that overlap significantly:

| Child issue            | Proposed command      | Core question                  | Overlap with                   |
| ---------------------- | --------------------- | ------------------------------ | ------------------------------ |
| `z-strata-doctor.md`   | `strata doctor`       | Is my local env healthy?       | whoami (auth + tools)          |
| `z-strata-whoami.md`   | `strata whoami`       | What context am I in?          | doctor (auth + integrations)   |
| `z-strata-status.md`   | `strata status`       | What's deployed across envs?   | drift (terraform plan)         |
| `z-strata-output.md`   | `strata output`       | What are live TF outputs?      | commands (env output)          |
| `z-strata-drift.md`    | `strata deploy drift` | Has infra drifted?             | status --full (terraform plan) |
| `z-strata-commands.md` | `strata env *`        | Build/deploy/env surface split | output, status                 |

If implemented independently, users face 5+ commands (`doctor`, `whoami`, `status`, `output`, `deploy drift`) that all surface overlapping information in different shapes. Discoverability suffers — operators won't know which command to run.

## Decision

Consolidate all environment inspection into **one `strata env` command group**. Each child issue maps to a subcommand with a clear, non-overlapping scope.

## Proposed Command Surface

```
strata env                              # (alias for strata env info)

strata env info                         # Context + identity (absorbs whoami)
strata env doctor                       # Full health check with fix hints
strata env status                       # Multi-deployment state overview
strata env output                       # Live terraform outputs
strata env state list                   # Terraform state list
strata env state show <resource>        # Terraform state show
strata env drift                        # Per-deployment drift detection
```

### Subcommand Boundaries

#### `strata env info` (absorbs `z-strata-whoami.md`)

Answers: **"Where am I and what's active?"** — read-only, instant, no external calls.

```
strata env info

Solution:   haven (7de99a3b)
Profile:    prd ✔ (active)
Config:     config/cfg-haven.yaml
Version:    strata v0.0.4
Work path:  /home/user/haven
```

- Shows solution, active profile, config file, strata version, work path
- No integration checks, no auth checks — that's doctor's job
- `INIT_REQUIRED = True` (needs solution.json)

#### `strata env doctor` (absorbs `z-strata-doctor.md`)

Answers: **"Is everything working?"** — runs checks against runtime, tools, auth, workspace, config.

```
strata env doctor                       # Full check (all categories)
strata env doctor --category runtime    # Single category
strata env doctor --fix                 # Auto-fix where possible (future)
strata env doctor --output json         # Machine-readable
```

- Five check categories: runtime, tools, auth, workspace, config
- Each check returns status + fix_hint
- `INIT_REQUIRED = False` — must work in broken/uninitialized workspaces
- Detailed design: see `z-strata-doctor.md`

#### `strata env status` (absorbs `z-strata-status.md`)

Answers: **"What's the state of my deployments?"** — aggregate multi-environment view.

```
strata env status                               # All deployments, quick mode
strata env status --file deploy/deploy-prd.yaml  # One deployment
strata env status --full                         # Include drift detection
strata env status --output json                  # CI/dashboard format
```

- Quick mode (default): checks build output exists, state file exists, timestamps
- Full mode: runs `terraform plan` per stage (same operation as drift, but aggregated)
- States: `deployed`, `drifted`, `unknown`, `undeployed`, `stale-build`
- `INIT_REQUIRED = True`
- Detailed design: see `z-strata-status.md`

#### `strata env output` (absorbs `z-strata-output.md`, supersedes `strata deploy status`)

Answers: **"What are the live values from terraform?"**

```
strata env output -f deploy-prd.yaml            # All outputs, table format
strata env output -f deploy-prd.yaml --name ip  # Single output value
strata env output -f deploy-prd.yaml --raw      # Raw value (scripting)
strata env output -f deploy-prd.yaml --json     # Raw JSON passthrough
```

- Wraps `tofu output -json` in the resolved build directory
- Groups by provisioner when multiple exist
- Sensitive outputs passed through as-is (never unwrapped)
- `INIT_REQUIRED = True`
- Detailed design: see `z-strata-output.md`

#### `strata env state list` / `strata env state show` (from `z-strata-commands.md`)

Answers: **"What resources does terraform know about?"**

```
strata env state list -f deploy-prd.yaml
strata env state show -f deploy-prd.yaml <resource>
```

- Thin wrappers around `tofu state list` / `tofu state show`
- Hard-fail with clear message when no state exists

#### `strata env drift` (absorbs `z-strata-drift.md`)

Answers: **"Has infrastructure diverged from config?"**

```
strata env drift -f deploy-prd.yaml             # Per-deployment drift scan
strata env drift -f deploy-prd.yaml --output json
```

- Runs `terraform plan` per stage, reports delta
- Exit code 3 when drift detected (validation failure convention)
- Equivalent to `strata env status --full --file X` but with detailed per-resource output
- Future: `--remediate` flag for auto-apply
- Detailed design: see `z-strata-drift.md`

## What This Replaces

| Old / proposed command | New command                               | Notes                                           |
| ---------------------- | ----------------------------------------- | ----------------------------------------------- |
| `strata whoami`        | `strata env info`                         | Lighter scope, no health checks                 |
| `strata doctor`        | `strata env doctor`                       | Moved under env group                           |
| `strata status`        | `strata env status`                       | Moved under env group                           |
| `strata output`        | `strata env output`                       | Already proposed in z-strata-commands.md        |
| `strata deploy drift`  | `strata env drift`                        | Moved from deploy to env (read-only inspection) |
| `strata deploy status` | `strata env output` + `strata env status` | Deprecated, split into two clear commands       |

## What Stays Unchanged

| Command               | Purpose                            | Why it stays                                   |
| --------------------- | ---------------------------------- | ---------------------------------------------- |
| `strata sln status`   | Workspace health (repos, profiles) | Solution lifecycle, not environment inspection |
| `strata tools status` | Tool availability check            | Subset of doctor; may deprecate later          |
| `strata deploy plan`  | Terraform plan (pre-apply preview) | Deploy lifecycle, write-intent command         |
| `strata deploy run`   | Terraform apply                    | Deploy lifecycle                               |
| `strata build plan`   | Artifact diff                      | Build lifecycle                                |

## Architecture

```
commands/
  cli_env.py                  ← Click group registration
  env/
    __init__.py
    info_command.py            ← EnvInfoCommand (BaseCommand)
    doctor_command.py          ← DoctorCommand (BaseCommand, INIT_REQUIRED=False)
    status_command.py          ← EnvStatusCommand (BaseCommand)
    output_command.py          ← EnvOutputCommand (BaseCommand)
    state_command.py           ← EnvStateCommand (BaseCommand)
    drift_command.py           ← EnvDriftCommand (BaseCommand)
controllers/
  env_info_controller.py       ← Gathers solution + profile + version context
  doctor_controller.py         ← Orchestrates check categories
  env_status_controller.py     ← Multi-deployment state aggregation
  env_output_controller.py     ← Terraform output resolution
  env_drift_controller.py      ← Per-deployment drift detection
```

## Implementation Order

1. **`strata env info`** — simplest, no external calls, validates the group wiring
2. **`strata env doctor`** — high user value, standalone (INIT_REQUIRED=False)
3. **`strata env output`** — already partially implemented as `strata deploy outputs`
4. **`strata env state list/show`** — thin wrappers, low risk
5. **`strata env status`** — depends on output + state infrastructure
6. **`strata env drift`** — depends on deploy plan infrastructure

## Acceptance Criteria

- [ ] `strata env` group registered in CLI with `--help` describing all subcommands
- [ ] `strata env info` shows solution, profile, version, work path
- [ ] `strata env doctor` runs 5 check categories with ✅/❌ output and fix hints
- [ ] `strata env output` retrieves terraform outputs with `--name`, `--raw`, `--json`
- [ ] `strata env state list` and `strata env state show` wrap terraform state commands
- [ ] `strata env status` shows aggregate deployment state (quick + full modes)
- [ ] `strata env drift` detects per-deployment drift with exit code 3 on divergence
- [ ] `strata deploy status` prints deprecation warning pointing to `env output` / `env status`
- [ ] `strata whoami` is not implemented (absorbed by `env info`)
- [ ] All commands support `--output json` for CI integration
- [ ] `--help` output reflects the unified env group structure

## Child Issues

| #   | Child issue file                                 | Command                      | Impl. order                 |
| --- | ------------------------------------------------ | ---------------------------- | --------------------------- |
| 1   | [x-strata-cli-info.md](x-strata-cli-info.md)     | `strata env info`            | 1 — establishes env group   |
| 2   | [x-strata-cli-doctor.md](x-strata-cli-doctor.md) | `strata env doctor`          | 2 — standalone, high value  |
| 3   | [x-strata-cli-output.md](x-strata-cli-output.md) | `strata env output`          | 3 — partially exists        |
| 4   | [x-strata-cli-state.md](x-strata-cli-state.md)   | `strata env state list/show` | 4 — thin wrappers           |
| 5   | [x-strata-cli-status.md](x-strata-cli-status.md) | `strata env status`          | 5 — depends on output/state |
| 6   | [x-strata-cli-drift.md](x-strata-cli-drift.md)   | `strata env drift`           | 6 — depends on deploy plan  |

## Origin Cross-Reference (z-strata files)

| Origin file            | Status                                                            | Absorbed into            |
| ---------------------- | ----------------------------------------------------------------- | ------------------------ |
| `z-strata-commands.md` | **Aligned** — env group matches; build/deploy split adopted as-is | Parent scope             |
| `z-strata-doctor.md`   | **Absorbed** — becomes `strata env doctor`                        | `x-strata-cli-doctor.md` |
| `z-strata-whoami.md`   | **Absorbed** — becomes `strata env info`                          | `x-strata-cli-info.md`   |
| `z-strata-status.md`   | **Absorbed** — becomes `strata env status`                        | `x-strata-cli-status.md` |
| `z-strata-output.md`   | **Superseded** — becomes `strata env output`                      | `x-strata-cli-output.md` |
| `z-strata-drift.md`    | **Absorbed** — becomes `strata env drift`                         | `x-strata-cli-drift.md`  |
