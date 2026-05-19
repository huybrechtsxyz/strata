# strata — DevOps Workflow Guide

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
| Local workspace root            | `C:\src\workspace\` (has `.strata/` after init) |
| Active profile                  | `prd`                                             |

All `xyz` commands are run from inside the workspace root (or pass `--work-path`).

---

## Global Options

Every `xyz` command accepts these options:

```bash
--work-path PATH    # Override workspace root (also: STRATA_WORK_PATH env var)
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
- `.strata/project.json`          — solution registry
- `.strata/cli.yaml`              — workspace defaults
- `.strata/logging.yaml`          — logging configuration
- `.devcontainer/devcontainer.json` — dev container definition (Python 3.13, Terraform, Azure CLI, kubectl/Helm)
- `.devcontainer/post-create.sh`    — installs `strata` and shell completion inside the container

> **VS Code / Codespaces:** Once `xyz init` completes, select **Reopen in Container** in VS Code (or open the repo in GitHub Codespaces) to get a fully configured environment with no local tool installation required.

### 1.3 Create the workspace from a template (optional)

A **workspace template** is a local YAML file that declares which repos to
register, which profiles to create, and which file references to add. Using
one skips Phases 2–4 for standard setups.

```bash
xyz init --name xyz-workspace --from-template ./templates/standard-three-repo.yaml
```

**Template file format** (`standard-three-repo.yaml`):

```yaml
apiVersion: platform.huybrechts.xyz/v1
kind: workspace-template
meta:
  name: standard-three-repo
  annotations:
    description: Config + infrastructure + traefik workspace
spec:
  repos:
    - name: xyz-config
      url: "git@github.com:org/xyz-config.git"
      branch: main
      path: repos/xyz-config
    - name: xyz-infrastructure
      url: "git@github.com:org/xyz-infrastructure.git"
      branch: main
      path: repos/xyz-infrastructure
    - name: xyz-svc-traefik
      url: "git@github.com:org/xyz-svc-traefik.git"
      branch: main
      path: repos/xyz-svc-traefik
  profiles:
    - name: prd
      activate: true
      refs:
        configfile:
          - name: global-config
            path: "@xyz-config/config/xyz-config.yaml"
          - name: logging-config
            path: "@xyz-config/config/xyz-logging.yaml"
        envfile:
          - name: prd-env
            path: "@xyz-config/environments/xyz-env-prd.yaml"
  approvals:
    approvers:
      platform-team:
        type: github-team
        value: "org/platform-team"
      devops-lead:
        type: user
        value: "devops@company.com"
```

> **Note:** `spec.approvals` in a workspace template is metadata only — it declares default approvers for deployments initialized from this template. Enforcement is handled by your CI/CD system. See §7.9 for the full approvals schema.

- `--from-template` accepts any local path (absolute or relative to `--work-path`).
- Remote / `@repo-name/...` template references are **not** supported — the file must be on disk before `init` runs.
- Repos registered from a template are **not** cloned automatically; run `xyz repo sync` afterwards.
- At most one profile may set `activate: true`.

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
xyz ref config add global-config @xyz-config/config/xyz-config.yaml       --profile prd
xyz ref config add logging-config @xyz-config/config/xyz-logging.yaml      --profile prd
```

### 4.2 Register environment overlays

```bash
xyz ref env add prd-env @xyz-config/environments/xyz-env-prd.yaml          --profile prd
```

### 4.3 Register secret files (plain file on disk — no vault layer yet)

```bash
xyz ref secret add prd-secrets /run/secrets/xyz-prd.yaml                   --profile prd
```

### 4.4 Verify refs

```bash
xyz ref config list --profile prd
xyz ref config show global-config --profile prd    # preview the file content
```

### 4.5 Inspect resolved values for a deployment

Before building or deploying you can verify which concrete values the CLI would
use for every declared variable, secret, and feature flag:

```bash
# List all (secrets are masked: first 3 chars + *****)
xyz values list -f repos/xyz-infrastructure/deployments/xyz-deploy-prd.yaml

