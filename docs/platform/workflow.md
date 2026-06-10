# strata — DevOps Workflow Guide

> **Audience:** DevOps engineer (Basher) working with VS Code, multiple repositories, and a
> dedicated workspace repo. This guide walks through the complete lifecycle of a platform
> workspace from first setup to running infrastructure deployments — and flags the gaps
> where manual steps are currently required.

---

## Setup Assumptions

Basher's environment:

| Item                            | Example                                         |
| ------------------------------- | ----------------------------------------------- |
| Workspace repo                  | `git@github.com:org/xyz-workspace.git`          |
| Platform config repo            | `git@github.com:org/xyz-config.git`             |
| Infrastructure (Terraform) repo | `git@github.com:org/xyz-infrastructure.git`     |
| Service config repo             | `git@github.com:org/xyz-svc-traefik.git`        |
| Local workspace root            | `C:\src\workspace\` (has `.strata/` after init) |
| Active profile                  | `prd`                                           |

All `strata` commands are run from inside the workspace root (or pass `--work-path`).

---

## Global Options

Every `strata` command accepts these options:

```bash
--work-path PATH    # Override workspace root (also: STRATA_WORK_PATH env var)
--output FORMAT     # Output format: console (default) | text | json
--verbose           # Show structured log output inline
--quiet             # Suppress all console output
-h / --help         # Show help
```

Persist defaults so you don't repeat them every time:

```bash
strata config set output json       # Switch to JSON output globally
strata config set verbose true      # Always show verbose logs
strata config list                  # Verify persisted defaults
```

---

## Phase 1 — Initialize the Workspace

### 1.1 Create the workspace

```bash
# In the empty workspace repo clone
cd C:\src\workspace

strata sln init --name xyz-workspace
```

Creates:
- `.strata/solution.json`          — solution registry
- `.strata/cli.yaml`              — workspace defaults
- `.strata/logging.yaml`          — logging configuration
- `.devcontainer/devcontainer.json` — dev container definition (Python 3.13, Terraform, Azure CLI, kubectl/Helm)
- `.devcontainer/post-create.sh`    — installs `strata` and shell completion inside the container

> **VS Code / Codespaces:** Once `strata sln init` completes, select **Reopen in Container** in VS Code (or open the repo in GitHub Codespaces) to get a fully configured environment with no local tool installation required.

### 1.3 Create the workspace from a template (optional)

A **workspace template** is a local YAML file that declares which repos to
register, which profiles to create, and which file references to add. Using
one skips Phases 2–4 for standard setups.

```bash
strata sln init --name xyz-workspace --template ./templates/standard-three-repo.yaml
```

**Template file format** (`standard-three-repo.yaml`):

```yaml
apiVersion: strata.huybrechts.xyz/v1
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

- `--template` accepts any local path (absolute or relative to `--work-path`).
- Remote / `@repo-name/...` template references are **not** supported — the file must be on disk before `init` runs.
- Repos registered from a template are **not** cloned automatically; run `strata repo sync` afterwards.
- At most one profile may set `activate: true`.

```bash
strata sln status
```

---

## Phase 2 — Register Repositories

Each external repo that contains config, Terraform, or YAML platform files must be
registered before it can be referenced.

### 2.1 Add repositories

```bash
# Register and clone in one step
strata repo add xyz-config         git@github.com:org/xyz-config.git         --branch main --path repos/xyz-config --clone
strata repo add xyz-infrastructure git@github.com:org/xyz-infrastructure.git --branch main --path repos/xyz-infrastructure --clone
strata repo add xyz-svc-traefik    git@github.com:org/xyz-svc-traefik.git    --branch main --path repos/xyz-svc-traefik --clone
```

Omit `--clone` to register without cloning and run `strata repo sync` separately.

### 2.2 Clone / pull them all (when not using `--clone`)

```bash
strata repo sync
```

Clones any repo not yet on disk; pulls repos already cloned. Re-run after upstream changes.

```bash
# Sync only one repo
strata repo sync --name xyz-infrastructure

# Hard reset dirty trees
strata repo sync --force
```

### 2.3 List registered repos

```bash
strata repo list
```

### 2.4 Check git state of all repos

```bash
# All registered repos
strata repo status

# Single repo
strata repo status --name xyz-infrastructure

# Include individual changed files
strata repo status --verbose
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
strata profile add dev
strata profile add stg
strata profile add prd
```

