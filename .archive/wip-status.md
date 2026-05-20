# Feature: `strata status` — Multi-Environment State Overview

## Summary

Implement `strata status` as a top-level command that shows the aggregate state of all (or one) deployment environments in a workspace — answering "what's deployed, what's drifted, what's unknown" in a single glance.

**Goal:** Give operators a dashboard view across all environments without running `strata diff` or `strata deploy status` against each deployment file individually.

---

## Motivation

- Current `strata deploy status -f FILE` shows Terraform outputs for ONE deployment — useful but narrow
- Current `strata sln status` shows workspace health (repos, profiles, integrations) — not deployment state
- Operators managing 3-8 environments need a single command: "Are my environments in sync or not?"
- Issue #29 calls this out: `strata status — show current state of each environment (deployed / drifted / unknown)`
- Without this, teams rely on CI dashboards or manual `terraform plan` in each stage to know if drift exists

---

## Proposed Design

### Command Interface

```bash
strata status                           # All deployments in workspace
strata status --file deploy/deploy-prd.yaml  # One specific deployment
strata status --quick                   # Skip drift detection (fast: just check if state exists)
strata status --output json             # Machine-readable for CI/dashboards
```

### Architecture

```
commands/
  cli_status.py              ← Click wiring (top-level command, NOT under deploy/sln)
  status/
    __init__.py
    show_status_command.py   ← Already exists — RENAME to workspace_status_command.py
    env_status_command.py    ← NEW: EnvStatusCommand extends BaseCommand
controllers/
  status_controller.py       ← NEW: Orchestrates per-deployment state checks
```

**Layer rules:**
- `EnvStatusCommand` → `StatusController` → existing deployer infrastructure
- `INIT_REQUIRED = True` — needs solution.json to find all deployment files
- Reuses `TerraformDeployer.plan()` for drift detection (already exists)

### Relationship to existing commands

| Command                        | Purpose                                            | Stays?          |
| ------------------------------ | -------------------------------------------------- | --------------- |
| `strata sln status`            | Workspace health (repos, profiles, tools)          | ✅ Unchanged     |
| `strata deploy status -f FILE` | Live Terraform outputs for one deployment          | ✅ Unchanged     |
| `strata status` (NEW)          | Aggregate environment state across all deployments | ✅ New top-level |

---

### State Model

Each deployment+stage combination has one of these states:

| State         | Meaning                                                          | How detected                                         |
| ------------- | ---------------------------------------------------------------- | ---------------------------------------------------- |
| `deployed`    | Infrastructure matches config — no drift                         | `terraform plan` shows 0 changes                     |
| `drifted`     | Infrastructure exists but differs from config                    | `terraform plan` shows N changes                     |
| `unknown`     | Cannot determine state (no backend, no state file, auth failure) | `terraform plan` fails or state doesn't exist        |
| `undeployed`  | Backend reachable but state is empty/doesn't exist               | State file missing or `terraform show` returns empty |
| `stale-build` | Build output is older than config source files                   | Compare file mtimes: config YAML vs `.strata/build/` |

### Discovery: Finding Deployments

The controller discovers deployments via the solution configuration:

1. Read `solution.json` → get registered profiles
2. For active profile → get deployment file paths (from profile `configfile_paths` or scan `deploy/` directory)
3. For each deployment file → load deployment model → enumerate stages
4. Return list of `(deployment_name, stage_name, state)` tuples

**Fallback:** If no profile-based discovery, scan `deploy/**/*.yaml` for files with `kind: deployment`.

---

### Quick Mode vs. Full Mode

| Mode                                     | What it checks                                                 | Speed                             |
| ---------------------------------------- | -------------------------------------------------------------- | --------------------------------- |
| `--quick` (default for console)          | Build output exists? State file exists? File timestamps match? | < 2 seconds                       |
| Full (`--full` or `--output json` in CI) | Actually runs `terraform plan` per stage to detect drift       | 10-60 seconds depending on stages |

**Quick mode checks:**
1. Does `.strata/build/<deployment>/<stage>/` exist? (if no → `undeployed` or `stale-build`)
2. Is any config file newer than build output? (if yes → `stale-build`)
3. Does Terraform state exist in backend? (fast: `terraform state list` or check state file)

**Full mode adds:**
4. `terraform plan -detailed-exitcode` per stage (exit 0 = no changes, exit 2 = changes exist)

---

### Output Format (Console)

```
📊 strata status
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  Deployment         Stage            State         Details
  ─────────────────  ───────────────  ────────────  ─────────────────────
  haven_deploy_prd   infrastructure   ✅ deployed    0 changes
  haven_deploy_stg   infrastructure   ⚠️  drifted    3 resources changed
  haven_deploy_dev   infrastructure   ❓ unknown     Auth failed: az login required

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Summary: 1 deployed, 1 drifted, 1 unknown
```

With `--quick`:

```
📊 strata status (quick)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  Deployment         Stage            State           Details
  ─────────────────  ───────────────  ──────────────  ──────────────────────
  haven_deploy_prd   infrastructure   ✅ deployed      Build current
  haven_deploy_stg   infrastructure   ⚠️  stale-build   Config newer than build
  haven_deploy_dev   infrastructure   ❓ undeployed    No build output found

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Summary: 1 deployed, 1 stale, 1 undeployed
Run with --full for drift detection via terraform plan.
```

