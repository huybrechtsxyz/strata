# strata — CLI Command Reference

All commands are invoked as `strata <command> [options]` (or `uv run strata <command>`).

## Standard Options

These options are accepted by every command and subcommand:

| Option             | Type                      | Default       | Description                                                                                                                                                                   |
| ------------------ | ------------------------- | ------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `--work-path PATH` | path                      | auto-detected | Root workspace directory. Falls back to `STRATA_WORK_PATH` env var, then walks up from CWD looking for `.strata/`.                                                            |
| `--output FORMAT`  | `console`\|`text`\|`json` | `console`     | Output format. Defaults to `console` (human-readable) when omitted. `json` returns a structured envelope `{"success": bool, "data": ...}`. Mutually exclusive with `--quiet`. |
| `--verbose`        | flag                      | off           | Enable verbose output.                                                                                                                                                        |
| `--quiet`          | flag                      | off           | Suppress console output.                                                                                                                                                      |

> **Automation / AI agents:** Always use `--output json` (or set `STRATA_OUTPUT=json`). Every CLI flag has an `XYZ_<OPTION>` environment-variable equivalent — set them once rather than passing flags on every call. In console mode, errors are written to **stderr**; the JSON envelope always goes to **stdout**.

## Exit Codes

| Code | Meaning                                                      |
| ---- | ------------------------------------------------------------ |
| `0`  | Success                                                      |
| `1`  | System / execution failure (crash, missing file, init error) |
| `2`  | Usage error — invalid CLI arguments (Click default)          |
| `3`  | Validation failure — file processed but schema-invalid       |

---

## Command Groups

| Group       | Subcommands                                                                  | Description                                             |
| ----------- | ---------------------------------------------------------------------------- | ------------------------------------------------------- |
| `sln`       | `init` `clean` `status` `export`                                             | Solution workspace lifecycle                            |
| `config`    | `set` `unset` `list`; `log list` `log get` `log set` `log unset` `log reset` | Manage persistent workspace defaults and logging config |
| `audit` †   | `list`                                                                       | View execution history logs (read-only)                 |
| `profile` † | `add` `remove` `list` `activate` `show`                                      | Manage environment profiles                             |
| `ref` †     | `env` `config` `data` `secret`                                               | Manage file references within profiles                  |
| `repo` †    | `add` `remove` `list` `sync` `status`                                        | Manage repositories in the solution                     |
| `build` †   | `run` `plan` `clean`                                                         | Build platform and Terraform artifacts                  |
| `validate`  | —                                                                            | Validate a single platform YAML file                    |
| `schema`    | `list` `get`                                                                 | Inspect JSON schemas for platform YAML kinds            |
| `deploy` †  | `run` `destroy` `status` `history` `health`                                  | Deploy platform using provisioners                      |
| `values` †  | `list` `get`                                                                 | Inspect resolved deployment values                      |
| `vars` †    | `set` `unset` `list`                                                         | Manage team-shared template variables                   |
| `tools`     | `status` `check` `install`                                                   | Manage and inspect external tool integrations           |
| `new` †     | —                                                                            | Create a platform config file from a template           |
| `version`   | —                                                                            | Show CLI version                                        |
| `help`      | —                                                                            | Show help topics                                        |

> **†** Requires an initialized workspace (`.strata/` directory). Run `strata sln init --name NAME` first.

---

## `sln`

Solution workspace lifecycle commands.

### `sln init`

Initialize a new strata solution workspace. Creates the `.strata/` state directory, workspace defaults, and a ready-to-use `.devcontainer/` for VS Code Dev Containers and GitHub Codespaces.

```
strata sln init --name NAME [--template NAME-OR-PATH] [standard options]
```

| Option                    | Required | Description                                                                                                                    |
| ------------------------- | -------- | ------------------------------------------------------------------------------------------------------------------------------ |
| `--name NAME`             | ✅        | Name of the solution workspace                                                                                                 |
| `--template NAME-OR-PATH` | —        | Built-in template name (e.g. `aks`) or path to a local template folder containing `scaffold/` and an optional `template.yaml`. |

