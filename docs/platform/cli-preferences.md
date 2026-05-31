# CLI Preferences and Defaults

Some CLI options apply across many commands — output format, verbosity, quiet mode. Rather than repeating `--output json --verbose` on every invocation, the CLI supports two mechanisms to set persistent defaults.

## Option A: Environment Variables

Set env vars in your shell profile (local dev) or pipeline definition (CI/CD). These are read at startup and used as defaults when the corresponding flag is not explicitly passed.

| Env var            | Equivalent flag  | Example value             |
| ------------------ | ---------------- | ------------------------- |
| `STRATA_OUTPUT`    | `--output`       | `json`, `text`, `console` |
| `STRATA_VERBOSE`   | `--verbose`      | `true`, `1`               |
| `STRATA_QUIET`     | `--quiet`        | `true`, `1`               |
| `STRATA_WORK_PATH` | `--work-path`    | `/path/to/workspace`      |
| `STRATA_NO_COLOR`  | `--no-color`     | `1`, `true`               |
| `NO_COLOR`         | `--no-color`     | any value (see below)     |

> **`NO_COLOR` standard:** strata respects the cross-tool [`NO_COLOR`](https://no-color.org) convention — setting `NO_COLOR` to *any* value (including empty string) disables all ANSI color output from both the CLI and the structured logger. Use `STRATA_NO_COLOR` for strata-specific control, or `NO_COLOR` when you want a single env var to silence color across all tools in a pipeline.

**Local dev — PowerShell profile (`$PROFILE`):**

```powershell
$env:STRATA_OUTPUT = "console"
$env:STRATA_WORK_PATH = "C:\Projects\myworkspace"
```

**Local dev — bash/zsh profile (`~/.bashrc` or `~/.zshrc`):**

```bash
export STRATA_OUTPUT=console
export STRATA_WORK_PATH=/home/user/projects/myworkspace
```

**CI/CD — Azure Pipelines:**

```yaml
- script: strata build
  env:
    STRATA_OUTPUT: json
    STRATA_WORK_PATH: $(Pipeline.Workspace)/myworkspace
```

**CI/CD — GitHub Actions:**

```yaml
- run: strata build
  env:
    STRATA_OUTPUT: json
    STRATA_WORK_PATH: ${{ github.workspace }}/myworkspace
```

Pros:
- Zero workspace dependency — works before `strata sln init`
- Standard pattern — well understood by CI/CD systems
- Per-shell overrides are easy (`STRATA_OUTPUT=json strata validate` for one invocation)

Cons:
- Global to the shell — affects all workspaces open in the same session
- Must be configured separately per machine / pipeline

---

## Option B: Workspace Config (`strata config set`)

> **Requires:** `strata sln init` to have been run — writes to `.strata/cli.yaml`.

Store preferences in the workspace itself. Because `.strata/` is workspace-scoped, different
workspaces can have different defaults.

```bash
strata config set output json    # all commands in this workspace default to JSON output
strata config set verbose true
strata config list               # show current workspace defaults
strata config unset output       # remove the override, fall back to built-in default
```

Stored in `.strata/cli.yaml`:

```yaml
values:
  output: json
  verbose: false
  quiet: false
```

Loaded at startup via Click's `default_map`, then merged with env vars and explicit flags.

Pros:
- Workspace-scoped — different workspaces, different defaults
- Committed to source control (or gitignored, your choice)
- Self-documenting via `strata config list`

Cons:
- Requires an initialised workspace
- Not available before `strata sln init`

---

## Resolution Order (highest to lowest priority)

```
--flag explicitly passed
  └─ STRATA_* environment variable
      └─ .strata/cli.yaml (strata config set)
            └─ built-in default (hardcoded in CLI)
```

This means:

- CI/CD: use `--work-path` or `STRATA_WORK_PATH` — explicit, deterministic, no filesystem dependency
- Local dev: run `strata config set output console` once after `strata sln init` and forget about it
- One-off override: prefix any command with `STRATA_OUTPUT=json strata validate`

---

## Work Path Resolution (special case)

`--work-path` / `STRATA_WORK_PATH` additionally supports **directory walking**: if neither the flag nor the env var is set, the CLI walks up from CWD looking for `.strata/`. This means on a local machine you can `cd` anywhere inside the workspace tree and commands just work.

```
/myworkspace/
  .strata/          ← found here → work-path resolved
  repo-a/
    src/
      ← user runs `strata build` here, walks up two levels, finds it
  repo-b/
```

In CI/CD pipelines the checkout directory is rarely predictable, so always set `STRATA_WORK_PATH` or `--work-path` explicitly there.

## Keyword Reference

The following keywords are common CLI verbs and their typical meaning. Use `--output json` for automation when appropriate.

| Keyword             | Use                                                                                                              |
| ------------------- | ---------------------------------------------------------------------------------------------------------------- |
| `list`              | Enumerate multiple items (short summary). Example: `strata config list` — shows keys and brief values.              |
| `show` / `get`      | Display a single resource's content or value; include source with `--source`. Example: `strata config show output`. |
| `dump` / `view`     | Output the full merged config or raw file (machine-friendly). Example: `strata config dump --output json`.          |
| `info` / `describe` | Human-friendly overview or summary of workspace state (counts, last run, repos). Example: `strata workspace info`.  |
| `status`            | Operational or sync state for resources (repos, deployments). Example: `strata repo status <name>`.                 |
| `show-file`         | Explicitly show raw file content (alias of `show` for files). Example: `strata config show cli.yaml`.               |
| `source`            | Show origin of a value (env, `cli.yaml`, builtin). Example: `strata config show output --source`.                   |

