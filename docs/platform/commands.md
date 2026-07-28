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
| `--no-color`       | flag                      | off           | Disable all ANSI color output. Also honoured via the `NO_COLOR` env var ([no-color.org](https://no-color.org)) or `STRATA_NO_COLOR`.                                          |

> **Automation / AI agents:** Always use `--output json` (or set `STRATA_OUTPUT=json`). Every CLI flag has an `XYZ_<OPTION>` environment-variable equivalent — set them once rather than passing flags on every call. In console mode, errors are written to **stderr**; the JSON envelope always goes to **stdout**.

## Exit Codes

| Code | Meaning                                                                                                                           |
| ---- | --------------------------------------------------------------------------------------------------------------------------------- |
| `0`  | Success                                                                                                                           |
| `1`  | System / execution failure (crash, missing file, init error)                                                                      |
| `2`  | Usage error — invalid CLI arguments (Click default)                                                                               |
| `3`  | Validation failure — file processed but schema-invalid                                                                            |
| `4`  | Lock conflict — another deployment holds the lock; safe to retry after delay. Only returned by `deploy run` and `deploy destroy`. |

> **Using strata in CI/CD?** See [ci-integration.md](ci-integration.md) for complete GitHub Actions and Azure Pipelines examples that leverage these exit codes.

---

## Idempotency & Retry Safety

This table answers: *"If this command fails, is it safe to re-run without manual cleanup?"*

| Category                         | Commands                                                                                                                                                                                                        | Safe to re-run?                                                                                                                                                                                        |
| -------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **Read-only**                    | All `status`, `show`, `list`, `get`, `history`, `health`, `info`, `output`, `graph`, `check`, `export` subcommands; `validate run`; `guide`; `version`; `schema *`; `policy list`; `tools status`; `completion` | ✅ Always safe — no state is written                                                                                                                                                                    |
| **Write-idempotent**             | `sln init`, `sln update`, `config set/unset`, `profile add/activate`, `ref * add/remove`, `repo add/remove/sync`, `vars set/unset`, `values set`, `build plan`, `build clean`                                   | ✅ Safe — produces the same result each time; re-running after failure leaves no partial state                                                                                                          |
| **Retry-safe with caveats**      | `build run`, `build sbom`, `deploy run`                                                                                                                                                                         | ⚠️ Safe to retry — provisioners are designed for re-entrant execution, but a failed deploy may leave infrastructure in an intermediate state; inspect `deploy status` before retrying                   |
| **Destructive / side-effecting** | `deploy destroy`, `service destroy`, `sln clean`, `secret put`, `secret rotate`, `audit resend`                                                                                                                 | ❌ Manual review required before re-running — these commands delete infrastructure, overwrite secret values in external stores, or re-send audit records that may already have been partially processed |

> **Lock conflicts (exit 4):** If `deploy run` or `deploy destroy` exits with code 4, another process holds the deployment lock. Wait for it to release (or use `deploy lock status` to inspect), then retry. Do **not** use `deploy lock release` unless the lock is stale from a crashed pipeline.

---

## Command Groups

| Group        | Subcommands                                                                            | Description                                               |
| ------------ | -------------------------------------------------------------------------------------- | --------------------------------------------------------- |
| `sln`        | `init` `update` `clean` `status` `export`                                              | Solution workspace lifecycle                              |
| `config`     | `set` `unset` `list`; `log list` `log get` `log set` `log unset` `log reset`           | Manage persistent workspace defaults and logging config   |
| `log` †      | `list`                                                                                 | View execution logs (read-only)                           |
| `profile` †  | `add` `remove` `list` `activate` `show`                                                | Manage environment profiles                               |
| `ref` †      | `env` `config` `data` `secret`                                                         | Manage file references within profiles                    |
| `repo` †     | `add` `remove` `list` `sync` `status`                                                  | Manage repositories in the solution                       |
| `build` †    | `run` `plan` `clean` `sbom`                                                            | Build platform and Terraform artifacts; generate SBOMs    |
| `validate`   | `run` `graph`                                                                          | Validate YAML files and visualize workspace dependencies  |
| `env` †      | `info` `output` `show` `status` `drift` `doctor`                                       | Inspect environment configuration and infrastructure      |
| `guide`      | —                                                                                      | Show workspace setup progress and suggest the next action |
| `console` †  | —                                                                                      | Interactive workspace REPL                                |
| `schema`     | `list` `get`                                                                           | Inspect JSON schemas for platform YAML kinds              |
| `policy` †   | `list` `check`                                                                         | Inspect and evaluate deployment guardrails                |
| `secret`     | `generate` `mask`                                                                      | Generate and manage secret values                         |
| `audit` †    | `changes` `resend` `export` `diff`                                                     | Query deploy-log evidence and forward to audit sinks      |
| `deploy` †   | `run` `destroy` `show` `status` `history` `health` `drift` `output` `outputs` `lock *` | Deploy platform using provisioners                        |
| `service` †  | `list` `status` `deploy` `destroy`                                                     | Deploy and manage individual services                     |
| `manifest` † | `list` `show` `export`                                                                 | Query and export deployment manifests                     |
| `mcp`        | `serve`                                                                                | Model Context Protocol server for AI integration          |
| `values` †   | `list` `get` `set` `resolve`                                                           | Inspect and manage resolved deployment values             |
| `vars` †     | `set` `unset` `list`                                                                   | Manage team-shared template variables                     |
| `tools`      | `status` `check` `install`                                                             | Manage and inspect external tool integrations             |
| `new` †      | —                                                                                      | Create a platform config file from a template             |
| `version`    | —                                                                                      | Show CLI version                                          |
| `completion` | `bash` `zsh` `fish` `powershell`                                                       | Output shell completion script for the given shell        |
| `help`       | —                                                                                      | Show help topics                                          |

> **†** Requires an initialized workspace (`.strata/` directory). Run `strata sln init --name NAME` first.

---

## `sln`

Solution workspace lifecycle commands.

### `sln init`

Initialize a new strata solution workspace. Creates the `.strata/` state directory, workspace defaults, and a ready-to-use `.devcontainer/` for VS Code Dev Containers and GitHub Codespaces.

```
strata sln init --name NAME [--template NAME-OR-PATH] [--list] [standard options]
```

| Option                    | Required | Description                                                                                                                               |
| ------------------------- | -------- | ----------------------------------------------------------------------------------------------------------------------------------------- |
| `--name NAME`             | ✅        | Name of the solution workspace                                                                                                            |
| `--template NAME-OR-PATH` | —        | Built-in template name (e.g. `aks`, `compose`) or path to a local template folder containing `scaffold/` and an optional `template.yaml`. |
| `--list`                  | —        | List available scaffold templates and exit. Does not require `--name`.                                                                    |
| `--guided`                | —        | Ask a short series of questions (stack type, cloud provider) to select and apply the right template. Requires an interactive terminal.    |

**Discovering templates:**

```bash
strata sln init --list
```

Shows built-in templates (shipped with strata) and workspace-local templates (`.strata/templates/` directories with a `scaffold/` subdirectory). Each template's description is pulled from its `template.yaml` manifest.

**Files created:**

| Path                              | Description                                                                |
| --------------------------------- | -------------------------------------------------------------------------- |
| `.strata/solution.json`           | Solution registry                                                          |
| `.strata/cli.yaml`                | Workspace CLI defaults                                                     |
| `.strata/logging.yaml`            | Logging configuration                                                      |
| `.devcontainer/devcontainer.json` | Dev container definition (Python 3.13, Terraform, Azure CLI, kubectl/Helm) |
| `.devcontainer/post-create.sh`    | Post-create script — installs `strata` and sets up shell completion        |

All `.devcontainer/` files are written **idempotently** — existing files are never overwritten.

**Exit codes:** 0 success · 1 failure · 2 missing `--name`

```bash
strata sln init --name my-platform
strata sln init --name my-platform --template aks
strata sln init --name my-platform --template .strata/templates/my-corp-base/
strata sln init --guided
```

> **Dev container:** After `strata sln init`, open the workspace in VS Code and select **Reopen in Container** to start a pre-configured environment with all tools installed. The container also works with GitHub Codespaces.

### `sln update`

Refresh package-owned files in an existing workspace after upgrading the strata package. Overwrites files that ship with the package (schemas, example templates, CI workflows, devcontainer config) while leaving user-customised files untouched.

```
strata sln update [standard options]
```

Requires an initialized workspace (`solution.json` must exist).

**Files updated (package-owned — always overwritten):**

| Path / Pattern                   | Description                                     |
| -------------------------------- | ----------------------------------------------- |
| `.strata/schemas/*.json`         | JSON Schemas derived from Pydantic models       |
| `.strata/README.md`              | Package documentation stub                      |
| `.strata/.gitignore`             | Package-standard ignore rules                   |
| `.strata/integrations/`          | Example custom integration starter              |
| `.strata/templates/`             | Example YAML document templates                 |
| `.devcontainer/`                 | Dev container definition and post-create script |
| `.github/workflows/`             | CI/CD workflow templates                        |
| `.github/strata.instructions.md` | Copilot instructions                            |
| `.gitignore` (root)              | Package-standard root ignore rules              |

**Files preserved (user-owned — never overwritten):**

| Path / Pattern          | Description                                      |
| ----------------------- | ------------------------------------------------ |
| `<name>.code-workspace` | User-customised VS Code workspace file           |
| `.vscode/`              | User VS Code settings, tasks, and launch configs |
| `.strata/cli.yaml`      | User CLI preferences                             |
| `.strata/logging.yaml`  | User logging configuration                       |
| `README.md` (root)      | User solution documentation                      |

**Exit codes:** 0 success · 1 failure (workspace not initialised, or file write error)

```bash
strata sln update
strata sln update --work-path /path/to/workspace
```

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

Export the current workspace as a reusable scaffold template. Copies all workspace files into `.strata/templates/<name>/scaffold/`, replaces every occurrence of the solution name with `{{ solution_name }}` in file content and file paths, then generates a `template.yaml` manifest.

```
strata sln export --name NAME [--force] [--dry-run] [standard options]
```

| Option        | Required | Description                                                             |
| ------------- | -------- | ----------------------------------------------------------------------- |
| `--name NAME` | ✅        | Template name — used as the output directory under `.strata/templates/` |
| `--force`     | —        | Overwrite an existing template with the same name                       |
| `--dry-run`   | —        | List files that would be copied without writing anything                |

**Output structure:**

| Path                                     | Description                                              |
| ---------------------------------------- | -------------------------------------------------------- |
| `.strata/templates/<name>/scaffold/`     | Workspace files with `{{ solution_name }}` substitutions |
| `.strata/templates/<name>/template.yaml` | Template manifest (name, description, variables)         |

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

Manage team-shared template variables stored in `solution.json`. Variables are substituted as `{{ key }}` in platform YAML files.

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

### `tools status` options

| Option              | Description                                                                                   |
| ------------------- | --------------------------------------------------------------------------------------------- |
| `--deployment FILE` | Load a deployment file to add a **Requirement** column (`required` / `optional` / `—`)        |
| `--required`        | Show only integrations marked `required` by the deployment (requires `--deployment`)          |
| `--optional`        | Show only integrations marked `optional` by the deployment (requires `--deployment`)          |
| `--available`       | Show only integrations that are installed and available                                       |
| `--missing`         | Show only integrations that are not available. Exits **3** if any `required` ones are missing |

```bash
strata tools status
strata tools status --output json
strata tools status --deployment deployment.yaml
strata tools status --deployment deployment.yaml --required --missing
strata tools check terraform
strata tools install terraform
strata tools install terraform --env-file .env.template
```

---

## `new`

Create a new platform configuration file (or set of files) from a built-in or
workspace-local template.

```
strata new TEMPLATE NAME [--output-file FILE] [--overwrite] [--set KEY=VALUE ...] [standard options]
strata new --list
```

| Option / Argument    | Description                                                                                                   |
| -------------------- | ------------------------------------------------------------------------------------------------------------- |
| `TEMPLATE`           | Template name (e.g. `namespace`, `provider`, `tenant`)                                                        |
| `NAME`               | Injected as `{{ name }}`; used in output filenames and path segments                                          |
| `--output-file FILE` | Output file path or directory (default: current directory)                                                    |
| `--overwrite`        | Overwrite output file(s) if they already exist                                                                |
| `--set KEY=VALUE`    | Inject an extra variable into the template (repeatable)                                                       |
| `--list`             | List available templates and bundles, then exit                                                               |
| `--scaffold-deps`    | After creation, detect any missing files referenced by the new file and offer to scaffold them automatically. |

**Template discovery** — `strata new --list` shows all templates grouped by type:

- **Single-file templates** — create one YAML file (e.g. `namespace`, `provider`, `tenant`)
- **Scaffold bundles** — multi-file workspace starters from `templates/examples/` and `.strata/templates/` (e.g. `aks`, `compose`). Use via `strata sln init --template <name>`.

Workspace-local templates are marked with `*` in the output.

```bash
strata new namespace my-app
strata new provider azure --output-file config/
strata new workspace my-ws --set owner=myteam
strata new dns my-zones --output-file config/dns/
strata new tenant newcorp --output-file repos/xyz-config/ --set zone=eu --set tier=premium
strata new --list
```

### Template resolution order

For any `TEMPLATE` name, strata searches in this order — first match wins:

1. `.strata/templates/<name>/` — workspace bundle directory
2. `.strata/templates/<name>.yaml` — workspace single file
3. Package bundle directory
4. Package single-file template

Workspace templates always override the package defaults.

### Bundle templates

A **bundle** is a directory under `.strata/templates/` that contains multiple
template files. The directory tree is the output structure — `{{ var }}`
substitution runs on both file content and path segments using the same
`{{ var }}` syntax as single-file templates.

**Built-in tenant bundle example:**

```
.strata/templates/tenant/
├── template.yaml              ← metadata + description
├── README.md
└── {{ name }}/                ← rendered to tenant name
    ├── {{ name }}.yaml        ← tenant config
    ├── README.md
    ├── CHECKLIST.md
    └── environments/
        ├── dev.yaml           ← environment overrides
        ├── qa.yaml
        └── prd.yaml
```

Running `strata new tenant contoso --output-file tenants/` produces:

```
tenants/contoso/
├── contoso.yaml               (tenant config with {{ name }} → contoso)
├── README.md
├── CHECKLIST.md
└── environments/
    ├── dev.yaml               (dev environment template)
    ├── qa.yaml                (qa environment template)
    └── prd.yaml               (prd environment template)
```

All `{{ var }}` references in content and path segments are substituted from:
- `name` — the `NAME` argument (always available)
- `--set KEY=VALUE` overrides
- Team context from `solution.json` (if present)

**Workspace-local bundles** — Teams can create custom bundles in `.strata/templates/<name>/`
to standardize tenant provisioning, CI/CD pipelines, or deployment structures. Teams maintain
these templates locally; they are not shipped with strata.

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

## `log`

View execution logs (read-only).

> **Note:** To configure logging behaviour (levels, output format), use `strata config log`. See [`config log`](#config-log) for details.

### `log list`

```
strata log list [--lines N] [--minutes N] [--level LEVEL] [--execution-id ID] [--last]
```

| Option              | Default | Description                                                              |
| ------------------- | ------- | ------------------------------------------------------------------------ |
| `--lines N`         | 50      | Maximum number of log entries to show                                    |
| `--minutes N`       | —       | Show only entries from the last N minutes                                |
| `--level LEVEL`     | —       | Filter by minimum log level: `DEBUG` `INFO` `WARNING` `ERROR` `CRITICAL` |
| `--execution-id ID` | —       | Filter to a specific execution ID                                        |
| `--last`            | off     | Show logs for the most recent command execution                          |

```bash
strata log list
strata log list --last
strata log list --level ERROR --lines 20
```

---

## `profile`

Manage environment profiles within the solution.

### `profile add NAME`

Create a new environment profile.

| Option       | Description                                         |
| ------------ | --------------------------------------------------- |
| `--activate` | Activate this profile immediately after creating it |

```bash
strata profile add staging
strata profile add prd --activate
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

| Type     | What it registers                                                                                                                                        |
| -------- | -------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `env`    | Environment variable files (`.env` format) — merged into the build environment                                                                           |
| `config` | Platform YAML config files — merged by kind into the build artifact                                                                                      |
| `data`   | Arbitrary data files (JSON, TOML, text) — copied verbatim into the build output without merging. Use for non-YAML assets your Terraform or scripts need. |
| `secret` | Secret files (YAML or env format) — loaded at deploy time, values masked in logs                                                                         |

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

> **`--type local`:** You can also pass `--type local` explicitly to force local registration when the auto-detection heuristic doesn't match (e.g. a relative path or an unusual UNC format). This is the same as what the workspace template's `GETTING_STARTED.md` uses for repos that already live on disk.

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

Show git working-tree state and tag status for registered repositories.

```
strata repo status [--name NAME] [standard options]
```

**Output includes:**

- Repository path, current branch, tracking remote
- Ahead/behind counts vs. tracking remote
- File status: count of staged, unstaged, untracked, conflicted files
- Latest release tag and quality-gate tag with age — **only shown when this repo's
  actual git remote URL matches a `configuration.spec.remotes[]` entry that declares
  `conventions`** (see the [`ref_convention` policy](policies.md#ref_convention)).
  There is no name-based guessing and no built-in tag-name heuristic — an unlinked
  repo simply shows no tags section.

**Console output example:**

```
Repository Status
─────────────────

  ✅ my-service  [main → origin/main]
       Tags:
         Release: v1.2.0 (14 days ago, abc1234)
         Quality: tested (2 days ago, def5678)

  ✅ tf-landscape  [main → origin/main]
       Tags:
         Release: v2.4.0 (7 days ago, ghi9abc)
         Quality: tested-20260701 (1 day ago, jkl2def)

  ⚠️  config-repo  [dev → origin/main ↑0 ↓2]
       2 unstaged, 1 untracked
```

**JSON output** (with `--output json`) includes full tag metadata:

```json
{
  "repos": [{
    "name": "my-service",
    "tags": {
      "latest_release": {
        "name": "v1.2.0",
        "commit": "abc1234567890abcdef",
        "short_commit": "abc1234",
        "created": "2026-06-20T10:30:00+00:00",
        "age_days": 14,
        "age_str": "14 days ago"
      },
      "latest_quality": {
        "name": "tested",
        "commit": "def567890123",
        "short_commit": "def5678",
        "created": "2026-07-01T10:15:00+00:00",
        "age_days": 2,
        "age_str": "2 days ago"
      }
    }
  }]
}
```

**Options:**

| Option        | Description                         |
| ------------- | ----------------------------------- |
| `--name NAME` | Show status for a single repository |
| `--verbose`   | List individual changed files       |

**Discovering release candidates:**

```bash
strata repo status --output json | jq '.repos[] | select(.tags.latest_quality) | .tags.latest_quality'
```

**Usage:**

```bash
strata repo status
strata repo status --name my-service
strata repo status --output json
strata repo status --verbose
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

Runs the full build pipeline (platform → terraform → ansible → compose → helm → SBOM). `--dry-run` validates and plans without writing output files.

```bash
strata build run -f xyz-deploy-prd.yaml
strata build run --dry-run
```

### `build plan`

```
strata build plan [-f FILE] [--stage NAME] [--artifacts-only] [standard options]
```

Builds into a temp directory, diffs against existing artifacts, then runs `terraform init → validate → plan` per stage. Nothing is written to the real build path.

Also shows a **value status table** derived from YAML alone (no store access required) — useful for verifying that all required values have a source before deploying.

| Option             | Description                                   |
| ------------------ | --------------------------------------------- |
| `--stage NAME`     | Limit terraform plan to one stage             |
| `--artifacts-only` | Skip terraform plan — show artifact diff only |

Value status column values:

| Status      | Meaning                                                                    |
| ----------- | -------------------------------------------------------------------------- |
| `ok`        | Built-in store (`constant`, `environment`, `github`) — always available    |
| `seeded`    | Integration-backed with `default:` — seeded on first run                   |
| `generated` | Secret with `generate:` spec — auto-generated on first run                 |
| `required`  | Integration-backed with no `default:` or `generate:` — must exist in store |

```bash
strata build plan -f xyz-deploy-prd.yaml
strata build plan --stage production --artifacts-only
```

### `build sbom`

```
strata build sbom [-f FILE | --scan PATH] [--report MODE] [--output-file PATH] [--no-deps] [--audit] [--severity LEVEL] [--fail-on LEVEL] [standard options]
```

Two modes of operation:

- **Deployment mode** (`-f FILE`): Regenerates the SBOM from an existing `platform.json`
  without a full rebuild. Requires a prior `strata build run`.
- **Scan mode** (`--scan PATH`): Scans any directory for SBOM components without a
  deployment file or workspace init. Useful for generating an SBOM from existing
  Terraform, Helm, Compose, or application repos.

Collectors run in both modes:

| Collector                                | Deployment mode | Scan mode       |
| ---------------------------------------- | --------------- | --------------- |
| Container images (platform model)        | ✅               | — (empty model) |
| Compose images (`docker-compose.yml`)    | ✅               | ✅               |
| Helm charts (platform model)             | ✅               | — (empty model) |
| Helm charts (`Chart.yaml` files)         | ✅               | ✅               |
| Terraform providers (`*.tf`)             | ✅               | ✅               |
| Terraform modules (`*.tf`)               | ✅               | ✅               |
| Ansible collections (`requirements.yml`) | ✅               | ✅               |
| Application dependencies (lockfiles)     | ✅               | ✅               |

`--scan` and `-f` are mutually exclusive.

`--report` selects the output style:

| Flag                 | Behaviour                                                                                                                                       |
| -------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------- |
| `--report cyclonedx` | Write `sbom.json` (CycloneDX 1.6 JSON) — **default**                                                                                            |
| `--report inventory` | Print a human-readable grouped listing to stdout                                                                                                |
| `--output-file PATH` | Write to *PATH* instead of the default location / stdout                                                                                        |
| `--no-deps`          | Skip `DependencyFileCollector` (faster for large repos)                                                                                         |
| `--audit`            | Run CVE vulnerability scan after generating the SBOM (requires [trivy](https://trivy.dev) or [grype](https://github.com/anchore/grype) in PATH) |
| `--severity LEVEL`   | Minimum severity to report: `CRITICAL` \| `HIGH` \| `MEDIUM` \| `LOW` \| `UNKNOWN`. Default: `MEDIUM`                                           |
| `--fail-on LEVEL`    | Exit non-zero (code 3) if findings at this severity or above exist. Optional — without it, audit is advisory only                               |

```bash
# Regenerate sbom.json from deployment
strata build sbom -f xyz-deploy-prd.yaml

# Print a human-readable platform inventory
strata build sbom -f xyz-deploy-prd.yaml --report inventory

# Save the inventory to a file
strata build sbom -f xyz-deploy-prd.yaml --report inventory --output-file inventory.txt

# Skip lockfile scanning (faster)
strata build sbom -f xyz-deploy-prd.yaml --no-deps

# Scan a directory (no strata workspace required)
strata build sbom --scan ./my-terraform-repo

# Scan + human-readable overview
strata build sbom --scan ./infra --report inventory

# Scan + write SBOM to a specific file
strata build sbom --scan . --output-file sbom.json

# Generate SBOM + run CVE audit (advisory — prints findings, never fails)
strata build sbom --scan . --audit

# Audit with CI gate — fail if any HIGH or CRITICAL findings
strata build sbom -f xyz-deploy-prd.yaml --audit --fail-on HIGH

# Audit only CRITICAL vulnerabilities
strata build sbom --scan . --audit --severity CRITICAL --fail-on CRITICAL
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

`validate` is a command group with two subcommands. Bare invocation (`strata validate -f file.yaml`) delegates to `run` for backward compatibility.

```
strata validate run [OPTIONS]     ← validate a YAML file
strata validate graph [OPTIONS]   ← build a dependency graph
strata validate -f FILE           ← shorthand; equivalent to `validate run -f FILE`
```

### `validate run`

Validate a single platform YAML file against its kind-specific schema.

```
strata validate run -f FILE_PATH [--deep] [--explain] [standard options]
```

| Option             | Description                                                                                                                                               |
| ------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `-f / --file PATH` | File to validate. Required (unless `--path` glob is used).                                                                                                |
| `--path GLOB`      | Validate multiple files matching a glob. Requires an initialized workspace with an active profile.                                                        |
| `--deep`           | Enable Phase 2 (cross-reference) validation against the active profile's configuration sources. Requires an initialized workspace with an active profile. |
| `--explain`        | After validation, emit a plain-English summary of what the file describes.                                                                                |

**Exit codes:** 0 valid · 1 system failure · 2 missing argument · 3 schema-invalid

```bash
strata validate run -f config/xyz-config.yaml
strata validate run -f config/xyz-ws-platform.yaml --deep
strata validate run --path "deployments/**"
strata validate -f config/xyz-config.yaml        # backward compat
```

### `validate graph`

Build a Mermaid dependency diagram of the workspace. Two modes:

- **`--mode files`** (default) — nodes are YAML files, edges are cross-file references. Shows which files wire together and highlights missing or invalid files.
- **`--mode resources`** — nodes are logical resources/modules/namespaces, edges are `depends_on` chains, module attachments, and subnet assignments. Shows the infrastructure topology organized by provisioner.

```
strata validate graph [--mode files|resources] [--entry PATH] [--save PATH] [--direction LR|TD|BT|RL] [--no-validate] [standard options]
```

| Option              | Default                    | Description                                                                            |
| ------------------- | -------------------------- | -------------------------------------------------------------------------------------- |
| `--mode`            | `files`                    | Graph type: `files` or `resources`.                                                    |
| `--entry / -e PATH` | auto-detect                | Entry point file (deployment or workspace YAML). Discovers all deployments if omitted. |
| `--save / -s PATH`  | —                          | Write Mermaid markdown to file. Defaults to `graph.md` when flag used without a value. |
| `--direction`       | LR (files), TD (resources) | Mermaid graph direction: `LR`, `TD`, `BT`, `RL`.                                       |
| `--no-validate`     | off                        | Skip validation — all nodes shown as neutral. Faster for large workspaces.             |

**Node status colours (file mode):**

| Status   | Colour | Condition                                                 |
| -------- | ------ | --------------------------------------------------------- |
| Valid    | green  | File exists and passes Phase 1 validation                 |
| Invalid  | orange | File exists but fails validation                          |
| Missing  | red    | Referenced by another file but not present on disk        |
| External | grey   | `@repo/path` reference to a file in another repository    |
| Orphan   | dashed | File exists and validates but no other file references it |

**Exit codes:** 0 success (including missing nodes — graph always produces output) · 1 system failure

```bash
strata validate graph
strata validate graph --entry deploy/deploy-prd.yaml
strata validate graph --mode resources --entry stack/ws-platform.yaml
strata validate graph --save graph.md
strata validate graph --output json
```

---

## `guide`

Show workspace setup progress and suggest the next action. Use `strata guide` to understand where you are in the setup sequence — whether you are onboarding to a new workspace for the first time or returning after making changes.

```
strata guide [OPTIONS]
```

| Option             | Type                                | Default       | Description                                                                |
| ------------------ | ----------------------------------- | ------------- | -------------------------------------------------------------------------- |
| `--file / -f PATH` | path                                | —             | Inspect a specific YAML file (file mode). Supports `STRATA_FILE` env var.  |
| `--work-path PATH` | path                                | auto-detected | Root workspace directory. Falls back to `STRATA_WORK_PATH`, then CWD walk. |
| `--output FORMAT`  | `console`\|`text`\|`json`\|`ndjson` | `console`     | Output format.                                                             |
| `--verbose`        | flag                                | off           | Emit structured log lines to console.                                      |
| `--quiet`          | flag                                | off           | Suppress all output.                                                       |

**Exit code:** always `0` — `guide` is advisory and never a pipeline gate.

Works outside an initialized workspace: degrades gracefully when `.strata/solution.json` is absent (shows Phase 1 as ⬜ and stops).

### Workspace mode

Run `strata guide` without `--file` to see the 7-phase workspace setup checklist. Each phase is marked ✅ done, ⚠️ needs attention, or ⬜ not started. The `→ Next step` block identifies the first phase that is not ✅ and shows the exact command to address it.

| #   | Phase                      | ✅ Done                                    | ⚠️ Attention                                | ⬜ Not started    |
| --- | -------------------------- | ----------------------------------------- | ------------------------------------------ | ---------------- |
| 1   | Workspace initialized      | `.strata/solution.json` exists and parses | File exists but cannot be parsed           | File absent      |
| 2   | Repositories registered    | At least one repo in `spec.repositories`  | —                                          | None registered  |
| 3   | Repositories on disk       | All registered repos exist locally        | Some paths missing (shows count and names) | Phase 2 is ⬜     |
| 4   | Profile created            | At least one profile in `spec.profiles`   | —                                          | None created     |
| 5   | Profile activated          | One profile has `active: true`            | —                                          | None active      |
| 6   | File references registered | Active profile has at least one ref       | Zero refs on active profile                | Phase 5 is ⬜     |
| 7   | Build artifact exists      | `build/` directory exists and has files   | Directory exists but is empty              | Directory absent |

```
Workspace: my-platform  (e:\src\my-config-repo)

Setup progress:

  ✅ Workspace initialized
  ✅ Repositories registered (3)
  ⚠️  Repositories on disk (2/3 cloned — xyz-svc-traefik not found)
  ✅ Profile created (prd, stg, dev)
  ✅ Profile activated (prd)
  ⚠️  File references (0 registered on active profile 'prd')
  ⬜ Build artifact

→ Next step: Register config files with your active profile:

   strata ref config add <name> @<repo>/path/to/config.yaml --profile prd

   See: strata help --topic environments
```

Next-step hints by phase:

| First incomplete phase        | Suggested command                                                             |
| ----------------------------- | ----------------------------------------------------------------------------- |
| 1 — Workspace not initialized | `strata sln init <name>`                                                      |
| 2 — No repos registered       | `strata repo add <name> <url>`                                                |
| 3 — Repos not all on disk     | `git clone <url> <path>` (one line per missing repo)                          |
| 4 — No profiles               | `strata profile add <name> --activate`                                        |
| 5 — No active profile         | `strata profile activate <name>`                                              |
| 6 — No refs registered        | `strata ref config add <name> @<repo>/path/to/config.yaml --profile <active>` |
| 7 — No build artifact         | `strata build run`                                                            |
| All ✅                         | `All setup phases complete. Your workspace is ready to deploy.`               |

### File mode (`--file`)

Supply `--file` (or `-f`) to inspect a specific YAML file. The workspace setup checklist is suppressed and replaced by a 5-phase structural check, followed by ready-to-use `validate` and `register` commands for the detected kind.

| #   | Phase              | Check                                           |
| --- | ------------------ | ----------------------------------------------- |
| 1   | File readable      | Path exists and YAML parses without error       |
| 2   | Kind recognized    | `kind:` is present and is a known platform kind |
| 3   | apiVersion present | `apiVersion: strata.huybrechts.xyz/v1`          |
| 4   | Name present       | `meta.name` is non-empty                        |
| 5   | Spec present       | `spec:` block exists and is non-empty           |

```
File: path/to/my-config.yaml  (kind: configuration)
Workspace: my-platform  (e:\src\my-config-repo)

File structure:

  ✅ File readable
  ✅ Kind: configuration
  ✅ apiVersion: strata.huybrechts.xyz/v1
  ✅ Name: my-config
  ✅ Spec present

→ Validate:

   strata validate -f path/to/my-config.yaml

→ Register with your active profile:

   strata ref config add my-config @<repo>/path/to/my-config.yaml --profile prd

   See: strata help --topic environments
```

`@repo/path` references are resolved via `solution.spec.repositories` and require an initialized workspace.

### Project customisation

Create `.strata/guide.yaml` to override the default next-step hints and `see_also` links for any phase:

```yaml
hints:
  phase_6:
    hint: "strata ref config add infra-config @infra/config/infra.yaml --profile prd"
    see_also: "strata help --topic environments"
```

```bash
strata guide
strata guide --output json
strata guide --file config/my-config.yaml
strata guide -f @myrepo/config/app.yaml
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

**Valid kinds:** `configuration` `deployment` `dns` `environment` `firewall` `module` `namespace` `network` `platform_model` `provider` `resource` `workspace`

**Exit codes:** 0 success · 2 unknown kind

```bash
strata schema get deployment
strata schema get deployment --output json
strata schema get environment --output text
```

---

## `policy`

Inspect policies declared in the active configuration. Policies define guardrails evaluated at the validate, build, and plan lifecycle phases. See [policies.md](policies.md) for the policy engine reference.

### `policy list`

List all policies declared in `configuration.spec.policies`.

```
strata policy list [-f PATH] [standard options]
```

Loads the active configuration from the workspace's active profile. Displays a table of every policy with its type, phase, enforcement level, and enabled state. `deny` enforcement is highlighted red; `warn` is highlighted yellow.

| Option          | Description                                                                                                                                                     |
| --------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `-f` / `--file` | Optional path to a deployment YAML. When given, annotates the output with which lifecycle phases that deployment can trigger (inferred from provisioner names). |

**Output keys (`--output json`):** `source`, `deployment`, `policy_count`, `enabled_count`, `phases_triggered`, `policies[]` (each with `name`, `type`, `phase`, `enforcement`, `enabled`, `description`, `configuration`).

**Exit codes:** 0 success · 1 workspace not initialised or configuration load failure

```bash
strata policy list
strata policy list -f config/deploy-prd.yaml
strata policy list --output json
strata policy list -f config/deploy.yaml --output json
```

### `policy check`

```
strata policy check -f FILE [--phase PHASE]... [--plan-file PATH] [standard options]
```

Evaluate all enabled policies for a deployment and report results. Policies are grouped by phase (validate, build, plan, deploy).

| Option                                  | Description                                                                                  |
| --------------------------------------- | -------------------------------------------------------------------------------------------- |
| `-f` / `--file`                         | ✅ Required. Path to the deployment YAML file.                                                |
| `--phase validate\|build\|plan\|deploy` | Filter to one or more phases. Repeatable. Defaults to all phases.                            |
| `--plan-file PATH`                      | Path to a Terraform plan JSON file used for plan-phase policies. Auto-discovered if omitted. |

When context artifacts are not available (no platform build, no plan file), the command includes **notes** with the exact command to run to generate the missing artifact. These notes appear in console output and in the `notes[]` field of JSON output — they do not fail the command.

**Exit codes:** 0 all policies passed · 1 execution error · 3 one or more `deny` policies failed

**Output keys (`--output json`):** `deployment`, `phases`, `policies_checked`, `passed`, `failed`, `denied`, `notes[]`, `results[]` (each with `phase`, `policy`, `enforcement`, `result`, `violations[]`).

```bash
strata policy check -f xyz-deploy-prd.yaml
strata policy check -f xyz-deploy-prd.yaml --phase validate --phase plan
strata policy check -f xyz-deploy-prd.yaml --plan-file build/infra.tfplan.json
strata policy check -f xyz-deploy-prd.yaml --output json
```

---

## `secret`

Generate and manage secret values. These utilities are useful for creating passwords, tokens, and other sensitive values to be stored in vaults or used in configuration files.

### `secret generate`

Generate a cryptographically secure secret value in various formats.

```
strata secret generate [--format FORMAT] [--length N] [standard options]
```

| Option            | Default   | Description                                                                                                                                                   |
| ----------------- | --------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `--format FORMAT` | `urlsafe` | Output encoding. Valid formats: `urlsafe`, `hex`, `alphanumeric`, `password`, `numeric`, `base64`, `uuid4`, `uuid7`                                           |
| `--length N`      | 32        | For most formats: number of bytes (output will be longer after encoding). For `alphanumeric`/`password`/`numeric`: exact character count. For UUIDs: ignored. |

**Format descriptions:**

| Format         | Use Case                                                 | Output Example                         |
| -------------- | -------------------------------------------------------- | -------------------------------------- |
| `urlsafe`      | Passwords, tokens, API keys (safe for URLs and env vars) | `qB2-mKp3XzZ_VqL9N8RkT4Wx1YuJaFbC`     |
| `hex`          | Binary data, checksums, hashing                          | `a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6`     |
| `alphanumeric` | Restrictive password policies (letters + digits only)    | `A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6`     |
| `password`     | General-purpose passwords (mix of letter+digit+symbols)  | `T7@kL2#qP5!mX9&rW3\`vY8$bZ1%cN4`      |
| `numeric`      | PINs, OTPs, numeric-only contexts                        | `482916375029847362`                   |
| `base64`       | Kubernetes secrets, Docker auth, config formats          | `YWJjZGVmZ2hpamtsbW5vcHFyc3R1dnd4eXo=` |
| `uuid4`        | Distributed IDs, uncorrelated identifiers                | `f47ac10b-58cc-4372-a567-0e02b2c3d479` |
| `uuid7`        | Time-ordered IDs, sortable identifiers, Postgres v13+    | `01891a9c-16d7-7e52-be7c-6cde194a6e5a` |

**Typical use cases:**

- Store in a vault: `strata secret generate --format password --length 20 | vault write secret/app_password -`
- Generate API token: `strata secret generate --format urlsafe --length 48`
- Create database password: `strata secret generate --format password --length 32`

**Exit codes:** 0 success · 1 invalid format or options

```bash
# Generate a 20-character password with symbols
strata secret generate --format password --length 20

# Generate a 64-byte URL-safe token (outputs base64-URL, ~86 chars)
strata secret generate --format urlsafe --length 64

# Generate a UUID v7 (time-ordered)
strata secret generate --format uuid7

# Get structured JSON output
strata secret generate --format password --length 32 --output json
```

### `secret mask`

Mask a secret value for safe display in logs or output. The first N characters remain visible; the rest are replaced.

```
strata secret mask VALUE [--show N] [--char C] [standard options]
```

| Option   | Default | Description                                  |
| -------- | ------- | -------------------------------------------- |
| `--show` | 4       | Number of leading characters to keep visible |
| `--char` | `*`     | Replacement character for the masked portion |

**Typical use cases:**

- Log tokens safely: `echo "Token: $(strata secret mask $TOKEN --show 4 --char '*')"`
- Display credentials in output: `strata secret mask "dbpassword123" --show 2 --char '#'`
- Audit trail: Show enough to identify, but not leak secrets

**Exit codes:** 0 success · 1 invalid arguments

```bash
# Show first 4 chars of a token, mask the rest with asterisks
strata secret mask "akiaiosfodnn7example" --show 4

# Show first 2 chars with custom mask character
strata secret mask "s3cr3t_p@ssw0rd" --show 2 --char '#'

# Get structured JSON output
strata secret mask "mysecretvalue" --show 3 --output json
```

**Output examples:**

```
# Token with 4 visible chars (default)
$ strata secret mask "token_abc123xyz789"
token****

# Password with 2 visible chars and custom replacement
$ strata secret mask "myP@ssw0rd" --show 2 --char '#'
my########
```

---

## `deploy`

> **Note:** Requires the CLI tool for the provisioner used by each stage (`terraform`, `helm`, `ansible-playbook`). Use `strata tools status` to verify availability. Configure integration credentials (Bitwarden, Vault, Azure Key Vault, etc.) before running. Use `--dry-run` to validate and plan without applying any changes.

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

After resolving values, the command prints a summary of any values that were seeded or generated for the first time:

```
  ✓  Resolved 3 variable(s), 2 secret(s), 1 feature(s).
  ↳  Seeded on first run: LOG_LEVEL=info, DARK_MODE=false
  ↳  Generated on first run: DB_PASSWORD
```

These lines only appear when values were actually seeded or auto-generated (i.e., on the first deploy against a new store). Subsequent runs are silent.

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

### `deploy show`

```
strata deploy show [-f FILE] [standard options]
```

Show resolved deployment configuration: effective remote versions after applying
environment overrides, plus the workspace and environment files in use.

For each remote, displays the effective reference and whether it came from an
environment override or the workspace default.

```bash
strata deploy show -f xyz-deploy-prd.yaml
strata deploy show -f xyz-deploy-prd.yaml --output json
```

Example output:

```
📋  Deployment:   acme-prd
    File:         deployments/acme-prd.yaml
    Workspace:    workspaces/customer-acme.yaml
    Environment:  env-prd (environments/env-prd.yaml)

    Remote Versions:

    Remote        Effective Ref  Source
    ───────────────────────────────────────────────
    tf_landscape  v2.2.0         env-prd (override)
    ans_deploy    v1.1.0         workspace default
```

### `deploy list`

```
strata deploy list [-p DIR] [standard options]
```

Enumerate deployment manifests with extracted metadata. Scans a directory
recursively for `kind: deployment` YAML files and returns one entry per
manifest. Designed for CI matrix generation — pipe `--output json` into
`jq` or consume directly as a GitHub Actions matrix input.

All `spec.layers` dimensions are promoted to top-level fields so any layer
key (e.g. `environment`, `zone`, `tier`) is directly usable as a matrix
variable without further parsing.

| Option   | Description                                               |
| -------- | --------------------------------------------------------- |
| `-p DIR` | Directory to scan (default: current directory, recursive) |

```bash
# Console table — all deployment manifests under ./deployments/
strata deploy list -p deployments/

# JSON for CI matrix
strata deploy list -p deployments/ --output json
```

Example JSON output (inside the standard `data` envelope):

```json
{
  "deployments": [
    {
      "file": "/repos/xyz-config/deployments/acme-prd.yaml",
      "name": "acme_deploy_prd",
      "environment": "prd",
      "zone": "eu",
      "customer": "acme",
      "workspace": "xyz_platform"
    },
    {
      "file": "/repos/xyz-config/deployments/globex-dev.yaml",
      "name": "globex_deploy_dev",
      "environment": "dev",
      "zone": "eu",
      "customer": "globex",
      "workspace": "xyz_platform"
    }
  ],
  "count": 2
}
```

GitHub Actions matrix usage:

```yaml
jobs:
  matrix:
    runs-on: ubuntu-latest
    outputs:
      matrix: ${{ steps.list.outputs.matrix }}
    steps:
      - id: list
        run: |
          matrix=$(strata deploy list -p deployments/ --output json | jq -c '.data.deployments')
          echo "matrix=$matrix" >> $GITHUB_OUTPUT

  deploy:
    needs: matrix
    strategy:
      matrix:
        deployment: ${{ fromJson(needs.matrix.outputs.matrix) }}
    steps:
      - run: strata deploy run --file "${{ matrix.deployment.file }}"
```

### `deploy plan`

```
strata deploy plan -f FILE [--stage NAME] [standard options]
```

Show the resource change summary from the last saved `.tfplan` file produced by `deploy run --dry-run`. Runs `terraform show -json` locally — no backend calls.

| Option         | Description                        |
| -------------- | ---------------------------------- |
| `--stage NAME` | Limit to a single deployment stage |

```bash
strata deploy plan -f xyz-deploy-prd.yaml
strata deploy plan -f xyz-deploy-prd.yaml --stage production
strata deploy plan -f xyz-deploy-prd.yaml --output json
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

### `deploy output`

```
strata deploy output -f FILE [--stage NAME] [--key NAME] [--refresh] [standard options]
```

Show Terraform outputs from the local cache (`.tf-outputs.json`) or fetch live from the backend with `--refresh`.

| Option         | Description                                               |
| -------------- | --------------------------------------------------------- |
| `--stage NAME` | Limit to a single deployment stage                        |
| `--key NAME`   | Print only a single output key (useful for scripting)     |
| `--refresh`    | Re-run `terraform output -json` and update the cache file |

```bash
strata deploy output -f xyz-deploy-prd.yaml
strata deploy output -f xyz-deploy-prd.yaml --stage production --key endpoint
strata deploy output -f xyz-deploy-prd.yaml --refresh
strata deploy output -f xyz-deploy-prd.yaml --version 2.0.0
strata deploy output -f xyz-deploy-prd.yaml --all-versions --output json
```

### `deploy lock`

Manage deployment state locks. Locking is enabled in the deployment YAML via `spec.locking.enabled: true`. When enabled, `deploy run` acquires a lock before the first stage and releases it in a `finally` block — protecting the full pipeline (hooks, Terraform, Ansible, health checks) from concurrent runs.

The lock backend is chosen automatically from the provisioner's `backend.type` — no separate connection configuration needed.

#### `deploy lock status`

```
strata deploy lock status -f FILE [standard options]
```

Show the current lock state for the deployment. Exits `0` whether locked or unlocked.

```bash
strata deploy lock status -f xyz-deploy-prd.yaml
strata deploy lock status -f xyz-deploy-prd.yaml --output json
```

**JSON output keys:** `locked` (bool), `deployment`, and when locked: `lock_id`, `holder`, `hostname`, `pid`, `acquired_at`, `expires_at`, `reason`.

#### `deploy lock release`

```
strata deploy lock release -f FILE [--force] [standard options]
```

Release the deployment lock. Without `--force`, release is denied if the lock is held by a different user or host (exit code `3`). With `--force`, the lock is released regardless of holder — use for stuck CI runs or crashed deploys.

| Option    | Description                                      |
| --------- | ------------------------------------------------ |
| `--force` | Release even if held by a different user or host |

```bash
strata deploy lock release -f xyz-deploy-prd.yaml
strata deploy lock release -f xyz-deploy-prd.yaml --force
```

**Exit codes:** `0` released (or was not locked); `1` backend error; `3` lock held by another holder and `--force` not supplied.

#### `deploy lock history`

```
strata deploy lock history -f FILE [--last N] [standard options]
```

Show recent lock events for the deployment (most recent first). History is stored locally in `{work_path}/.strata/locks/` as an append-only NDJSON file.

| Option     | Type | Default | Description                               |
| ---------- | ---- | ------- | ----------------------------------------- |
| `--last N` | int  | `10`    | Number of recent events to show (max 100) |

```bash
strata deploy lock history -f xyz-deploy-prd.yaml
strata deploy lock history -f xyz-deploy-prd.yaml --last 25
strata deploy lock history -f xyz-deploy-prd.yaml --output json
```

**JSON output keys:** `deployment`, `entries[]` — each entry: `lock_id`, `holder`, `hostname`, `pid`, `acquired_at`, `expires_at`, `reason`, `stage`.

---

## `env`

Inspect environment configuration and live infrastructure state.

### `env info`

```
strata env info [standard options]
```

Show instant workspace context — solution name, active profile, strata version, and work path. No subprocess calls.

```bash
strata env info
strata env info --output json
```

**JSON output keys:** `solution_id`, `active_profile`, `version`, `work_path`.

### `env output`

```
strata env output -f FILE [--name NAME] [--provisioner NAME] [--raw] [--json] [standard options]
```

Show live Terraform outputs per stage by running `terraform output -json` against the backend. Results are grouped by provisioner in a table.

| Option               | Description                                                                                   |
| -------------------- | --------------------------------------------------------------------------------------------- |
| `--name NAME`        | Show only a single output key across all stages                                               |
| `--provisioner NAME` | Limit to stages referencing a specific provisioner                                            |
| `--raw`              | Print the bare value only — requires `--name`. Suppresses all chrome; ideal for shell scripts |
| `--json`             | Emit the raw outputs dict as JSON — bypasses the strata envelope                              |

```bash
strata env output -f xyz-deploy-prd.yaml
strata env output -f xyz-deploy-prd.yaml --name endpoint
strata env output -f xyz-deploy-prd.yaml --provisioner platform_iac
IP=$(strata env output -f deploy.yaml --name hearth_ip --raw)
strata env output -f xyz-deploy-prd.yaml --json
```

**JSON output keys:** `file`, `stages{}` — each stage: `provisioner`, `outputs{}`, `ok`, `error`.

### `env show`

```
strata env show -f FILE [--stage NAME] [standard options]
```

Show the full resolved environment for a deployment: meta, properties, variables, secrets (masked), feature flags, overrides, and stages. Uses cached resolution — no subprocess calls.

| Option         | Description                                                              |
| -------------- | ------------------------------------------------------------------------ |
| `--stage NAME` | Filter secrets visibility to a specific stage's allowlist (default: all) |

```bash
strata env show -f xyz-deploy-prd.yaml
strata env show -f xyz-deploy-prd.yaml --stage production
strata env show -f xyz-deploy-prd.yaml --output json
```

**JSON output keys:** `file`, `deployment`, `meta{}`, `properties{}`, `variables[]`, `secrets[]`, `features[]`, `overrides{}`, `stages[]`.

### `env status`

```
strata env status [-f FILE | --path DIR | --all] [--stage NAME] [--offline] [standard options]
```

Show infrastructure status. Supports three modes:

| Mode             | Flags                   | Description                                                                               |
| ---------------- | ----------------------- | ----------------------------------------------------------------------------------------- |
| Single (live)    | `-f FILE`               | Per-stage detail via `terraform show -json` — resources, outputs, serial, cache freshness |
| Single (offline) | `-f FILE --offline`     | Per-stage detail from build cache only — no backend contact                               |
| Multi            | `--all` or `--path DIR` | One-line summary per deployment found — always offline (reads build cache)                |

| Option         | Description                                                                     |
| -------------- | ------------------------------------------------------------------------------- |
| `--stage NAME` | Query only a single stage. Single-deployment mode only.                         |
| `--offline`    | Use cached data only — no remote backend contact. Single-deployment mode only.  |
| `--path DIR`   | Scan a directory for deployment manifests and show a summary for each.          |
| `--all`        | Scan the entire workspace for deployment manifests and show a summary for each. |

```bash
strata env status -f xyz-deploy-prd.yaml
strata env status -f xyz-deploy-prd.yaml --offline
strata env status -f xyz-deploy-prd.yaml --stage production
strata env status --all
strata env status --path deploy/
strata env status --all --output json
```

**JSON output keys (single):** `file`, `deployment`, `mode`, `stages[]` — each stage: `name`, `provisioner`, `reachable`, `resources`, `outputs`, `serial`, `cache{}`.

**JSON output keys (multi):** `scan_path`, `deployments[]` — each: `file`, `name`, `stage_count`, `cached_count`, `stages[]`.

### `env drift`

```
strata env drift -f FILE [--stage NAME] [standard options]
```

Detect configuration drift by running `terraform plan -detailed-exitcode` against the live backend. Reports create/update/delete/replace counts per stage. Exit code 3 if drift is detected.

| Option         | Description                              |
| -------------- | ---------------------------------------- |
| `--stage NAME` | Check only a single stage (default: all) |

```bash
strata env drift -f xyz-deploy-prd.yaml
strata env drift -f xyz-deploy-prd.yaml --stage production
strata env drift -f xyz-deploy-prd.yaml --output json
```

**JSON output keys:** `file`, `deployment`, `stages[]` — each stage: `name`, `provisioner`, `drift_detected`, `to_add`, `to_change`, `to_destroy`, `to_replace`, `plan_output`.

### `env doctor`

```
strata env doctor [-f FILE] [--category CATEGORY] [--deep] [standard options]
```

Run a workspace health check across five categories. Exit code 3 if any check fails.

| Category    | What is checked                                                       |
| ----------- | --------------------------------------------------------------------- |
| `runtime`   | Python version, OS, available memory and disk                         |
| `workspace` | `.strata/` directory, `solution.json`, active profile                 |
| `tools`     | External tool availability (terraform, helm, docker, ansible, git, …) |
| `config`    | Deployment file validity, stage configuration, provisioner references |
| `auth`      | Backend authentication and secret store reachability                  |

| Option            | Description                                                               |
| ----------------- | ------------------------------------------------------------------------- |
| `-f FILE`         | Check only the tools referenced by this deployment file                   |
| `--category NAME` | Run only one category (`runtime`, `workspace`, `tools`, `config`, `auth`) |
| `--deep`          | Enable deeper checks (requires an active profile)                         |

```bash
strata env doctor
strata env doctor -f xyz-deploy-prd.yaml
strata env doctor --category tools
strata env doctor --deep --output json
```

**JSON output keys:** `categories[]` — each: `name`, `status`, `checks[]` — each check: `name`, `ok`, `message`.

---

## `audit`

> **Note:** Requires an initialized workspace. Audit records are written automatically by `strata deploy run` to `.strata/deploy-log/` — no extra configuration required to start collecting evidence. Configure `spec.deployment.audit` in the configuration YAML to enable remote push, structured path layout, and SIEM forwarding.

Query deployment audit logs and manage compliance evidence (ISO 27001 / ISAE 3402 layer 2 audit trail).

Every `strata deploy run` execution writes a structured JSON audit record to the local deploy-log directory. These records capture who ran the deployment, from which commit, which stages ran, whether they succeeded, step timings, and any PR metadata linked to the commit.

All `audit` subcommands accept standard options (`--work-path`, `--output`, `--verbose`, `--quiet`).

### `audit changes`

```
strata audit changes [--last N] [--since TIMESTAMP] [--stage NAME] [standard options]
```

List recent deployment executions from the local deploy-log. Results are sorted by timestamp descending (most recent first).

| Option              | Default | Description                                                                       |
| ------------------- | ------- | --------------------------------------------------------------------------------- |
| `--last N`          | `10`    | Maximum number of entries to show                                                 |
| `--since TIMESTAMP` | —       | Show only entries on or after an ISO 8601 timestamp (e.g. `2026-01-15T00:00:00Z`) |
| `--stage NAME`      | —       | Show only entries that include a stage with this exact name                       |

```bash
# Show the 10 most recent deployments
strata audit changes

# Show all deployments in the last 7 days
strata audit changes --since 2026-06-25T00:00:00Z

# Show deployments that ran the 'infrastructure' stage
strata audit changes --stage infrastructure

# Machine-readable output for CI / compliance tooling
strata audit changes --last 50 --output json
```

**Console output columns:** `timestamp`, `deployment`, `environment`, `success`, `duration`, `stages` (count), `commit` (short SHA).

**JSON output keys** (per entry): `execution_id`, `timestamp`, `version`, `deployment`, `workspace`, `environment`, `file`, `success`, `duration_seconds`, `commit_sha`, `stages[]`, `pull_request{}`.

### `audit resend`

```
strata audit resend [--last N] [--since TIMESTAMP] [standard options]
```

Re-forward local deploy-log entries to all configured audit sinks (webhook, ndjson, syslog, SIEM integrations). Useful after adding a new sink or recovering from a delivery failure.

| Option              | Default | Description                                     |
| ------------------- | ------- | ----------------------------------------------- |
| `--last N`          | —       | Resend only the last N entries                  |
| `--since TIMESTAMP` | —       | Resend only entries since an ISO 8601 timestamp |

```bash
# Resend all local records to currently configured sinks
strata audit resend

# Resend only the most recent 5 entries
strata audit resend --last 5

# Resend entries from the last 24 hours (machine-readable result)
strata audit resend --since 2026-07-01T00:00:00Z --output json
```

**JSON output keys:** `sent` (int), `failed` (int).

### `audit export`

```
strata audit export [--last N] [--since TIMESTAMP] [--format json|ndjson] [--out PATH] [standard options]
```

Export deploy-log entries to a file or stdout. Useful for offline compliance reviews and feeding records into external tooling.

| Option              | Default | Description                                              |
| ------------------- | ------- | -------------------------------------------------------- |
| `--last N`          | —       | Export only the last N entries                           |
| `--since TIMESTAMP` | —       | Export only entries since an ISO 8601 timestamp          |
| `--format`          | `json`  | Output format: `json` (array) or `ndjson` (one per line) |
| `--out PATH`        | stdout  | Write to a file instead of stdout                        |

```bash
# Export all records as JSON array to stdout
strata audit export

# Export as NDJSON (one JSON object per line — preferred for log pipelines)
strata audit export --format ndjson --out audit-$(date +%Y%m).ndjson

# Export the last 30 days and pipe into jq
strata audit export --since 2026-06-01T00:00:00Z --format ndjson | jq 'select(.success == false)'
```

### `audit diff`

```
strata audit diff FROM_ID TO_ID [standard options]
```

Show the YAML configuration changes between two deployment executions. Retrieves the `commit_sha` fields from both deploy-log entries and runs `git diff <sha_before> <sha_after>` on the deployment YAML file. Useful for auditors who need to see exactly what configuration values changed between two deployments.

| Argument  | Description                                                   |
| --------- | ------------------------------------------------------------- |
| `FROM_ID` | Execution ID of the earlier deployment (from `audit changes`) |
| `TO_ID`   | Execution ID of the later deployment (from `audit changes`)   |

**Exit codes:** `0` no changes · `1` system error · `3` changes detected (scriptable CI gate)

```bash
# Compare two consecutive deployments
strata audit diff 12345678-aabb-ccdd-eeff-000000000001 12345678-aabb-ccdd-eeff-000000000002

# Machine-readable — check whether configuration changed
strata audit diff $FROM_ID $TO_ID --output json | jq '.has_changes'

# Use as a CI gate — exits 3 if configuration changed
strata audit diff $FROM_ID $TO_ID
```

**Console output:** colourised unified diff with a metadata header showing deployment name, file, commit SHAs, timestamps, and PR numbers (if PR enrichment is configured).

**JSON output keys:** `from{}`, `to{}`, `file`, `has_changes`, `diff` (raw unified diff string).

### Audit configuration

Configure audit output in the `spec.audit` block of your **configuration** YAML (`kind: configuration`):

```yaml
spec:
  deployment:
    paths:
      by-execution: "{{ deployment }}/{{ timestamp }}"
      by-tenant:    "{{ tenant }}/{{ deployment }}/{{ timestamp }}"

    audit:
      path: .strata/deploy-log         # base directory (relative to workspace root)
      structure: by-execution          # built-in or inline Jinja2 template
      repository: xyz-configuration    # registered solution repo to push audit records to (optional)
```

**Path templates** — `structure` accepts one of the built-in names or an inline [Jinja2](https://jinja.palletsprojects.com/) template. Built-in options:

| Name             | Pattern                                                        | Best for                               |
| ---------------- | -------------------------------------------------------------- | -------------------------------------- |
| `flat`           | `{deployment}/`                                                | Simple single-env setups               |
| `by-stage`       | `{deployment}/{stage}/`                                        | Per-stage history                      |
| `by-execution`   | `{deployment}/{timestamp}/`                                    | **Default** — one folder per run       |
| `by-date`        | `{deployment}/{date}/{timestamp}/`                             | Group runs by calendar day             |
| `by-environment` | `{environment}/{deployment}/{timestamp}/`                      | Multi-env: browse by environment first |
| `by-workspace`   | `{workspace}/{deployment}/{timestamp}/`                        | Multi-workspace                        |
| `by-tenant`      | `{tenant}/{deployment}/{timestamp}/`                           | Multi-tenant                           |
| `full`           | `{tenant}/{workspace}/{environment}/{deployment}/{timestamp}/` | Enterprise: all layers                 |

Available template tokens:

| Token               | Example                | Always available |
| ------------------- | ---------------------- | ---------------- |
| `{{ deployment }}`  | `xyz_platform_prd`     | Yes              |
| `{{ workspace }}`   | `xyz_platform`         | Yes              |
| `{{ environment }}` | `prd`                  | Yes              |
| `{{ timestamp }}`   | `2026-06-24T14-32-00Z` | Yes              |
| `{{ date }}`        | `2026-06-24`           | Yes              |
| `{{ tenant }}`      | `example-xyz`          | No (if labelled) |
| `{{ version }}`     | `1.0.0`                | No (if labelled) |
| `{{ stage }}`       | `infrastructure`       | Per-stage only   |

### SIEM sink configuration

Configure structured event forwarding to external immutable stores in the `spec.audit.sinks[]` block of your **configuration** YAML. Each sink forwards every `deploy_audit` event to an external receiver.

**Built-in sink types** (no integration credentials required):

```yaml
spec:
  audit:
    sinks:
      - name: local-ndjson
        type: ndjson
        enabled: true
        path: .strata/audit-events.ndjson   # appended on every deployment

      - name: ops-webhook
        type: webhook
        enabled: true
        url: https://hooks.example.com/audit
        headers:
          X-API-Key: "${WEBHOOK_SECRET}"

      - name: syslog-server
        type: syslog
        enabled: true
        address: "syslog.internal:514"      # UDP
```

**Integration-backed sinks** (`sentinel`, `elk`, `otel`) — reference an integration declared in `spec.integrations[]`:

```yaml
spec:
  integrations:
    # Azure Sentinel — DCR Logs Ingestion API
    - name: sentinel-audit
      type: sentinel
      enabled: true
      endpoints:
        address: https://my-dce.eastus-1.ingest.monitor.azure.com
      properties:
        data_collection_rule_id: dcr-abc123abc456abc789
        stream_name: Custom-DeployAudit_CL
      # Authentication: DefaultAzureCredential — managed identity, service principal, or az CLI

    # ELK — TCP (Logstash JSON codec input)
    - name: elk-audit
      type: elk
      enabled: true
      endpoints:
        address: "logstash.internal:5000"   # host:port for TCP
      properties:
        protocol: tcp                        # or "http" for Elasticsearch Bulk API

    # OpenTelemetry — OTLP/HTTP to any OTel-compatible backend
    - name: otel-audit
      type: otel
      enabled: true
      endpoints:
        address: https://otel-collector.internal:4318
      properties:
        resource_attributes:
          environment: production
          team: platform

  audit:
    sinks:
      - name: sentinel
        type: integration
        integration: sentinel-audit
        enabled: true
        events: [deploy_audit]
      - name: elk
        type: integration
        integration: elk-audit
        enabled: true
      - name: otel
        type: integration
        integration: otel-audit
        enabled: true
```

**Non-blocking design:** SIEM delivery failures are always logged at `WARNING` level but never fail a deployment. The deploy-log JSON on disk is the primary source of truth; SIEM sinks are best-effort secondary delivery.

---

## `service`

Deploy and manage individual services (namespaces or modules). Useful for selective deployments, health checks, and targeted updates to specific services without deploying the entire deployment.

### `service list`

```
strata service list -f FILE [standard options]
```

List all services declared in a deployment definition. Shows namespace and module names.

| Option    | Description                                  |
| --------- | -------------------------------------------- |
| `-f FILE` | ✅ Required. Path to the deployment YAML file |

```bash
strata service list -f xyz-deploy-prd.yaml
strata service list -f xyz-deploy-prd.yaml --output json
```

**JSON output keys:** `deployment`, `services[]` (each with `name`, `type` — namespace or module, `provisioner`, `configured_replicas`).

### `service status`

```
strata service status -f FILE NAME [standard options]
```

Show runtime status of a specific service by name (live query against the provisioner backend).

| Option    | Description                                         |
| --------- | --------------------------------------------------- |
| `-f FILE` | ✅ Required. Path to the deployment YAML file        |
| `NAME`    | ✅ Required. Service name as shown in `service list` |

```bash
strata service status -f xyz-deploy-prd.yaml core-services
strata service status -f xyz-deploy-prd.yaml my-namespace --output json
```

**JSON output keys:** `service`, `type`, `status`, `replicas_ready`, `replicas_total`, `events[]`, `resources[]`.

### `service deploy`

```
strata service deploy -f FILE NAME [--force] [--dry-run] [standard options]
```

Deploy or update a single service by name. Runs the provisioner only for that service's stages.

| Option      | Description                                  |
| ----------- | -------------------------------------------- |
| `-f FILE`   | ✅ Required. Path to the deployment YAML file |
| `NAME`      | ✅ Required. Service name to deploy           |
| `--force`   | Continue even if health check fails          |
| `--dry-run` | Validate and plan only — no provisioners run |

```bash
strata service deploy -f xyz-deploy-prd.yaml core-services
strata service deploy -f xyz-deploy-prd.yaml my-namespace --dry-run
strata service deploy -f xyz-deploy-prd.yaml my-namespace --force --output json
```

**Exit codes:** `0` success · `1` execution error · `3` service not found or health check failed (without `--force`).

### `service destroy`

```
strata service destroy -f FILE NAME [--force] [standard options]
```

Tear down a single service by name. Runs destroy provisioners for that service only.

| Option    | Description                             |
| --------- | --------------------------------------- |
| `-f FILE` | ✅ Required. Path to the deployment file |
| `NAME`    | ✅ Required. Service name to destroy     |
| `--force` | Required. Non-interactive destroy.      |

```bash
strata service destroy -f xyz-deploy-prd.yaml core-services --force
```

---

## `manifest`

Query and export deployment manifests. Build and deploy manifests are first-class records captured at artifact generation and deployment execution time. They contain configuration snapshots, SBOM data, policy results, and deployment outcomes.

### `manifest list`

```
strata manifest list [--deployment NAME] [--last N] [--work-path PATH] [standard options]
```

List all deployment manifests in the workspace. Manifests are stored in `.strata/build/{deployment_name}/manifest.json` (build manifests) and `.strata/deploy/{deployment_name}_{timestamp}.json` (deploy manifests).

| Option              | Description                                   |
| ------------------- | --------------------------------------------- |
| `--deployment NAME` | Filter by deployment name (optional)          |
| `--last N`          | Show only the last N manifests (default: all) |

```bash
strata manifest list
strata manifest list --deployment xyz-deploy-prd
strata manifest list --last 20 --output json
```

**JSON output keys:** `manifests[]` — each with `path`, `deployment`, `action`, `status`, `started_at`, `deployed_by`.

### `manifest show`

```
strata manifest show [--deployment NAME] [--last 1] [standard options]
```

Display the full content of a deployment manifest (build or deploy). By default shows the most recent manifest; use `--deployment` and `--last` to select a specific one.

| Option              | Description                                      |
| ------------------- | ------------------------------------------------ |
| `--deployment NAME` | Show manifest for this deployment (required)     |
| `--last N`          | Show the Nth most recent manifest (default: `1`) |

```bash
strata manifest show --deployment xyz-deploy-prd
strata manifest show --deployment xyz-deploy-prd --last 5
strata manifest show --deployment xyz-deploy-prd --output json
```

**Displayed fields:** `kind`, `apiVersion`, `meta` (name, timestamp), `spec` (deployment name, action, status, configuration snapshot, SBOM, policy results, stages, commit hash).

### `manifest export`

```
strata manifest export [--deployment NAME] [--last N] [--format json|csv] [--out PATH] [standard options]
```

Export manifests to a file for compliance, audit, or external tooling. Supports JSON and CSV formats.

| Option              | Description                                     |
| ------------------- | ----------------------------------------------- |
| `--deployment NAME` | Export manifests for this deployment (required) |
| `--last N`          | Export only the last N manifests                |
| `--format`          | Output format: `json` (default) or `csv`        |
| `--out PATH`        | Write to this file (default: stdout)            |

```bash
strata manifest export --deployment xyz-deploy-prd
strata manifest export --deployment xyz-deploy-prd --last 30 --format csv --out manifests.csv
strata manifest export --deployment xyz-deploy-prd --format json --out manifests.json
```

---

## `mcp`

Model Context Protocol server for AI agent integration. Start an MCP server that exposes strata workspace operations as tools, allowing AI assistants (Claude, GitHub Copilot, custom agents) to validate, plan, and query infrastructure without parsing CLI output.

### `mcp serve`

```
strata mcp serve [--transport stdio|sse]
```

Start the strata MCP server. The server exposes the following tools to connected AI clients:

| Tool             | Action                                             |
| ---------------- | -------------------------------------------------- |
| `validate_file`  | Validate a single YAML file                        |
| `validate_glob`  | Validate files matching a glob pattern             |
| `build_plan`     | Preview a build (platform, Terraform, SBOM)        |
| `deploy_plan`    | Preview a deploy (what would run, stages, changes) |
| `deploy_status`  | Query live infrastructure state and drift          |
| `audit_query`    | Query deployment history and audit logs            |
| `scaffold_file`  | Generate a new YAML file from a template           |
| `resolve_values` | Resolve variables, secrets, features for a deploy  |
| `policy_check`   | Evaluate deployment policies                       |

| Option        | Type             | Default | Description                                                         |
| ------------- | ---------------- | ------- | ------------------------------------------------------------------- |
| `--transport` | `stdio` \| `sse` | `stdio` | Transport protocol. Use `stdio` for VS Code; `sse` for web clients. |

**Setup (VS Code):**

Add to `.vscode/settings.json` or user settings:

```json
{
  "mcp-servers": {
    "strata": {
      "command": "uv",
      "args": ["run", "strata", "mcp", "serve", "--transport", "stdio"],
      "cwd": "${workspaceFolder}"
    }
  }
}
```

Then restart VS Code and Copilot Chat will gain access to strata workspace operations.

**Setup (Claude Desktop):**

Add to `~/.config/claude/claude.json` (macOS) or `%APPDATA%\Claude\claude.json` (Windows):

```json
{
  "mcpServers": {
    "strata": {
      "command": "uv",
      "args": ["run", "strata", "mcp", "serve", "--transport", "stdio"],
      "cwd": "/path/to/workspace"
    }
  }
}
```

Restart Claude Desktop and the server will be available.

**Requires the optional mcp dependency:**

```bash
pip install xyz-strata[mcp]
```

```bash
strata mcp serve
strata mcp serve --transport sse
```

---



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

### `values set`

```
strata values set -f FILE -k KEY {--value VALUE | --from-file PATH | --stdin} [standard options]
```

Write a value to its configured store backend. Behaviour depends on the store type:

| Store type                                                                                                                 | Action                                                                        |
| -------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------- |
| `constant`                                                                                                                 | Prints the file path and key location (cannot write to YAML)                  |
| `environment`                                                                                                              | Prints the env var name and export instruction                                |
| `github`                                                                                                                   | Calls `gh secret set` via the GitHub CLI                                      |
| Integration-backed (`azure-keyvault`, `vault`, `consul`, `azure-appconfig`, `bitwarden`, `infisical`, `etcd`, `flagsmith`) | Calls the integration's `set_variable`, `set_secret`, or `set_feature` method |

Multiline values (SSH keys, certificates) use `--from-file` or `--stdin`:

```bash
strata values set -f xyz-deploy-prd.yaml -k DB_HOST --value "new-host.example.com"
strata values set -f xyz-deploy-prd.yaml -k TLS_CERT --from-file cert.pem
cat key.pem | strata values set -f xyz-deploy-prd.yaml -k SSH_KEY --stdin
```

### `values resolve`

```
strata values resolve -f FILE [-k KEY] [--probe] [standard options]
```

Diagnose the resolution chain for each value key without revealing actual values.
Reports store type, integration registration, availability, and optionally
(with `--probe`) whether the reference exists in the backend.

| Flag      | Effect                                                                    |
| --------- | ------------------------------------------------------------------------- |
| `-k KEY`  | Diagnose a single key only                                                |
| `--probe` | Also attempt resolution against backends (pass/fail only, no value shown) |

```bash
strata values resolve -f xyz-deploy-prd.yaml
strata values resolve -f xyz-deploy-prd.yaml -k DB_PASSWORD
strata values resolve -f xyz-deploy-prd.yaml --probe
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