# Filter to a single type
xyz values list -f … --type secrets
xyz values list -f … --type variables
xyz values list -f … --type features

# Show only entries that failed to resolve
xyz values list -f … --unresolved

# Also show the store reference (env var name, key path, flag id)
xyz values list -f … --show-store

# Retrieve one or more values in full (secrets revealed)
xyz values get -f … DB_PASSWORD API_KEY
```

Exit codes: `0` = all resolved, `3` = one or more entries failed.

JSON output is supported via `--output json`.

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

---

## Phase 6 — Build

> **Requires:** Terraform CLI (`terraform`) installed and on `PATH`. Use `--dry-run` to validate and plan without writing output files — this works without Terraform.

Build generates the deployment artifacts (rendered Terraform variable files,
`platform.json`, merged configs) in `.strata/build/<deployment>/`.

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
- `.strata/build/<deployment>/` — Terraform `.tfvars.json`, `platform.json`, rendered templates

### 6.3 Clean build artifacts

```bash
xyz build clean -f repos/xyz-infrastructure/deployments/xyz-deploy-prd.yaml
```

### 6.4 Preview what build run would change

```bash
# Full plan: artifact diff + terraform plan per stage
xyz build plan -f repos/xyz-infrastructure/deployments/xyz-deploy-prd.yaml

# Artifact diff only (no terraform required)
xyz build plan -f repos/xyz-infrastructure/deployments/xyz-deploy-prd.yaml --artifacts-only

# Limit terraform plan to one stage
xyz build plan -f repos/xyz-infrastructure/deployments/xyz-deploy-prd.yaml --stage xyz-dc-eu-fr
```

Nothing is written to `.strata/build/`. The command builds into a temp directory,
diffs the result against the current on-disk build, then runs
`terraform init → validate → plan` per stage.

Sample output:

```
📋  Build Plan — xyz-deploy-prd
  ────────────────────────────────────────────────────────────
  Artifact changes:
  ────────────────────────────────────────────────────────────
  ~  terraform/xyz-dc-eu-fr.tfvars.json    3 line(s) changed
  +  terraform/xyz-ns-base.tfvars.json     new file
  =  platform.json                         no change

  Terraform plan  [stage: xyz-dc-eu-fr]
  ────────────────────────────────────────────────────────────
  Plan: 2 to add, 1 to change, 0 to destroy.
  ✅  Plan complete

  ────────────────────────────────────────────────────────────
  Artifacts: 1 new, 1 changed, 1 unchanged
  Terraform: 1 stage(s) planned
```

### ⚠️ Gap: no change-plan diff

`--dry-run` validates and plans but produces no readable diff against current
infrastructure state. There is no `build diff` or `deploy diff` that shows
what would change before applying.

---

## Phase 7 — Deploy

> **Requires:** Terraform CLI and configured integration credentials (Bitwarden, Vault, Azure Key Vault, etc.). Use `--dry-run` to run `terraform init → validate → plan` safely before applying.

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

- Scans `.strata/logs/` JSONL files for `deploy_run` and `deploy_destroy` events.
- Groups entries by `execution_id` so each run appears as a single row.
- No deployment file required — reads workspace logs only.
- Exit code `0` even when the history is empty.

### 7.9 Declare approval metadata

Approvals are **metadata declared in the deployment YAML** — the CLI logs which
approvers apply per stage, but enforcement is done by the CI/CD system
(Azure DevOps environment gate, GitHub Actions environment protection rule, etc.).

**Deployment YAML — add `approvals` to the spec:**

```yaml
spec:
  approvals:
    approvers:
      platform-team:
        type: github-team       # github-team | ado-group | user
        value: "org/platform-team"
      devops-lead:
        type: user
        value: "vhuybrec@company.com"
      ado-approvers:
        type: ado-group
        value: "Platform-Approvers"

  stages:
    - name: xyz-dc-eu-fr
      type: infrastructure
      # no approval field → all spec-level approvers apply

    - name: xyz-dc-eu-prod
      type: infrastructure
      approval:
        approvers:
          - platform-team       # keys from spec.approvals.approvers
          - ado-approvers
