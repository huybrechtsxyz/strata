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

### ⚠️ Gap: destroy is a stub

`--destroy` flag is declared on `deploy run` but **not yet implemented** (marked TODO).
Tearing down infrastructure currently requires running `terraform destroy` manually.

### ⚠️ Gap: no deploy status / history

`xyz status` shows workspace health only. There is no `xyz deploy status`,
`xyz deploy history`, or deployment audit trail in the CLI.

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

| Command                                                    | Description                 |
| ---------------------------------------------------------- | --------------------------- |
| `xyz deploy run -f FILE [--stage S] [--force] [--dry-run]` | Execute the deploy pipeline |

---

## Known Gaps

| Gap                                      | Priority     | Workaround                                     |
| ---------------------------------------- | ------------ | ---------------------------------------------- |
| `deploy run --destroy` is a stub (TODO)  | **Critical** | Run `terraform destroy` manually               |
| No `deploy status` / deployment history  | High         | Check Terraform state directly                 |
| No `build diff` / change-plan output     | High         | Use `--dry-run` + read Terraform plan manually |
| No `validate --all` / bulk scan          | High         | Script individual `validate` calls per file    |
| No `repo status` (git state inspection)  | Medium       | Use `git status` directly in each repo         |
| No `profile export` (merged env preview) | Medium       | Run `build run --dry-run` as a proxy           |
| No secrets management layer              | Medium       | Manage secret files manually outside the CLI   |
| No `deploy approve` / gate workflow      | Medium       | Use `--force` or external gate tooling         |
| No `init --from-template`                | Low          | Manually configure each new workspace          |