**Files created:**

| Path                              | Description                                                                |
| --------------------------------- | -------------------------------------------------------------------------- |
| `.strata/project.json`            | Solution registry                                                          |
| `.strata/cli.yaml`                | Workspace CLI defaults                                                     |
| `.strata/logging.yaml`            | Logging configuration                                                      |
| `.devcontainer/devcontainer.json` | Dev container definition (Python 3.13, Terraform, Azure CLI, kubectl/Helm) |
| `.devcontainer/post-create.sh`    | Post-create script — installs `strata` and sets up shell completion        |

All `.devcontainer/` files are written **idempotently** — existing files are never overwritten.

**Exit codes:** 0 success · 1 failure · 2 missing `--name`

```bash
strata sln init --name my-platform
strata sln init --name my-platform --template .strata/templates/my-corp-base/
```

> **Dev container:** After `strata sln init`, open the workspace in VS Code and select **Reopen in Container** to start a pre-configured environment with all tools installed. The container also works with GitHub Codespaces.

### `sln clean`

Remove workspace artifacts (log files, temp files) without touching solution state.

```
strata sln clean [--dry-run] [standard options]
```

| Option      | Default | Description                                            |
| ----------- | ------- | ------------------------------------------------------ |
| `--dry-run` | off     | Report what would be deleted without removing anything |

```bash
strata sln clean
strata sln clean --dry-run
```

### `sln status`

Show workspace health: solution identity, active profile, repositories, and integration availability.

```
strata sln status [standard options]
```

Works inside and outside an initialized workspace (degrades gracefully when `solution.json` is absent).

```bash
strata sln status
strata sln status --output json
```

### `sln export`

Export the current workspace as a reusable scaffold template. Copies all workspace files into `.strata/templates/<name>/scaffold/`, replaces every occurrence of the solution name with `${solution_name}` in file content and file paths, then generates a `template.yaml` manifest.

```
strata sln export --name NAME [--force] [--dry-run] [standard options]
```

| Option        | Required | Description                                                             |
| ------------- | -------- | ----------------------------------------------------------------------- |
| `--name NAME` | ✅        | Template name — used as the output directory under `.strata/templates/` |
| `--force`     | —        | Overwrite an existing template with the same name                       |
| `--dry-run`   | —        | List files that would be copied without writing anything                |

**Output structure:**

| Path                                     | Description                                           |
| ---------------------------------------- | ----------------------------------------------------- |
| `.strata/templates/<name>/scaffold/`     | Workspace files with `${solution_name}` substitutions |
| `.strata/templates/<name>/template.yaml` | Template manifest (name, description, variables)      |

**Excluded from export:** `.git/`, `repos/`, `.venv/`, `node_modules/`, `.strata/logs/`, `__pycache__/`, `*.pyc`, `*.log`

**Exit codes:** 0 success · 1 failure (includes target directory already exists without `--force`)

```bash
strata sln export --name my-corp-base
strata sln export --name my-corp-base --dry-run
strata sln export --name my-corp-base --force
```

> **Next steps:** Use the exported template with `strata sln init --name new-ws --template .strata/templates/my-corp-base/`

---

## `vars`

Manage team-shared template variables stored in `solution.json`. Variables are substituted as `${key}` in platform YAML files.

| Subcommand      | Description                          |
| --------------- | ------------------------------------ |
| `set KEY VALUE` | Set or overwrite a template variable |
| `unset KEY`     | Remove a template variable           |
| `list`          | Show all current template variables  |

```bash
strata vars set owner myteam
strata vars unset owner
strata vars list
strata vars list --output json
```

---

## `tools`

Manage and inspect external tool integrations (Terraform, Docker, kubectl, Helm, Azure CLI, etc.).

