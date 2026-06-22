<!-- CHILD ISSUE
  Parent: x-strata-cli.md — strata env command group
  Absorbs: z-strata-status.md
  Status: Ready to implement (step 5 in implementation order)
-->

# Feature: `strata env status` — Multi-Environment State Overview

**Parent:** [x-strata-cli.md](x-strata-cli.md) — `strata env` Unified Environment Inspection Group

## Summary

Implement `strata env status` as an aggregate view across all (or one) deployment environments in a workspace — answering "what's deployed, what's drifted, what's unknown" in a single glance.

**Goal:** Give operators a dashboard view across all environments without running separate commands against each deployment file individually.

---

## Motivation

- Current `strata deploy status -f FILE` shows Terraform outputs for ONE deployment — useful but narrow
- Current `strata sln status` shows workspace health (repos, profiles, integrations) — not deployment state
- Operators managing 3-8 environments need a single command: "Are my environments in sync or not?"
- Without this, teams rely on CI dashboards or manual `terraform plan` in each stage to know if drift exists

---

## Command Interface

```bash
strata env status                                # All deployments, quick mode
strata env status --file deploy/deploy-prd.yaml  # One specific deployment
strata env status --full                         # Include drift detection (terraform plan)
strata env status --output json                  # Machine-readable for CI/dashboards
```

---

## State Model

Each deployment+stage combination has one of these states:

| State         | Meaning                                           | How detected                                         |
| ------------- | ------------------------------------------------- | ---------------------------------------------------- |
| `deployed`    | Infrastructure matches config — no drift          | `terraform plan` shows 0 changes                     |
| `drifted`     | Infrastructure exists but differs from config     | `terraform plan` shows N changes                     |
| `unknown`     | Cannot determine state (no backend, auth failure) | `terraform plan` fails or state doesn't exist        |
| `undeployed`  | Backend reachable but state is empty              | State file missing or `terraform show` returns empty |
| `stale-build` | Build output is older than config source files    | Compare file mtimes: config YAML vs `.strata/build/` |

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

## Quick Mode vs. Full Mode

| Mode                | What it checks                                                 | Speed         |
| ------------------- | -------------------------------------------------------------- | ------------- |
| `--quick` (default) | Build output exists? State file exists? File timestamps match? | < 2 seconds   |
| `--full`            | Actually runs `terraform plan` per stage to detect drift       | 10-60 seconds |

**Quick mode checks:**
1. Does `.strata/build/<deployment>/<stage>/` exist? → if no: `undeployed` or `stale-build`
2. Is any config file newer than build output? → if yes: `stale-build`
3. Does Terraform state exist in backend? → fast: `terraform state list` or check state file

**Full mode adds:**
4. `terraform plan -detailed-exitcode` per stage (exit 0 = no changes, exit 2 = changes exist)

---

## Discovery: Finding Deployments

1. Read `solution.json` → get registered profiles
2. For active profile → get deployment file paths
3. For each deployment file → load deployment model → enumerate stages
4. Return list of `(deployment_name, stage_name, state)` tuples

**Fallback:** If no profile-based discovery, scan `deploy/**/*.yaml` for files with `kind: deployment`.

---

## Output Format (Console)

```
📊 strata env status
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  Deployment         Stage            State         Details
  ─────────────────  ───────────────  ────────────  ─────────────────────
  haven_deploy_prd   infrastructure   ✅ deployed    0 changes
  haven_deploy_stg   infrastructure   ⚠️  drifted    3 resources changed
  haven_deploy_dev   infrastructure   ❓ unknown     Auth failed: az login required

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Summary: 1 deployed, 1 drifted, 1 unknown
```

Quick mode:
```
📊 strata env status (quick)
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

## Output Format (JSON)

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
    }
  ]
}
```

---

## Architecture

```
commands/
  env/
    status_command.py            ← EnvStatusCommand extends BaseCommand
controllers/
  env_status_controller.py       ← Orchestrates per-deployment state checks
```

### Layer Rules

- `EnvStatusCommand` → `EnvStatusController` → existing deployer infrastructure
- `INIT_REQUIRED = True` — needs solution.json to find all deployment files
- Reuses `TerraformDeployer.plan()` for drift detection (already exists)
- Reuses build path detection from deploy infrastructure

### Reuse Existing Infrastructure

- **Deployment loading:** Reuse `BaseDeployCommand._load_deployment()` path
- **Terraform plan:** Reuse `TerraformDeployer.plan()` for `--full` mode
- **Build path detection:** Check `.strata/build/<name>/<stage>/` directly
- **Integration availability:** Reuse `IntegrationController` for auth pre-checks

---

## Exit Codes

| Code | Meaning                                              |
| ---- | ---------------------------------------------------- |
| 0    | All environments deployed, no drift                  |
| 1    | System/execution error (auth failure, command crash) |
| 3    | Drift detected or environments not in desired state  |

Exit code 3 for drift enables CI usage: `strata env status --full || echo "Drift detected"`

---

## Relationship to Existing Commands

| Command                        | Purpose                                            | After this change           |
| ------------------------------ | -------------------------------------------------- | --------------------------- |
| `strata sln status`            | Workspace health (repos, profiles, tools)          | ✅ Unchanged                 |
| `strata deploy status -f FILE` | Live Terraform outputs for one deployment          | ⚠️ Deprecated → `env output` |
| `strata env status` (NEW)      | Aggregate environment state across all deployments | ✅ New                       |

---

## Acceptance Criteria

- [ ] `strata env status` shows aggregate state for all deployments in workspace
- [ ] `--file` filters to a single deployment
- [ ] `--quick` (default) checks build output + timestamps only (< 2 seconds)
- [ ] `--full` runs `terraform plan` per stage for drift detection
- [ ] `--output json` emits valid JSON with documented schema
- [ ] Exit code 3 when drift detected or environments not in desired state
- [ ] Discovery works via solution profiles and fallback directory scan

## Relationships

- **Absorbs:** `z-strata-status.md` (full design carried forward, command renamed)
- **Depends on:** `x-strata-cli-output.md` and `x-strata-cli-state.md` (shared build path infrastructure)
- **Related:** `x-strata-cli-drift.md` (status = aggregate overview, drift = detailed per-deployment delta)
