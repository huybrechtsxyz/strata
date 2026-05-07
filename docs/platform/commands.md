# XYZ Platform — CLI Command Reference

All commands are invoked as `xyz <command> [options]` (or `uv run xyz-platform <command>`).

## Standard Options

These options are accepted by every command and subcommand:

| Option             | Type                      | Default       | Description                                                                                                                                                                   |
| ------------------ | ------------------------- | ------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `--work-path PATH` | path                      | auto-detected | Root workspace directory. Falls back to `XYZ_WORK_PATH` env var, then walks up from CWD looking for `.platform/`.                                                             |
| `--output FORMAT`  | `console`\|`text`\|`json` | `console`     | Output format. Defaults to `console` (human-readable) when omitted. `json` returns a structured envelope `{"success": bool, "data": ...}`. Mutually exclusive with `--quiet`. |
| `--verbose`        | flag                      | off           | Enable verbose output.                                                                                                                                                        |
| `--quiet`          | flag                      | off           | Suppress console output.                                                                                                                                                      |

## Exit Codes

| Code | Meaning                                                      |
| ---- | ------------------------------------------------------------ |
| `0`  | Success                                                      |
| `1`  | System / execution failure (crash, missing file, init error) |
| `2`  | Usage error — invalid CLI arguments (Click default)          |
| `3`  | Validation failure — file processed but schema-invalid       |

---

## Command Groups

| Group      | Subcommands                                                    | Description                                   |
| ---------- | -------------------------------------------------------------- | --------------------------------------------- |
| `init`     | —                                                              | Initialize a new solution workspace           |
| `clean`    | —                                                              | Remove workspace artifacts (logs, temp files) |
| `config`   | `set` `unset` `list`                                           | Manage persistent workspace defaults          |
| `status`   | —                                                              | Show workspace health                         |
| `audit`    | `list`; `log list` `log get` `log set` `log unset` `log reset` | View execution logs and manage log config     |
| `profile`  | `add` `remove` `list` `activate` `show`                        | Manage environment profiles                   |
| `ref`      | `envfile` `configfile` `datafile` `secretfile`                 | Manage file references within profiles        |
| `repo`     | `add` `remove` `list` `sync` `status`                          | Manage repositories in the solution           |
| `build`    | `run` `plan` `clean`                                           | Build platform and Terraform artifacts        |
| `validate` | —                                                              | Validate a single platform YAML file          |
| `deploy`   | `run` `destroy` `status` `history` `health`                    | Deploy platform using provisioners            |
| `values`   | `list` `get`                                                   | Inspect resolved deployment values            |
| `version`  | —                                                              | Show CLI version                              |
| `help`     | —                                                              | Show help topics                              |

---

## `init`

Initialize a new XYZ Platform solution workspace. Creates `.platform/` state directory and `solution.json`.

```
xyz init --name NAME [--from-template FILE] [standard options]
```

| Option                 | Required | Description                                                                                       |
| ---------------------- | -------- | ------------------------------------------------------------------------------------------------- |
| `--name NAME`          | ✅        | Name of the solution workspace                                                                    |
| `--from-template FILE` | —        | Path to a workspace template YAML file. Pre-populates repos, profiles, and refs. File must exist. |

**Exit codes:** 0 success · 1 failure · 2 missing `--name`

```bash
xyz init --name my-platform
xyz init --name my-platform --from-template templates/base.yaml
```

---

## `clean`

Remove workspace artifacts (log files, temp files) without touching solution state.

```
xyz clean [--dry-run] [standard options]
```

| Option      | Default | Description                                            |
| ----------- | ------- | ------------------------------------------------------ |
| `--dry-run` | off     | Report what would be deleted without removing anything |

```bash
xyz clean
xyz clean --dry-run
```

---

## `config`

Manage persistent workspace defaults stored in `.platform/cli.yaml`.

### `config set KEY VALUE`

```bash
xyz config set output json
xyz config set verbose true
```

Allowed keys: `output`, `verbose`, `quiet`, `work_path`.

### `config unset KEY`

```bash
xyz config unset output
```

### `config list`

```bash
xyz config list
xyz config list --output json
```

---

## `status`

Show workspace health: solution identity, active profile, repositories, and integration availability.

```
xyz status [standard options]
```

Works inside and outside an initialized workspace (degrades gracefully when `solution.json` is absent).

```bash
xyz status
xyz status --output json
```

---

## `audit`

View execution logs and manage logging configuration.

### `audit list`

```
xyz audit list [--lines N] [--minutes N] [--level LEVEL] [--execution-id ID] [--last]
```

| Option              | Default | Description                                                              |
| ------------------- | ------- | ------------------------------------------------------------------------ |
| `--lines N`         | 50      | Maximum number of log entries to show                                    |
| `--minutes N`       | —       | Show only entries from the last N minutes                                |
| `--level LEVEL`     | —       | Filter by minimum log level: `DEBUG` `INFO` `WARNING` `ERROR` `CRITICAL` |
| `--execution-id ID` | —       | Filter to a specific execution ID                                        |
| `--last`            | off     | Show logs for the most recent command execution                          |