| Subcommand     | Description                                                      |
| -------------- | ---------------------------------------------------------------- |
| `status`       | List all known integrations and their availability               |
| `check NAME`   | Deep-check a single integration (version, auth, connectivity)    |
| `install NAME` | Show download URL, env vars, and auth methods for an integration |

`tools install` accepts `--env-file PATH` to write a commented env-var template to a file.

```bash
strata tools status
strata tools check terraform
strata tools install terraform
strata tools install terraform --env-file .env.template
```

---

## `new`

Create a new platform configuration file from a built-in or custom template.

```
strata new TEMPLATE NAME [--path PATH] [--overwrite] [--set KEY=VALUE ...] [standard options]
strata new --list
```

| Option / Argument | Description                                                |
| ----------------- | ---------------------------------------------------------- |
| `TEMPLATE`        | Template name (e.g. `namespace`, `provider`, `workspace`)  |
| `NAME`            | Written into `meta.name` and used in the output filename   |
| `--path PATH`     | Output file path or directory (default: current directory) |
| `--overwrite`     | Overwrite the output file if it already exists             |
| `--set KEY=VALUE` | Override a template variable (repeatable)                  |
| `--list`          | List available templates and exit                          |

```bash
strata new namespace my-app
strata new provider azure --path config/
strata new workspace my-ws --set owner=myteam
strata new --list
```

---

## `config`

Manage persistent workspace defaults stored in `.strata/cli.yaml`.

### `config set KEY VALUE`

```bash
strata config set output json
strata config set verbose true
```

Allowed keys: `output`, `verbose`, `quiet`, `work_path`.

### `config unset KEY`

```bash
strata config unset output
```

### `config list`

```bash
strata config list
strata config list --output json
```

### `config log` {#config-log}

Manage `logging.yaml` — the workspace logging configuration.

| Subcommand        | Description                                   |
| ----------------- | --------------------------------------------- |
| `log list`        | Show the full current `logging.yaml`          |
| `log get KEY`     | Get a single value by dot-notation key        |
| `log set KEY VAL` | Set a logging config value                    |
| `log unset KEY`   | Remove a logging config key (restore default) |
| `log reset`       | Reset `logging.yaml` to package defaults      |

```bash
strata config log list
strata config log get level
strata config log set level DEBUG
strata config log unset level
strata config log reset
```

---

## `audit`

View execution history logs (read-only). Logging configuration has moved to `config log`.

### `audit list`

```
strata audit list [--lines N] [--minutes N] [--level LEVEL] [--execution-id ID] [--last]
```

| Option              | Default | Description                                                              |
| ------------------- | ------- | ------------------------------------------------------------------------ |
| `--lines N`         | 50      | Maximum number of log entries to show                                    |
| `--minutes N`       | —       | Show only entries from the last N minutes                                |
| `--level LEVEL`     | —       | Filter by minimum log level: `DEBUG` `INFO` `WARNING` `ERROR` `CRITICAL` |
| `--execution-id ID` | —       | Filter to a specific execution ID                                        |
| `--last`            | off     | Show logs for the most recent command execution                          |

```bash
strata audit list
strata audit list --last
strata audit list --level ERROR --lines 20
```

