# CLI Preferences and Defaults

Some CLI options apply across many commands — output format, verbosity, quiet mode. Rather than repeating `--output json --verbose` on every invocation, the CLI supports two mechanisms to set persistent defaults.

## Option A: Environment Variables

Set env vars in your shell profile (local dev) or pipeline definition (CI/CD). These are read at startup and used as defaults when the corresponding flag is not explicitly passed.

| Env var         | Equivalent flag | Example value             |
| --------------- | --------------- | ------------------------- |
| `XYZ_OUTPUT`    | `--output`      | `json`, `text`, `console` |
| `XYZ_VERBOSE`   | `--verbose`     | `true`, `1`               |
| `XYZ_QUIET`     | `--quiet`       | `true`, `1`               |
| `XYZ_WORK_PATH` | `--work-path`   | `/path/to/workspace`      |

**Local dev — PowerShell profile (`$PROFILE`):**

```powershell
$env:XYZ_OUTPUT = "console"
$env:XYZ_WORK_PATH = "C:\Projects\myworkspace"
```

**Local dev — bash/zsh profile (`~/.bashrc` or `~/.zshrc`):**

```bash
export XYZ_OUTPUT=console
export XYZ_WORK_PATH=/home/user/projects/myworkspace
```

**CI/CD — Azure Pipelines:**

```yaml
- script: xyz build
  env:
    XYZ_OUTPUT: json
    XYZ_WORK_PATH: $(Pipeline.Workspace)/myworkspace
```

**CI/CD — GitHub Actions:**

```yaml
- run: xyz build
  env:
    XYZ_OUTPUT: json
    XYZ_WORK_PATH: ${{ github.workspace }}/myworkspace
```

Pros:
- Zero workspace dependency — works before `xyz init`
- Standard pattern — well understood by CI/CD systems
- Per-shell overrides are easy (`XYZ_OUTPUT=json xyz validate` for one invocation)

Cons:
- Global to the shell — affects all workspaces open in the same session
- Must be configured separately per machine / pipeline

---

## Option B: Workspace Config (`xyz set`)

> **Requires:** `xyz init` to have been run — writes to `.xyz_platform/cli.yaml`.

Store preferences in the workspace itself. Because `.xyz_platform/` is workspace-scoped, different
workspaces can have different defaults.

```bash
xyz set output json        # all commands in this workspace default to JSON output
xyz set verbose true
xyz set list               # show current workspace defaults
xyz set unset output       # remove the override, fall back to built-in default
```

Stored in `.xyz_platform/cli.yaml`:

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
- Self-documenting via `xyz set list`

Cons:
- Requires an initialised workspace
- Not available before `xyz project init`

---

## Resolution Order (highest to lowest priority)

```
--flag explicitly passed
  └─ XYZ_* environment variable
      └─ .xyz_platform/cli.yaml (xyz set)
            └─ built-in default (hardcoded in CLI)
```

This means:

- CI/CD: use `--work-path` or `XYZ_WORK_PATH` — explicit, deterministic, no filesystem dependency
- Local dev: run `xyz set output console` once after `xyz init` and forget about it
- One-off override: prefix any command with `XYZ_OUTPUT=json xyz validate`

---

## Work Path Resolution (special case)

`--work-path` / `XYZ_WORK_PATH` additionally supports **directory walking**: if neither the flag nor the env var is set, the CLI walks up from CWD looking for `.xyz_platform/`. This means on a local machine you can `cd` anywhere inside the workspace tree and commands just work.

```
/myworkspace/
  .xyz_platform/      ← found here → work-path resolved
  repo-a/
    src/
      ← user runs `xyz build` here, walks up two levels, finds it
  repo-b/
```

In CI/CD pipelines the checkout directory is rarely predictable, so always set `XYZ_WORK_PATH` or `--work-path` explicitly there.

## Keyword Reference

The following keywords are common CLI verbs and their typical meaning. Use `--output json` for automation when appropriate.

| Keyword             | Use                                                                                                              |
| ------------------- | ---------------------------------------------------------------------------------------------------------------- |
| `list`              | Enumerate multiple items (short summary). Example: `xyz config list` — shows keys and brief values.              |
| `show` / `get`      | Display a single resource's content or value; include source with `--source`. Example: `xyz config show output`. |
| `dump` / `view`     | Output the full merged config or raw file (machine-friendly). Example: `xyz config dump --output json`.          |
| `info` / `describe` | Human-friendly overview or summary of workspace state (counts, last run, repos). Example: `xyz workspace info`.  |
| `status`            | Operational or sync state for resources (repos, deployments). Example: `xyz repo status <name>`.                 |
| `show-file`         | Explicitly show raw file content (alias of `show` for files). Example: `xyz config show cli.yaml`.               |
| `source`            | Show origin of a value (env, `cli.yaml`, builtin). Example: `xyz config show output --source`.                   |