```bash
xyz audit list
xyz audit list --last
xyz audit list --level ERROR --lines 20
```

### `audit log list`

Show the current `logging.yaml` configuration.

```bash
xyz audit log list
```

### `audit log get KEY`

Get a single logging config value by dot-notation key.

```bash
xyz audit log get level
```

### `audit log set KEY VALUE`

Set a logging config value. Use `level` as shorthand for log level.

```bash
xyz audit log set level DEBUG
```

---

## `profile`

Manage environment profiles within the solution.

### `profile add NAME`

```bash
xyz profile add staging
```

### `profile remove NAME`

```bash
xyz profile remove staging
```

### `profile list [--name NAME]`

```bash
xyz profile list
xyz profile list --name staging
```

### `profile activate NAME`

Activate a profile (deactivates all others).

```bash
xyz profile activate staging
```

### `profile show NAME`

Show all registered ref paths for a profile, grouped by type.

```bash
xyz profile show staging
```

---

## `ref`

Manage file references (envfile, configfile, datafile, secretfile) within profiles.

Each file type has its own subgroup: `ref envfile`, `ref configfile`, `ref datafile`, `ref secretfile`. All four expose the same four subcommands.

All `ref` subcommands accept `--profile NAME` (optional; defaults to the active profile).

### `ref <type> add NAME PATH`

```bash
xyz ref envfile add base-env .env
xyz ref configfile add app-config @myrepo/config/app.yaml --profile staging
```

### `ref <type> remove NAME`

```bash
xyz ref envfile remove base-env --profile staging
```

### `ref <type> list`

```bash
xyz ref configfile list
xyz ref configfile list --profile staging
```

### `ref <type> show NAME`

Display the file content of a ref path entry.

```bash
xyz ref configfile show app-config --profile staging
```

---

## `repo`

Manage repositories registered in the solution.

### `repo add NAME URL`

```
xyz repo add NAME URL [--branch BRANCH] [--path PATH] [--clone]
```

| Option            | Default | Description                                                |
| ----------------- | ------- | ---------------------------------------------------------- |
| `--branch BRANCH` | `main`  | Default branch to track (git repos only)                   |
| `--path PATH`     | —       | Local path relative to work-path (default: `repos/<name>`) |
| `--clone`         | off     | Clone the repository immediately after registering         |

**URL / path auto-detection:**

- Remote git URL (any `https://`, `git@`, etc.) → registered as type `gitops`. Use `--branch` and `--clone` as needed.
- Local path starting with a drive letter (`C:/…`, `C:\…`) or a network path (`//server/…`, `\\server\…`) → registered as type `local`. The path must exist and be a directory at registration time. `--branch` and `--clone` are ignored. No sync is needed — the folder is already on disk.

```bash
# Remote git repository
xyz repo add platform https://github.com/org/platform.git
xyz repo add platform https://github.com/org/platform.git --branch develop --clone

# Local folder (Windows drive path)
xyz repo add infra C:/repos/xyz-infrastructure

# Local network share
xyz repo add shared //fileserver/repos/platform
```

### `repo list [--name NAME]`

```bash
xyz repo list
xyz repo list --name platform
```

### `repo remove NAME [--purge]`

`--purge` also deletes the local clone directory from disk.

```bash
xyz repo remove old-repo
xyz repo remove old-repo --purge
```

### `repo sync [--name NAME] [--force]`

Clone or pull repositories. `--force` hard-resets dirty working trees instead of skipping them.

```bash
xyz repo sync
xyz repo sync --name platform --force
```

### `repo status [--name NAME]`

Show git working-tree state for registered repositories.

```bash
xyz repo status
xyz repo status --name platform
```

---

## `build`

> **Note:** `build` requires the Terraform CLI (`terraform`) to be installed and on `PATH`. The `--dry-run` mode (plan only, no files written) works without it. Full artifact generation targets production-ready environments.

Build platform and Terraform artifacts from a deployment YAML file.

All `build` subcommands accept `--file/-f PATH` to specify the deployment file.

### `build run`

```
xyz build run [-f FILE] [--dry-run] [standard options]
```

Runs the full build pipeline (platform builder → terraform builder). `--dry-run` validates and plans without writing output files.

```bash
xyz build run -f xyz-deploy-prd.yaml
xyz build run --dry-run
```

### `build plan`

```
xyz build plan [-f FILE] [--stage NAME] [--artifacts-only] [standard options]
```

Builds into a temp directory, diffs against existing artifacts, then runs `terraform init → validate → plan` per stage. Nothing is written to the real build path.

| Option             | Description                                   |
| ------------------ | --------------------------------------------- |
| `--stage NAME`     | Limit terraform plan to one stage             |
| `--artifacts-only` | Skip terraform plan — show artifact diff only |

