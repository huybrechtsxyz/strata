# XYZ Platform — DevOps Workflow Guide

> **Audience:** DevOps engineer (Basher) working with VS Code, multiple repositories, and a
> dedicated workspace repo. This guide walks through the complete lifecycle of a platform
> workspace from first setup to running infrastructure deployments — and flags the gaps
> where manual steps are currently required.

---

## Setup Assumptions

Basher's environment:

| Item                            | Example                                           |
| ------------------------------- | ------------------------------------------------- |
| Workspace repo                  | `git@github.com:org/xyz-workspace.git`            |
| Platform config repo            | `git@github.com:org/xyz-config.git`               |
| Infrastructure (Terraform) repo | `git@github.com:org/xyz-infrastructure.git`       |
| Service config repo             | `git@github.com:org/xyz-svc-traefik.git`          |
| Local workspace root            | `C:\src\workspace\` (has `.platform/` after init) |
| Active profile                  | `prd`                                             |

All `xyz` commands are run from inside the workspace root (or pass `--work-path`).

---

## Global Options

Every `xyz` command accepts these options:

```bash
--work-path PATH    # Override workspace root (also: XYZ_WORK_PATH env var)
--output FORMAT     # Output format: console (default) | text | json
--verbose           # Show structured log output inline
--quiet             # Suppress all console output
-h / --help         # Show help
```

Persist defaults so you don't repeat them every time:

```bash
xyz config set output json       # Switch to JSON output globally
xyz config set verbose true      # Always show verbose logs
xyz config list                  # Verify persisted defaults
```

---

## Phase 1 — Initialize the Workspace

### 1.1 Create the workspace

```bash
# In the empty workspace repo clone
cd C:\src\workspace

xyz init --name xyz-workspace
```

Creates:
- `.platform/project.json` — solution registry
- `.platform/cli.yaml`    — workspace defaults
- `.platform/logging.yaml` — logging configuration

### 1.2 Verify it's healthy

```bash
xyz status
```

---

## Phase 2 — Register Repositories

Each external repo that contains config, Terraform, or YAML platform files must be
registered before it can be referenced.

### 2.1 Add repositories

```bash
# Register and clone in one step
xyz repo add xyz-config         git@github.com:org/xyz-config.git         --branch main --path repos/xyz-config --clone
xyz repo add xyz-infrastructure git@github.com:org/xyz-infrastructure.git --branch main --path repos/xyz-infrastructure --clone
xyz repo add xyz-svc-traefik    git@github.com:org/xyz-svc-traefik.git    --branch main --path repos/xyz-svc-traefik --clone
```

Omit `--clone` to register without cloning and run `xyz repo sync` separately.

### 2.2 Clone / pull them all (when not using `--clone`)

```bash
xyz repo sync
```

Clones any repo not yet on disk; pulls repos already cloned. Re-run after upstream changes.

```bash
# Sync only one repo
xyz repo sync --name xyz-infrastructure

# Hard reset dirty trees
xyz repo sync --force
```

### 2.3 List registered repos

```bash
xyz repo list
```

### 2.4 Check git state of all repos

```bash
# All registered repos
xyz repo status

# Single repo
xyz repo status --name xyz-infrastructure