Use `--activate` to create and activate in one step:

```bash
strata profile add prd --activate
```

### 3.2 Activate the working profile

```bash
strata profile activate prd
```

### 3.3 List / inspect profiles

```bash
strata profile list
strata profile show prd
```

---

## Phase 4 — Register File References

Refs tell the build which YAML files to merge. References use `@repo-name/relative/path`
notation to point into registered repos.

### 4.1 Register configuration files (merged into platform config)

```bash
strata ref config add global-config @xyz-config/config/xyz-config.yaml       --profile prd
strata ref config add logging-config @xyz-config/config/xyz-logging.yaml      --profile prd
```

### 4.2 Register environment overlays

```bash
strata ref env add prd-env @xyz-config/environments/xyz-env-prd.yaml          --profile prd
```

### 4.3 Register secret files (plain file on disk — no vault layer yet)

```bash
strata ref secret add prd-secrets /run/secrets/xyz-prd.yaml                   --profile prd
```

### 4.3b Register data files (non-YAML assets)

`ref data` registers arbitrary files (JSON, TOML, scripts, certs) that the build copies
verbatim into the output folder. Use this for assets your Terraform modules or deployment
scripts need but that don't follow the platform YAML schema.

```bash
strata ref data add ca-bundle /run/certs/ca-bundle.pem --profile prd
strata ref data add tf-backend @xyz-config/backend.json --profile prd
```

```bash
strata ref config list --profile prd
strata ref config show global-config --profile prd    # preview the file content
```

### 4.5 Inspect resolved values for a deployment

Before building or deploying you can verify which concrete values the CLI would
use for every declared variable, secret, and feature flag:

```bash
# List all (secrets are masked: first 3 chars + *****)
strata values list -f repos/xyz-infrastructure/deployments/xyz-deploy-prd.yaml

# Filter to a single type
strata values list -f … --type secrets
strata values list -f … --type variables
strata values list -f … --type features

# Show only entries that failed to resolve
strata values list -f … --unresolved

# Also show the store reference (env var name, key path, flag id)
strata values list -f … --show-store

# Retrieve one or more values in full (secrets revealed)
strata values get -f … DB_PASSWORD API_KEY

# Write a value to its store backend
strata values set -f … -k DB_HOST --value "new-host.example.com"
strata values set -f … -k TLS_CERT --from-file cert.pem
cat key.pem | strata values set -f … -k SSH_KEY --stdin

# Diagnose resolution paths (no secrets revealed)
strata values resolve -f …
strata values resolve -f … -k DB_PASSWORD
strata values resolve -f … --probe          # also check backend reachability
```

Exit codes: `0` = all resolved, `3` = one or more entries failed.

JSON output is supported via `--output json`.

---

## Phase 4b — Create New Config Files

Use `strata new` to scaffold a new platform YAML file from a built-in template.
This is the fastest way to add namespaces, modules, providers, DNS zones,
network definitions, and other config files to your repo.

```bash
# See every available template
strata new --list

# Create a namespace config file in the current directory
strata new namespace my-app

# Create a module config, written to a specific folder
strata new module my-api --path repos/xyz-config/stack/

# Create a provider definition with a variable override
strata new provider azure --path repos/xyz-config/config/ --set owner=myteam

# Create a DNS zones file
strata new dns my-zones --path repos/xyz-config/dns/

# Create a network topology file
strata new network my-networks --path repos/xyz-config/network/
```

Each command writes a ready-to-edit YAML file with `meta.name` pre-filled and
all spec fields present as commented placeholders. Use `--overwrite` to replace
an existing file.

---

## Phase 5 — Validate YAML Files

Before building, validate individual YAML files:

### 5.1 Structural validation (Pydantic schema)

```bash
strata validate repos/xyz-config/config/xyz-config.yaml
strata validate repos/xyz-infrastructure/deployments/xyz-deploy-prd.yaml
```

Exit codes: `0` = valid, `3` = validation failure.

### 5.2 Deep validation (cross-references against active profile)

```bash
strata validate repos/xyz-infrastructure/deployments/xyz-deploy-prd.yaml --deep
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
strata build run -f repos/xyz-infrastructure/deployments/xyz-deploy-prd.yaml --dry-run
```

### 6.2 Full build

```bash
strata build run -f repos/xyz-infrastructure/deployments/xyz-deploy-prd.yaml
```