```

- `spec.approvals` absent → no gate declared, deploy proceeds.
- `spec.approvals.approvers` empty dict → silently treated as no gate.
- Stage without `approval` field → no stage-level restriction.
- Stage `approval.approvers` lists keys from `spec.approvals.approvers`; unknown keys are a validation error.
- Approver types: `github-team`, `ado-group`, `user`.

---

## Phase 8 — Inspect and Debug

```bash
# Workspace health + integration availability
xyz status

# View logs from last command
xyz audit list --last

# View last 100 lines at DEBUG level
xyz audit list --lines 100 --level DEBUG

# View logs for a specific execution
xyz audit list --execution-id <UUID>

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
xyz audit log reset
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

xyz ref config add global-config  @xyz-config/config/xyz-config.yaml      --profile prd
xyz ref config add logging-config @xyz-config/config/xyz-logging.yaml     --profile prd
xyz ref env    add prd-env        @xyz-config/environments/xyz-env-prd.yaml --profile prd

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
xyz audit list --last
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
xyz audit list --last
```

---

## Complete Command Reference

### Workspace

| Command                                       | Description                                                                           |
| --------------------------------------------- | ------------------------------------------------------------------------------------- |
| `xyz init --name NAME [--from-template FILE]` | Initialize a new workspace; `--from-template` pre-populates repos, profiles, and refs |
| `xyz status`                                  | Show workspace health and integration availability                                    |
| `xyz clean [--dry-run]`                       | Remove logs and temp artifacts                                                        |
| `xyz version`                                 | Print CLI version                                                                     |
| `xyz help [--topic NAME]`                     | Show workflow guidance topics                                                         |

### Configuration

| Command                    | Description                           |
| -------------------------- | ------------------------------------- |
| `xyz config set KEY VALUE` | Persist a workspace-level CLI default |
| `xyz config unset KEY`     | Remove a persisted default            |
| `xyz config list`          | List all persisted defaults           |

Valid keys: `output`, `verbose`, `quiet`, `work_path`

### Audit

| Command                                           | Description                              |
| ------------------------------------------------- | ---------------------------------------- |
| `xyz audit list [--last] [--lines N] [--level L]` | View execution logs                      |
| `xyz audit log list`                              | Print current `logging.yaml`             |
| `xyz audit log set KEY VALUE`                     | Set a logging config value               |
| `xyz audit log reset`                             | Reset logging config to package defaults |

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

All `ref` subgroups (`env`, `config`, `data`, `secret`) share:

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

| Command                                                 | Description                                                     |
| ------------------------------------------------------- | --------------------------------------------------------------- |
| `xyz build run -f FILE [--dry-run]`                     | Run the platform + Terraform build pipeline                     |
| `xyz build plan -f FILE [--stage S] [--artifacts-only]` | Artifact diff + terraform plan per stage (reads only, temp dir) |
| `xyz build clean -f FILE [--dry-run]`                   | Remove build artifacts                                          |

### Deploy

| Command                                                        | Description                                                          |
| -------------------------------------------------------------- | -------------------------------------------------------------------- |
| `xyz deploy run -f FILE [--stage S] [--force] [--dry-run]`     | Execute the deploy pipeline                                          |
| `xyz deploy destroy -f FILE [--stage S] [--force] [--dry-run]` | Tear down infrastructure; `--dry-run` plans, `--force` auto-approves |
| `xyz deploy status -f FILE [--stage S] [--plan]`               | Live Terraform outputs or saved plan details                         |
| `xyz deploy history [--lines N] [--operation run\|destroy]`    | Execution history from workspace logs                                |
| `xyz deploy health -f FILE [--stage S]`                        | Run `health_checks` defined in the deployment YAML                   |

### Values

| Command                                                            | Description                                                          |
| ------------------------------------------------------------------ | -------------------------------------------------------------------- |
| `xyz values list -f FILE [--type T] [--show-store] [--unresolved]` | List all variables / secrets (masked) / feature flags                |
| `xyz values get  -f FILE KEY [KEY …]`                              | Retrieve full resolved value(s) for specific keys (secrets revealed) |