```bash
xyz build plan -f xyz-deploy-prd.yaml
xyz build plan --stage production --artifacts-only
```

### `build clean`

```
xyz build clean [-f FILE] [--dry-run] [standard options]
```

Remove build artifacts for the selected deployment.

```bash
xyz build clean -f xyz-deploy-prd.yaml
xyz build clean --dry-run
```

---

## `validate`

Validate a single platform YAML file against its kind-specific schema.

```
xyz validate FILE_PATH [--deep] [standard options]
```

| Option   | Description                                                                                                                                               |
| -------- | --------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `--deep` | Enable Phase 2 (cross-reference) validation against the active profile's configuration sources. Requires an initialized workspace with an active profile. |

**Exit codes:** 0 valid · 1 system failure · 2 missing argument · 3 schema-invalid

```bash
xyz validate config/xyz-config.yaml
xyz validate config/xyz-ws-platform.yaml --deep
```

---

## `deploy`

> **Note:** `deploy` requires the Terraform CLI and configured integration credentials (Bitwarden, Vault, Azure Key Vault, etc.). Use `--dry-run` to run `terraform init → validate → plan` without applying any changes.

Deploy platform infrastructure using provisioners defined in a deployment YAML file.

All `deploy` subcommands accept `--file/-f PATH` and `--stage NAME`.

### `deploy run`

```
xyz deploy run [-f FILE] [--stage NAME] [--force] [--dry-run] [standard options]
```

Execute the deploy pipeline (setup → check → plan → apply).

| Option         | Description                                  |
| -------------- | -------------------------------------------- |
| `--stage NAME` | Limit execution to one deployment stage      |
| `--force`      | Skip confirmation prompts and approval gates |
| `--dry-run`    | Validate and plan only — no provisioners run |

```bash
xyz deploy run -f xyz-deploy-prd.yaml
xyz deploy run --stage production --dry-run
xyz deploy run --force
```

### `deploy destroy`

```
xyz deploy destroy [-f FILE] [--stage NAME] [--force] [--dry-run] [standard options]
```

Tear down provisioned infrastructure. `--force` is required for a real destroy (runs non-interactively). `--dry-run` runs `terraform plan -destroy` only.

```bash
xyz deploy destroy -f xyz-deploy-prd.yaml --dry-run
xyz deploy destroy --stage production --force
```

### `deploy status`

```
xyz deploy status [-f FILE] [--stage NAME] [--plan] [standard options]
```

Show live Terraform outputs or saved plan details. `--plan` reads the last saved `.tfplan` file without backend calls.

```bash
xyz deploy status -f xyz-deploy-prd.yaml
xyz deploy status --stage production --plan
```

### `deploy history`

```
xyz deploy history [--lines N] [--operation run|destroy] [standard options]
```

Show deployment execution history from workspace logs.

```bash
xyz deploy history
xyz deploy history --lines 20 --operation run
```

### `deploy health`

```
xyz deploy health -f FILE [--stage NAME] [standard options]
```

Run health checks against provisioned infrastructure stages. Exit code 3 if any check fails.

```bash
xyz deploy health -f xyz-deploy-prd.yaml
xyz deploy health -f xyz-deploy-prd.yaml --stage production
```

---

## `values`

Inspect resolved deployment values (variables, secrets, feature flags).

### `values list`

```
xyz values list -f FILE [--stage NAME] [--type TYPE] [--show-store] [--unresolved] [standard options]
```

| Option                                | Description                                                    |
| ------------------------------------- | -------------------------------------------------------------- |
| `-f FILE`                             | ✅ Required. Path to the deployment YAML file                   |
| `--stage NAME`                        | Use the environment from this stage (default: first stage)     |
| `--type variables\|secrets\|features` | Show only this value type                                      |
| `--show-store`                        | Include the store reference (env var name, key path) in output |
| `--unresolved`                        | Show only entries that failed to resolve                       |

Secrets are masked (first 3 chars + `*****`). Exit code 3 if any entry is unresolved.

```bash
xyz values list -f xyz-deploy-prd.yaml
xyz values list -f xyz-deploy-prd.yaml --type secrets --show-store
xyz values list -f xyz-deploy-prd.yaml --unresolved
```

### `values get`

```
xyz values get -f FILE KEY... [standard options]
```

Retrieve the full resolved value for one or more keys. **Secrets are revealed in plain text.**

```bash
xyz values get -f xyz-deploy-prd.yaml DB_PASSWORD
xyz values get -f xyz-deploy-prd.yaml DB_PASSWORD API_KEY
```

---

## `version`

Show the current CLI version.

```bash
xyz version
xyz version --output json
```

---

## `help`

Show help topics for common workflows.

```bash
xyz help
xyz help quickstart
xyz help profiles
```

Available topics: `quickstart`, `workspace`, `profiles`, `refs`, `config-merge`, `cross-repo`, `environments`, `troubleshooting`.