Reads:
- The deployment YAML file
- All `configfile` refs from the active profile
- All related workspace / stack YAML files referenced inside the deployment

Writes:
- `.strata/build/<deployment>/` — Terraform `.tfvars.json`, `platform.json`, rendered templates

> **Workspace-level resource kinds:** The workspace YAML can declare additional resource kinds
> that strata validates and builds into separate artifact files. Firewall rules are listed under
> `spec.firewalls` and produce `firewalls.auto.tfvars.json`. DNS zones follow the same pattern
> — list them under `spec.dns_zones` and the build produces `dns.auto.tfvars.json`. Network
> topologies are listed under `spec.networks` and produce `networks.auto.tfvars.json` — including
> CIDR overlap detection across subnets and peered networks.
> See [firewall.md](../config/firewall.md), [dns.md](../config/dns.md), and
> [network.md](../config/network.md) for schema details.

### 6.3 Clean build artifacts

```bash
strata build clean -f repos/xyz-infrastructure/deployments/xyz-deploy-prd.yaml
```

### 6.4 Preview what build run would change

```bash
# Full plan: artifact diff + terraform plan per stage
strata build plan -f repos/xyz-infrastructure/deployments/xyz-deploy-prd.yaml

# Artifact diff only (no terraform required)
strata build plan -f repos/xyz-infrastructure/deployments/xyz-deploy-prd.yaml --artifacts-only

# Limit terraform plan to one stage
strata build plan -f repos/xyz-infrastructure/deployments/xyz-deploy-prd.yaml --stage xyz-dc-eu-fr
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


---

## Phase 7 — Deploy

> **Requires:** Terraform CLI and configured integration credentials (Bitwarden, Vault, Azure Key Vault, etc.). Use `--dry-run` to run `terraform init → validate → plan` safely before applying.

Deploy executes provisioners (Terraform) against the built artifacts.

### 7.1 Dry-run (init + validate + plan — no apply)

```bash
strata deploy run -f repos/xyz-infrastructure/deployments/xyz-deploy-prd.yaml --dry-run
```

Runs: `terraform init` → `terraform validate` → `terraform plan`

### 7.2 Deploy a single stage (selective)

```bash
strata deploy run -f repos/xyz-infrastructure/deployments/xyz-deploy-prd.yaml --stage xyz-dc-eu-fr
```

### 7.3 Full deploy

```bash
strata deploy run -f repos/xyz-infrastructure/deployments/xyz-deploy-prd.yaml
```

Runs per stage (in order): `terraform init` → `terraform validate` → `terraform plan` → `terraform apply`

### 7.4 Force (skip approval gates)

```bash
strata deploy run -f repos/xyz-infrastructure/deployments/xyz-deploy-prd.yaml --force
```

### 7.5 Destroy (tear down infrastructure)

Destroy is its own command — not a flag on `deploy run`.

```bash
# Preview what would be removed (safe — no changes)
strata deploy destroy -f repos/xyz-infrastructure/deployments/xyz-deploy-prd.yaml --dry-run

# Tear down a single stage
strata deploy destroy -f repos/xyz-infrastructure/deployments/xyz-deploy-prd.yaml --stage xyz-dc-eu-fr --force

# Tear down all stages (--force required — runs terraform destroy -auto-approve)
strata deploy destroy -f repos/xyz-infrastructure/deployments/xyz-deploy-prd.yaml --force
```

- `--dry-run` runs `terraform plan -destroy` — shows exactly what would be removed, writes nothing.
- `--force` is required for the real destroy (enables `-auto-approve`).
- Without `--force` and without `--dry-run`, the command exits with an error.

### 7.6 Status (outputs, plan details, history)

```bash
# Live infrastructure outputs per stage (queries the Terraform backend)
strata deploy status -f repos/xyz-infrastructure/deployments/xyz-deploy-prd.yaml

# Single stage only
strata deploy status -f repos/xyz-infrastructure/deployments/xyz-deploy-prd.yaml --stage xyz-dc-eu-fr

# Decode the last saved .tfplan — no backend call, instant
strata deploy status -f repos/xyz-infrastructure/deployments/xyz-deploy-prd.yaml --plan