# Include individual changed files
xyz repo status --verbose
```

Shows current branch, tracking remote, ahead/behind counts, and a clean/dirty
summary for each repo that has been cloned.  Repos not yet on disk show as
``not cloned``.

---

## Phase 3 — Set Up Profiles

Profiles map a named context (e.g. `prd`, `stg`, `dev`) to a set of file refs.
The active profile determines which config files are fed into `build` and `deploy`.

### 3.1 Create profiles

```bash
xyz profile add dev
xyz profile add stg
xyz profile add prd
```

### 3.2 Activate the working profile

```bash
xyz profile activate prd
```

### 3.3 List / inspect profiles

```bash
xyz profile list
xyz profile show prd
```

---

## Phase 4 — Register File References

Refs tell the build which YAML files to merge. References use `@repo-name/relative/path`
notation to point into registered repos.

### 4.1 Register configuration files (merged into platform config)

```bash
xyz ref configfile add global-config @xyz-config/config/xyz-config.yaml       --profile prd
xyz ref configfile add logging-config @xyz-config/config/xyz-logging.yaml      --profile prd
```

### 4.2 Register environment overlays

```bash
xyz ref envfile add prd-env @xyz-config/environments/xyz-env-prd.yaml          --profile prd
```

### 4.3 Register secret files (plain file on disk — no vault layer yet)

```bash
xyz ref secretfile add prd-secrets /run/secrets/xyz-prd.yaml                   --profile prd
```

### 4.4 Verify refs

```bash
xyz ref configfile list --profile prd
xyz ref configfile show global-config --profile prd    # preview the file content
```

### ⚠️ Gap: no merged-env preview

There is no `xyz profile export` or `xyz env show` command that shows all refs
deep-merged as the build would see them. To debug merge issues you must run a full build.

### ⚠️ Gap: no secrets management layer

`ref secretfile` records a file path. There is no `xyz secret get KEY`, no vault
integration, no encryption at rest. Secret files are plain files on disk.

---

## Phase 5 — Validate YAML Files

Before building, validate individual YAML files:

### 5.1 Structural validation (Pydantic schema)

```bash
xyz validate repos/xyz-config/config/xyz-config.yaml
xyz validate repos/xyz-infrastructure/deployments/xyz-deploy-prd.yaml
```

Exit codes: `0` = valid, `3` = validation failure.

### 5.2 Deep validation (cross-references against active profile)

```bash
xyz validate repos/xyz-infrastructure/deployments/xyz-deploy-prd.yaml --deep
```

Resolves `@repo-name/...` cross-references, checks that all referenced files exist,
and validates values against the merged configuration.

### ⚠️ Gap: no bulk/scan validation

`validate` works on one file at a time. There is no `validate --all` or
`validate scan` to check every YAML in the workspace at once. CI pipelines
must call `validate` per file explicitly.

---

## Phase 6 — Build

Build generates the deployment artifacts (rendered Terraform variable files,
`platform.json`, merged configs) in `.platform/build/<deployment>/`.

### 6.1 Dry-run first (plan only — no files written)

```bash
xyz build run -f repos/xyz-infrastructure/deployments/xyz-deploy-prd.yaml --dry-run
```

### 6.2 Full build

```bash
xyz build run -f repos/xyz-infrastructure/deployments/xyz-deploy-prd.yaml
```

Reads:
- The deployment YAML file
- All `configfile` refs from the active profile
- All related workspace / stack YAML files referenced inside the deployment

Writes:
- `.platform/build/<deployment>/` — Terraform `.tfvars.json`, `platform.json`, rendered templates

### 6.3 Clean build artifacts

```bash
xyz build clean -f repos/xyz-infrastructure/deployments/xyz-deploy-prd.yaml
```

### ⚠️ Gap: no change-plan diff

`--dry-run` validates and plans but produces no readable diff against current
infrastructure state. There is no `build diff` or `deploy diff` that shows
what would change before applying.

---

## Phase 7 — Deploy

Deploy executes provisioners (Terraform) against the built artifacts.

### 7.1 Dry-run (init + validate + plan — no apply)

```bash
xyz deploy run -f repos/xyz-infrastructure/deployments/xyz-deploy-prd.yaml --dry-run
```

Runs: `terraform init` → `terraform validate` → `terraform plan`

### 7.2 Deploy a single stage (selective)

```bash
xyz deploy run -f repos/xyz-infrastructure/deployments/xyz-deploy-prd.yaml --stage xyz-dc-eu-fr
```

### 7.3 Full deploy

```bash
xyz deploy run -f repos/xyz-infrastructure/deployments/xyz-deploy-prd.yaml
```

Runs per stage (in order): `terraform init` → `terraform validate` → `terraform plan` → `terraform apply`

### 7.4 Force (skip approval gates)

```bash
xyz deploy run -f repos/xyz-infrastructure/deployments/xyz-deploy-prd.yaml --force
```

### 7.5 Destroy (tear down infrastructure)

Destroy is its own command — not a flag on `deploy run`.

```bash
# Preview what would be removed (safe — no changes)
xyz deploy destroy -f repos/xyz-infrastructure/deployments/xyz-deploy-prd.yaml --dry-run

# Tear down a single stage
xyz deploy destroy -f repos/xyz-infrastructure/deployments/xyz-deploy-prd.yaml --stage xyz-dc-eu-fr --force

# Tear down all stages (--force required — runs terraform destroy -auto-approve)
xyz deploy destroy -f repos/xyz-infrastructure/deployments/xyz-deploy-prd.yaml --force
```

- `--dry-run` runs `terraform plan -destroy` — shows exactly what would be removed, writes nothing.
- `--force` is required for the real destroy (enables `-auto-approve`).
- Without `--force` and without `--dry-run`, the command exits with an error.

### 7.6 Status (outputs, plan details, history)

```bash
# Live infrastructure outputs per stage (queries the Terraform backend)
xyz deploy status -f repos/xyz-infrastructure/deployments/xyz-deploy-prd.yaml

# Single stage only
xyz deploy status -f repos/xyz-infrastructure/deployments/xyz-deploy-prd.yaml --stage xyz-dc-eu-fr

# Decode the last saved .tfplan — no backend call, instant
xyz deploy status -f repos/xyz-infrastructure/deployments/xyz-deploy-prd.yaml --plan