> **Note:** `audit log` subcommands have moved to `config log`. See [config log](#config-log) below.

---

## `profile`

Manage environment profiles within the solution.

### `profile add NAME`

```bash
strata profile add staging
```

### `profile remove NAME`

```bash
strata profile remove staging
```

### `profile list [--name NAME]`

```bash
strata profile list
strata profile list --name staging
```

### `profile activate NAME`

Activate a profile (deactivates all others).

```bash
strata profile activate staging
```

### `profile show NAME`

Show all registered ref paths for a profile, grouped by type.

```bash
strata profile show staging
```

---

## `ref`

Manage file references (env, config, data, secret) within profiles.

Each file type has its own subgroup: `ref env`, `ref config`, `ref data`, `ref secret`. All four expose the same four subcommands.

All `ref` subcommands accept `--profile NAME` (optional; defaults to the active profile).

### `ref <type> add NAME PATH`

```bash
strata ref env add base-env .env
strata ref config add app-config @myrepo/config/app.yaml --profile staging
```

### `ref <type> remove NAME`

```bash
strata ref env remove base-env --profile staging
```

### `ref <type> list`

```bash
strata ref config list
strata ref config list --profile staging
```

### `ref <type> show NAME`

Display the file content of a ref path entry.

```bash
strata ref config show app-config --profile staging
```

---

## `repo`

Manage repositories registered in the solution.

### `repo add NAME URL`

```
strata repo add NAME URL [--branch BRANCH] [--path PATH] [--clone]
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
strata repo add platform https://github.com/org/platform.git
strata repo add platform https://github.com/org/platform.git --branch develop --clone

# Local folder (Windows drive path)
strata repo add infra C:/repos/xyz-infrastructure

# Local network share
strata repo add shared //fileserver/repos/platform
```

### `repo list [--name NAME]`

```bash
strata repo list
strata repo list --name platform
```

### `repo remove NAME [--purge]`

`--purge` also deletes the local clone directory from disk.

```bash
strata repo remove old-repo
strata repo remove old-repo --purge
```

### `repo sync [--name NAME] [--force]`

Clone or pull repositories. `--force` hard-resets dirty working trees instead of skipping them.

```bash
strata repo sync
strata repo sync --name platform --force
```

### `repo status [--name NAME]`

Show git working-tree state for registered repositories.

```bash
strata repo status
strata repo status --name platform
```

---

## `build`

> **Note:** `build` requires the Terraform CLI (`terraform`) to be installed and on `PATH`. The `--dry-run` mode (plan only, no files written) works without it. Full artifact generation targets production-ready environments.

Build platform and Terraform artifacts from a deployment YAML file.

All `build` subcommands accept `--file/-f PATH` to specify the deployment file.

### `build run`

```
strata build run [-f FILE] [--dry-run] [standard options]
```

Runs the full build pipeline (platform builder → terraform builder). `--dry-run` validates and plans without writing output files.

```bash
strata build run -f xyz-deploy-prd.yaml
strata build run --dry-run
```

### `build plan`

```
strata build plan [-f FILE] [--stage NAME] [--artifacts-only] [standard options]
```

Builds into a temp directory, diffs against existing artifacts, then runs `terraform init → validate → plan` per stage. Nothing is written to the real build path.

| Option             | Description                                   |
| ------------------ | --------------------------------------------- |
| `--stage NAME`     | Limit terraform plan to one stage             |
| `--artifacts-only` | Skip terraform plan — show artifact diff only |

```bash
strata build plan -f xyz-deploy-prd.yaml
strata build plan --stage production --artifacts-only
```

### `build clean`

```
strata build clean [-f FILE] [--dry-run] [standard options]
```

Remove build artifacts for the selected deployment.

```bash
strata build clean -f xyz-deploy-prd.yaml
strata build clean --dry-run
```

---

## `validate`

Validate a single platform YAML file against its kind-specific schema.

```
strata validate FILE_PATH [--deep] [standard options]
```

| Option   | Description                                                                                                                                               |
| -------- | --------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `--deep` | Enable Phase 2 (cross-reference) validation against the active profile's configuration sources. Requires an initialized workspace with an active profile. |

**Exit codes:** 0 valid · 1 system failure · 2 missing argument · 3 schema-invalid

```bash
strata validate config/xyz-config.yaml
strata validate config/xyz-ws-platform.yaml --deep
```

---

## `schema`

Inspect JSON schemas for platform YAML document kinds. Useful for editors, linters, and AI agents that need to understand what fields a document type requires.

### `schema list`

List all supported platform document kinds.

```
strata schema list [--output FORMAT]
```

**`--output json`** returns `{"kinds": ["configuration", "deployment", ...]}`. **`--output text`** prints one kind per line.

```bash
strata schema list
strata schema list --output json
```

### `schema get KIND`

Emit the full JSON Schema for a platform document kind.

```
strata schema get KIND [--output FORMAT]
```

Default and `--output json` both emit the complete Pydantic-generated JSON Schema. `--output text` shows a compact summary (required fields and top-level property names).

**Valid kinds:** `configuration` `deployment` `environment` `firewall` `module` `namespace` `platform_model` `provider` `resource` `workspace`

**Exit codes:** 0 success · 2 unknown kind

```bash
strata schema get deployment
strata schema get deployment --output json
strata schema get environment --output text
```

---

## `deploy`

> **Note:** `deploy` requires the Terraform CLI and configured integration credentials (Bitwarden, Vault, Azure Key Vault, etc.). Use `--dry-run` to run `terraform init → validate → plan` without applying any changes.

Deploy platform infrastructure using provisioners defined in a deployment YAML file.

All `deploy` subcommands accept `--file/-f PATH` and `--stage NAME`.

### `deploy run`

```
strata deploy run [-f FILE] [--stage NAME] [--force] [--dry-run] [standard options]
```

Execute the deploy pipeline (setup → check → plan → apply).

| Option         | Description                                  |
| -------------- | -------------------------------------------- |
| `--stage NAME` | Limit execution to one deployment stage      |
| `--force`      | Skip confirmation prompts and approval gates |
| `--dry-run`    | Validate and plan only — no provisioners run |

```bash
strata deploy run -f xyz-deploy-prd.yaml
strata deploy run --stage production --dry-run
strata deploy run --force
```

### `deploy destroy`

```
strata deploy destroy [-f FILE] [--stage NAME] [--force] [--dry-run] [standard options]
```

Tear down provisioned infrastructure. `--force` is required for a real destroy (runs non-interactively). `--dry-run` runs `terraform plan -destroy` only.

```bash
strata deploy destroy -f xyz-deploy-prd.yaml --dry-run
strata deploy destroy --stage production --force
```

### `deploy status`

```
strata deploy status [-f FILE] [--stage NAME] [--plan] [standard options]
```

Show live Terraform outputs or saved plan details. `--plan` reads the last saved `.tfplan` file without backend calls.

```bash
strata deploy status -f xyz-deploy-prd.yaml
strata deploy status --stage production --plan
```

### `deploy history`

```
strata deploy history [--lines N] [--operation run|destroy] [standard options]
```

Show deployment execution history from workspace logs.

```bash
strata deploy history
strata deploy history --lines 20 --operation run
```

### `deploy health`

```
strata deploy health -f FILE [--stage NAME] [standard options]
```

Run health checks against provisioned infrastructure stages. Exit code 3 if any check fails.

```bash
strata deploy health -f xyz-deploy-prd.yaml
strata deploy health -f xyz-deploy-prd.yaml --stage production
```

---

## `values`

Inspect resolved deployment values (variables, secrets, feature flags).

### `values list`

```
strata values list -f FILE [--stage NAME] [--type TYPE] [--show-store] [--unresolved] [standard options]
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
strata values list -f xyz-deploy-prd.yaml
strata values list -f xyz-deploy-prd.yaml --type secrets --show-store
strata values list -f xyz-deploy-prd.yaml --unresolved
```

### `values get`

```
strata values get -f FILE KEY... [standard options]
```

Retrieve the full resolved value for one or more keys. **Secrets are revealed in plain text.**

```bash
strata values get -f xyz-deploy-prd.yaml DB_PASSWORD
strata values get -f xyz-deploy-prd.yaml DB_PASSWORD API_KEY
```

---

## `version`

Show the current CLI version.

```bash
strata version
strata version --output json
```

---

## `help`

Show help topics for common workflows.

```bash
strata help
strata help quickstart
strata help profiles
```

Available topics: `quickstart`, `workspace`, `profiles`, `refs`, `config-merge`, `cross-repo`, `environments`, `troubleshooting`.