# Show execution history from workspace logs
strata deploy status --history
strata deploy status --history --lines 20
```

- Default (no flags): runs `terraform output -json` per stage — shows live endpoint URLs, resource IDs, etc.
- `--plan`: reads the `.tfplan` written by the last `deploy run --dry-run`. Shows resource add/change/destroy counts. No network required.

For execution history use `strata deploy history`.

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
strata deploy health -f repos/xyz-infrastructure/deployments/xyz-deploy-prd.yaml

# Single stage
strata deploy health -f repos/xyz-infrastructure/deployments/xyz-deploy-prd.yaml --stage xyz-dc-eu-fr
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
strata deploy history

# Limit to last 20 entries
strata deploy history --lines 20

# Filter by operation type
strata deploy history --operation run
strata deploy history --operation destroy

# Include execution IDs
strata deploy history --verbose
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
strata sln status

# View logs from last command
strata log list --last

# View last 100 lines at DEBUG level
strata log list --lines 100 --level DEBUG

# View logs for a specific execution
strata log list --execution-id <UUID>

# Show built-in workflow topics
strata help --list
strata help --topic quickstart
strata help --topic cross-repo
```

---

## Phase 9 — Maintenance

```bash
# After upgrading the strata package — refresh schemas, templates, devcontainer
strata sln update

# Pull latest from all registered repos
strata repo sync

# Wipe logs and temp artifacts
strata sln clean
strata sln clean --dry-run     # preview first

# Reset logging config to defaults
strata config log reset
```

---

## Full Example Session (New Workspace)

```bash
# -- One-time setup --
cd C:\src\workspace
strata sln init --name xyz-workspace

strata repo add xyz-config         git@github.com:org/xyz-config.git
strata repo add xyz-infrastructure git@github.com:org/xyz-infrastructure.git
strata repo add xyz-svc-traefik    git@github.com:org/xyz-svc-traefik.git
strata repo sync

strata profile add prd
strata profile activate prd

strata ref config add global-config  @xyz-config/config/xyz-config.yaml      --profile prd
strata ref config add logging-config @xyz-config/config/xyz-logging.yaml     --profile prd
strata ref env    add prd-env        @xyz-config/environments/xyz-env-prd.yaml --profile prd

# -- Validate --
strata validate repos/xyz-infrastructure/deployments/xyz-deploy-prd.yaml --deep

# -- Build --
strata build run -f repos/xyz-infrastructure/deployments/xyz-deploy-prd.yaml --dry-run
strata build run -f repos/xyz-infrastructure/deployments/xyz-deploy-prd.yaml

# -- Preview changes --
strata build plan -f repos/xyz-infrastructure/deployments/xyz-deploy-prd.yaml

# -- Deploy --
strata deploy run -f repos/xyz-infrastructure/deployments/xyz-deploy-prd.yaml --dry-run
strata deploy run -f repos/xyz-infrastructure/deployments/xyz-deploy-prd.yaml

# -- Inspect --
strata sln status
strata log list --last
```

---

## Day-to-Day Cycle (Existing Workspace)

```bash
# Pull latest config/infra changes
strata repo sync

# Re-build after upstream changes
strata build run -f repos/xyz-infrastructure/deployments/xyz-deploy-prd.yaml

# Review what would change
strata build plan -f repos/xyz-infrastructure/deployments/xyz-deploy-prd.yaml

# Deploy changes
strata deploy run -f repos/xyz-infrastructure/deployments/xyz-deploy-prd.yaml --dry-run
strata deploy run -f repos/xyz-infrastructure/deployments/xyz-deploy-prd.yaml

# Review what happened
strata log list --last
```

---

## Complete Command Reference

### Workspace

| Command                                         | Description                                                                      |
| ----------------------------------------------- | -------------------------------------------------------------------------------- |
| `strata sln init --name NAME [--template FILE]` | Initialize a new workspace; `--template` pre-populates repos, profiles, and refs |
| `strata sln export --name NAME [--dry-run]`     | Save current workspace as a reusable scaffold template                           |
| `strata sln status`                             | Show workspace health and integration availability                               |
| `strata sln update`                             | Refresh package-owned files (schemas, templates, devcontainer) after upgrade     |
| `strata sln clean [--dry-run]`                  | Remove logs and temp artifacts                                                   |
| `strata version`                                | Print CLI version                                                                |
| `strata help [--topic NAME]`                    | Show workflow guidance topics                                                    |

### Configuration

| Command                       | Description                           |
| ----------------------------- | ------------------------------------- |
| `strata config set KEY VALUE` | Persist a workspace-level CLI default |
| `strata config unset KEY`     | Remove a persisted default            |
| `strata config list`          | List all persisted defaults           |