### Output Format (JSON)

```json
{
  "success": true,
  "mode": "full",
  "summary": {"deployed": 1, "drifted": 1, "unknown": 1, "undeployed": 0, "stale_build": 0},
  "deployments": [
    {
      "name": "haven_deploy_prd",
      "file": "deploy/deploy-prd.yaml",
      "stages": [
        {
          "name": "infrastructure",
          "state": "deployed",
          "changes": 0,
          "detail": null
        }
      ]
    },
    {
      "name": "haven_deploy_stg",
      "file": "deploy/deploy-stg.yaml",
      "stages": [
        {
          "name": "infrastructure",
          "state": "drifted",
          "changes": 3,
          "detail": "2 to change, 1 to add"
        }
      ]
    }
  ]
}
```

---

### Exit Codes

| Code | Meaning                                              |
| ---- | ---------------------------------------------------- |
| 0    | All environments deployed, no drift                  |
| 1    | System/execution error (auth failure, command crash) |
| 3    | Drift detected or environments not in desired state  |

Exit code 3 for drift enables CI usage: `strata status --full || echo "Drift detected"`.

---

### Model

```python
class EnvironmentState(str, Enum):
    DEPLOYED = "deployed"
    DRIFTED = "drifted"
    UNKNOWN = "unknown"
    UNDEPLOYED = "undeployed"
    STALE_BUILD = "stale_build"

class StageStatus:
    deployment_name: str
    deployment_file: str
    stage_name: str
    state: EnvironmentState
    changes: int                  # number of resource changes (0 = deployed)
    detail: Optional[str]        # human summary of drift or error
    checked_at: datetime
```

---

## Implementation Notes

### Reuse existing infrastructure

- **Deployment loading:** Reuse `BaseDeployCommand._load_deployment()` path — already resolves `@repo/` refs, loads env/workspace
- **Terraform plan:** Reuse `TerraformDeployer.plan()` — already runs `terraform plan` and parses output
- **Build path detection:** Reuse `BuildController` or check `.strata/build/<name>/<stage>/` directly
- **Integration availability:** Reuse `IntegrationController` for auth pre-checks

### Drift detection strategy

Use `terraform plan -detailed-exitcode` (already available via deployer):
- Exit 0 → no changes (deployed)
- Exit 1 → error (unknown)
- Exit 2 → changes detected (drifted)

This avoids parsing plan JSON — just use the exit code for state determination, with optional detail from plan summary.

### Performance considerations

- **Parallel stage checks:** Stages within different deployments are independent — can be parallelized with `concurrent.futures.ThreadPoolExecutor`
- **Quick mode default:** For interactive use, default to `--quick` (file timestamp checks only). Full drift detection requires explicit `--full` flag
- **Timeout per stage:** Individual `terraform plan` calls should timeout after 60s — mark as `unknown` on timeout
- **Cache plan results:** If `strata diff` was run recently (within last N minutes), reuse its output rather than re-running plan

### What NOT to do

- Don't duplicate `strata diff` — `status` gives a summary (deployed/drifted/N changes), `diff` gives the details
- Don't replace `strata deploy status` — that shows live outputs (values), this shows state
- Don't run `terraform apply` — this is purely read-only
- Don't require all deployments to be checked — fail gracefully per deployment (one auth error shouldn't block others)
- Don't hardcode deployment discovery — use profile/solution config, fall back to directory scan

---

## Test Plan

- Unit tests: mock `TerraformDeployer.plan()` to return various exit codes → verify state mapping
- CLI tests: `CliRunner.invoke(main, ["status"])` with mocked deployment discovery
- Test `--quick` vs `--full` mode behavior
- Test `--file` filtering (single deployment)
- Test `--output json` produces valid JSON
- Test exit codes: 0 when all deployed, 3 when drift exists, 1 on system error
- Test graceful degradation: one deployment fails auth → others still report, overall exit 1
- Test with zero deployments found → informative message, not crash

---

## Acceptance Criteria

- [ ] `strata status` discovers all deployments in the active profile
- [ ] `--quick` mode completes in < 3 seconds (no terraform calls)
- [ ] `--full` mode runs `terraform plan` per stage and reports drift count
- [ ] Each deployment/stage shows one of: deployed, drifted, unknown, undeployed, stale_build
- [ ] `--output json` produces machine-parseable results
- [ ] Exit code 3 when any drift detected (enables CI gating)
- [ ] One failing deployment doesn't prevent others from being checked
- [ ] Console output is a compact, scannable table
- [ ] Documented: "Run `strata status` to see if your environments are in sync"
- [ ] Registered as `strata status` (top-level command)

---

## Related

- `strata sln status` — workspace health (repos, profiles, integrations) — NOT deployment state
- `strata deploy status -f FILE` — live Terraform outputs for one deployment — complementary
- `strata diff --file FILE` — detailed drift for one deployment — `status` is the summary version
- Issue #29 — Adoption Readiness Checklist (priority item #12 / "strata status")
- `strata doctor` — checks tool readiness; `status` checks deployment readiness