# Show execution history from workspace logs
xyz deploy status --history
xyz deploy status --history --lines 20
```

- Default (no flags): runs `terraform output -json` per stage — shows live endpoint URLs, resource IDs, etc.
- `--plan`: reads the `.tfplan` written by the last `deploy run --dry-run`. Shows resource add/change/destroy counts. No network required.

For execution history use `xyz deploy history`.

### 7.7 Health checks

Health checks probe the actual running infrastructure after a deploy. They are defined
per stage in the deployment YAML and run via `deploy health`.

**Deployment YAML — add `health_checks` to a stage:**

```yaml
stages:
  - name: xyz-dc-eu-fr
    type: infrastructure
    health_checks:
      - name: api-endpoint
        type: http
        output_key: api_url     # reads from terraform output
        expect_status: 200
        timeout: 10
      - name: db-port
        type: tcp
        host: 10.0.0.5
        port: 5432
```

**Run the checks:**

```bash
# All stages in the deployment
xyz deploy health -f repos/xyz-infrastructure/deployments/xyz-deploy-prd.yaml

# Single stage
xyz deploy health -f repos/xyz-infrastructure/deployments/xyz-deploy-prd.yaml --stage xyz-dc-eu-fr
```

**Check types:**

| Type   | Target resolution                                               | What is tested                                                  |
| ------ | --------------------------------------------------------------- | --------------------------------------------------------------- |
| `http` | `url` field, or Terraform output named by `output_key`          | HTTP GET — status code must match `expect_status` (default 200) |
| `tcp`  | `host` + `port` fields, or `host:port` from `output_key` output | TCP connection succeeds within `timeout` seconds                |

- Stages without `health_checks` are silently skipped.
- Exit code `3` if any check fails; `0` if all pass.

### 7.8 Execution history

```bash
# All deploy operations (newest first)
xyz deploy history

# Limit to last 20 entries
xyz deploy history --lines 20

# Filter by operation type
xyz deploy history --operation run
xyz deploy history --operation destroy

# Include execution IDs
xyz deploy history --verbose
```

- Scans `.platform/logs/` JSONL files for `deploy_run` and `deploy_destroy` events.
- Groups entries by `execution_id` so each run appears as a single row.
- No deployment file required — reads workspace logs only.
- Exit code `0` even when the history is empty.

### ⚠️ Gap: no approval workflow

`--force` bypasses gates. There is no `xyz deploy approve`, no pending-approval list,
and no integration with external gate systems (Azure DevOps environment gates, etc.).

---

## Phase 8 — Inspect and Debug

```bash
# Workspace health + integration availability
xyz status

# View logs from last command
xyz log list --last

# View last 100 lines at DEBUG level
xyz log list --lines 100 --level DEBUG

# View logs for a specific execution
xyz log list --execution-id <UUID>

# Show built-in workflow topics
xyz help --list
xyz help --topic quickstart
xyz help --topic cross-repo
```

---

## Phase 9 — Maintenance

```bash
# Pull latest from all registered repos
xyz repo sync

# Wipe logs and temp artifacts
xyz clean
xyz clean --dry-run     # preview first

# Reset logging config to defaults
xyz log reset
```

---

## Full Example Session (New Workspace)

```bash
# -- One-time setup --
cd C:\src\workspace
xyz init --name xyz-workspace

xyz repo add xyz-config         git@github.com:org/xyz-config.git
xyz repo add xyz-infrastructure git@github.com:org/xyz-infrastructure.git
xyz repo add xyz-svc-traefik    git@github.com:org/xyz-svc-traefik.git
xyz repo sync

xyz profile add prd
xyz profile activate prd

xyz ref configfile add global-config  @xyz-config/config/xyz-config.yaml      --profile prd
xyz ref configfile add logging-config @xyz-config/config/xyz-logging.yaml     --profile prd
xyz ref envfile    add prd-env        @xyz-config/environments/xyz-env-prd.yaml --profile prd

# -- Validate --
xyz validate repos/xyz-infrastructure/deployments/xyz-deploy-prd.yaml --deep

# -- Build --
xyz build run -f repos/xyz-infrastructure/deployments/xyz-deploy-prd.yaml --dry-run
xyz build run -f repos/xyz-infrastructure/deployments/xyz-deploy-prd.yaml

# -- Deploy --
xyz deploy run -f repos/xyz-infrastructure/deployments/xyz-deploy-prd.yaml --dry-run
xyz deploy run -f repos/xyz-infrastructure/deployments/xyz-deploy-prd.yaml

# -- Inspect --
xyz status
xyz log list --last
```

---

## Day-to-Day Cycle (Existing Workspace)

```bash
# Pull latest config/infra changes
xyz repo sync

# Re-build after upstream changes
xyz build run -f repos/xyz-infrastructure/deployments/xyz-deploy-prd.yaml