Valid keys: `output`, `verbose`, `quiet`, `work_path`

### Logs

| Command                                            | Description                     |
| -------------------------------------------------- | ------------------------------- |
| `strata log list [--last] [--lines N] [--level L]` | View execution logs (read-only) |

> **Note:** To configure logging behaviour (levels, output format), use `strata config log` — see [Logging Config](#logging-config) below.

### Logging Config

| Command                           | Description                              |
| --------------------------------- | ---------------------------------------- |
| `strata config log list`          | Print current `logging.yaml`             |
| `strata config log get KEY`       | Get a single logging config value        |
| `strata config log set KEY VALUE` | Set a logging config value               |
| `strata config log unset KEY`     | Remove a logging config key              |
| `strata config log reset`         | Reset logging config to package defaults |

### Repositories

| Command                                                      | Description                                   |
| ------------------------------------------------------------ | --------------------------------------------- |
| `strata repo add NAME URL [--branch B] [--path P] [--clone]` | Register a repo; `--clone` clones immediately |
| `strata repo list [--name NAME]`                             | List registered repos                         |
| `strata repo status [--name NAME]`                           | Show git state (branch, dirty, ahead/behind)  |
| `strata repo remove NAME [--purge]`                          | Remove a repo (`--purge` deletes from disk)   |
| `strata repo sync [--name NAME] [--force]`                   | Clone / pull registered repos                 |

### Profiles

| Command                                | Description                                                   |
| -------------------------------------- | ------------------------------------------------------------- |
| `strata profile add NAME [--activate]` | Create a new profile; `--activate` sets it active immediately |
| `strata profile remove NAME`           | Delete a profile                                              |
| `strata profile list`                  | List all profiles                                             |
| `strata profile activate NAME`         | Set the active profile                                        |
| `strata profile show NAME`             | Show all refs registered on a profile                         |

### File References

All `ref` subgroups (`env`, `config`, `data`, `secret`) share:

| Command                                         | Description                |
| ----------------------------------------------- | -------------------------- |
| `strata ref <TYPE> add NAME PATH [--profile P]` | Register a file reference  |
| `strata ref <TYPE> remove NAME [--profile P]`   | Remove a file reference    |
| `strata ref <TYPE> list [--profile P]`          | List registered references |
| `strata ref <TYPE> show NAME [--profile P]`     | Display the file content   |

### Validation

| Command                         | Description                   |
| ------------------------------- | ----------------------------- |
| `strata validate FILE [--deep]` | Validate a platform YAML file |

### Build

| Command                                                    | Description                                                     |
| ---------------------------------------------------------- | --------------------------------------------------------------- |
| `strata build run -f FILE [--dry-run]`                     | Run the platform + Terraform build pipeline                     |
| `strata build plan -f FILE [--stage S] [--artifacts-only]` | Artifact diff + terraform plan per stage (reads only, temp dir) |
| `strata build clean -f FILE [--dry-run]`                   | Remove build artifacts                                          |

### Deploy

| Command                                                           | Description                                                          |
| ----------------------------------------------------------------- | -------------------------------------------------------------------- |
| `strata deploy run -f FILE [--stage S] [--force] [--dry-run]`     | Execute the deploy pipeline                                          |
| `strata deploy destroy -f FILE [--stage S] [--force] [--dry-run]` | Tear down infrastructure; `--dry-run` plans, `--force` auto-approves |
| `strata deploy status -f FILE [--stage S] [--plan]`               | Live Terraform outputs or saved plan details                         |
| `strata deploy history [--lines N] [--operation run\|destroy]`    | Execution history from workspace logs                                |
| `strata deploy health -f FILE [--stage S]`                        | Run `health_checks` defined in the deployment YAML                   |

### Values

| Command                                                               | Description                                                          |
| --------------------------------------------------------------------- | -------------------------------------------------------------------- |
| `strata values list -f FILE [--type T] [--show-store] [--unresolved]` | List all variables / secrets (masked) / feature flags                |
| `strata values get  -f FILE KEY [KEY …]`                              | Retrieve full resolved value(s) for specific keys (secrets revealed) |
| `strata values set  -f FILE -k KEY --value V`                         | Write a value to its configured store backend                        |
| `strata values resolve -f FILE [-k KEY] [--probe]`                    | Diagnose resolution paths without revealing values                   |