# Deploy changes
xyz deploy run -f repos/xyz-infrastructure/deployments/xyz-deploy-prd.yaml --dry-run
xyz deploy run -f repos/xyz-infrastructure/deployments/xyz-deploy-prd.yaml

# Review what happened
xyz log list --last
```

---

## Complete Command Reference

### Workspace

| Command                   | Description                                        |
| ------------------------- | -------------------------------------------------- |
| `xyz init --name NAME`    | Initialize a new workspace                         |
| `xyz status`              | Show workspace health and integration availability |
| `xyz clean [--dry-run]`   | Remove logs and temp artifacts                     |
| `xyz version`             | Print CLI version                                  |
| `xyz help [--topic NAME]` | Show workflow guidance topics                      |

### Configuration

| Command                    | Description                           |
| -------------------------- | ------------------------------------- |
| `xyz config set KEY VALUE` | Persist a workspace-level CLI default |
| `xyz config unset KEY`     | Remove a persisted default            |
| `xyz config list`          | List all persisted defaults           |

Valid keys: `output`, `verbose`, `quiet`, `work_path`

### Logging

| Command                                         | Description                              |
| ----------------------------------------------- | ---------------------------------------- |
| `xyz log list [--last] [--lines N] [--level L]` | View execution logs                      |
| `xyz log config`                                | Print current `logging.yaml`             |
| `xyz log set KEY VALUE`                         | Set a logging config value               |
| `xyz log reset`                                 | Reset logging config to package defaults |

### Repositories

| Command                                                   | Description                                   |
| --------------------------------------------------------- | --------------------------------------------- |
| `xyz repo add NAME URL [--branch B] [--path P] [--clone]` | Register a repo; `--clone` clones immediately |
| `xyz repo list [--name NAME]`                             | List registered repos                         |
| `xyz repo status [--name NAME]`                           | Show git state (branch, dirty, ahead/behind)  |
| `xyz repo remove NAME [--purge]`                          | Remove a repo (`--purge` deletes from disk)   |
| `xyz repo sync [--name NAME] [--force]`                   | Clone / pull registered repos                 |

### Profiles

| Command                     | Description                           |
| --------------------------- | ------------------------------------- |
| `xyz profile add NAME`      | Create a new profile                  |
| `xyz profile remove NAME`   | Delete a profile                      |
| `xyz profile list`          | List all profiles                     |
| `xyz profile activate NAME` | Set the active profile                |
| `xyz profile show NAME`     | Show all refs registered on a profile |

### File References

All `ref` subgroups (`envfile`, `configfile`, `datafile`, `secretfile`) share:

| Command                                      | Description                |
| -------------------------------------------- | -------------------------- |
| `xyz ref <TYPE> add NAME PATH [--profile P]` | Register a file reference  |
| `xyz ref <TYPE> remove NAME [--profile P]`   | Remove a file reference    |
| `xyz ref <TYPE> list [--profile P]`          | List registered references |
| `xyz ref <TYPE> show NAME [--profile P]`     | Display the file content   |

### Validation

| Command                      | Description                   |
| ---------------------------- | ----------------------------- |
| `xyz validate FILE [--deep]` | Validate a platform YAML file |

### Build

| Command                               | Description                                 |
| ------------------------------------- | ------------------------------------------- |
| `xyz build run -f FILE [--dry-run]`   | Run the platform + Terraform build pipeline |
| `xyz build clean -f FILE [--dry-run]` | Remove build artifacts                      |

### Deploy

| Command                                                        | Description                                                          |
| -------------------------------------------------------------- | -------------------------------------------------------------------- |
| `xyz deploy run -f FILE [--stage S] [--force] [--dry-run]`     | Execute the deploy pipeline                                          |
| `xyz deploy destroy -f FILE [--stage S] [--force] [--dry-run]` | Tear down infrastructure; `--dry-run` plans, `--force` auto-approves |
| `xyz deploy status -f FILE [--stage S] [--plan]`               | Live Terraform outputs or saved plan details                         |
| `xyz deploy history [--lines N] [--operation run\|destroy]`    | Execution history from workspace logs                                |
| `xyz deploy health -f FILE [--stage S]`                        | Run `health_checks` defined in the deployment YAML                   |

---

## Known Gaps

| Gap                                      | Priority | Workaround                                     |
| ---------------------------------------- | -------- | ---------------------------------------------- |
| No `build diff` / change-plan output     | High     | Use `--dry-run` + read Terraform plan manually |
| No `validate --all` / bulk scan          | High     | Script individual `validate` calls per file    |
| No `profile export` (merged env preview) | Medium   | Run `build run --dry-run` as a proxy           |
| No secrets management layer              | Medium   | Manage secret files manually outside the CLI   |
| No `deploy approve` / gate workflow      | Medium   | Use `--force` or external gate tooling         |
| No `init --from-template`                | Low      | Manually configure each new workspace          |
