# Using strata console

`strata console` is an interactive workspace session. It keeps your workspace state in memory, re-evaluates progress after every action, and guides you through setup with tab completion and session history — without leaving the terminal.

---

## Starting the console

```bash
cd /path/to/your-workspace
strata console
```

The console works even before the workspace is initialized — it will show Phase 1 as pending and tell you what to do.

On startup you'll see:

```
╭─── strata console ─────────────────────────────────────────────╮
│ Workspace: my-platform  (/path/to/your-workspace)              │
│ ░░░░░░░░░░░░░░░░░░░░░░░░  0/8 phases complete                  │
╰────────────────────────────────────────────────────────────────╯

  ○  Workspace initialized
  ○  Repositories registered
  ○  Repositories on disk
  ○  Profile created
  ○  Profile activated
  ○  File references registered
  ○  Build artifact exists
  ○  Platform inventory generated

→ Next: Initialize the workspace to create .strata/ and solution.json

  $ strata sln init {name}

strata>
```

---

## The basic loop

The three commands you'll use most:

| Command  | What it does                                   |
| -------- | ---------------------------------------------- |
| `status` | Re-evaluates and re-renders the full checklist |
| `next`   | Shows the next pending step with its hint      |
| `do`     | Runs the suggested command for the next step   |

A typical new workspace session looks like:

```
strata> next
→ Next: Initialize the workspace

  $ strata sln init my-platform

strata> do
$ strata sln init my-platform
...
  [auto-refresh] Phase 1: ○ → ✅ Workspace initialized

strata> do
$ strata repo add my-infra https://github.com/org/infra.git
...
  [auto-refresh] Phase 2: ○ → ✅ Repositories registered

strata> status
  ✅  Workspace initialized
  ✅  Repositories registered
  ○   Repositories on disk
  ...
```

After each `do`, the console **auto-refreshes**: it re-evaluates the checklist and prints a delta showing which phases changed.

---

## Inspecting files

Use `check` to validate any YAML file and see its per-file analysis:

```
strata> check config/my-platform-config.yaml
File: config/my-platform-config.yaml  (kind: configuration)
  ✅  File exists
  ✅  Schema valid
  ✅  Kind recognized
  ⚠️   Cross-references not resolved (run with active profile)
  ○   Not registered in active profile

  → Register file: strata ref config add ...
```

The `check` command accepts paths relative to the workspace root.

---

## Scaffolding files

Use `new` to scaffold a YAML file from a template without leaving the console:

```
strata> new configuration my-platform --path config/
$ strata new configuration my-platform --path config/
Created: config/my-platform-config.yaml

strata> new environment dev --path envs/
```

List available templates:

```
strata> templates
$ strata new --list
...
```

---

## Running validation

```
strata> validate config/my-platform-config.yaml
strata> validate                                   ← validates all registered files
```

Or use the alias `v`:

```
strata> v config/my-platform-config.yaml
```

---

## Opening files

`open` launches the file in your system editor (respects `$EDITOR`, otherwise uses the OS default):

```
strata> open config/my-platform-config.yaml
strata> open .strata/workflow.yaml
```

---

## Checking tool availability

```
strata> tools
$ strata tools status
  ✅  terraform  1.9.3
  ✅  git        2.45.1
  ✅  docker     27.0.3
  ○   ansible    not found
```

---

## Reloading workspace state

If you edit files outside the console (e.g. in your editor), use `reload` to pick up the changes:

```
strata> reload
✅ Workspace state reloaded.
```

---

## Tab completion and history

- **Tab** completes command names and file paths
- **↑ / ↓** navigates command history
- History is persisted across sessions to `.strata/console-history`
- Disable color with `strata console --no-color` or set `NO_COLOR=1`

---

## All commands

| Command                 | Alias | Description                             |
| ----------------------- | ----- | --------------------------------------- |
| `status`                | `s`   | Show workspace checklist                |
| `check <file>`          | `c`   | Inspect a YAML file                     |
| `next`                  | `n`   | Show next step with hint and command    |
| `do`                    | `d`   | Execute the suggested next-step command |
| `new <template> [name]` | —     | Scaffold a file via `strata new`        |
| `validate [file\|glob]` | `v`   | Run validation                          |
| `graph [--mode]`        | `g`   | Render dependency graph                 |
| `templates`             | `t`   | List available templates                |
| `tools`                 | —     | Check external tool availability        |
| `open <file>`           | `o`   | Open file in editor                     |
| `reload`                | —     | Reload workspace state from disk        |
| `help`                  | `?`   | Show command table                      |
| `clear`                 | —     | Clear terminal                          |
| `quit`                  | `q`   | Exit console                            |

---

## Customizing the workflow

The checklist and `next` / `do` behaviour are driven by `.strata/workflow.yaml`. Edit this file to add project-specific steps, reorder phases, or change the suggested commands.

See [workflow.md](../config/workflow.md) for the full reference.

---

## Non-interactive use

The console requires an interactive terminal (`stdin` is a TTY). When `stdin` is not a TTY (piped input, CI), the prompt falls back to plain `input()` and tab completion is disabled. For scripting and CI, use the individual strata commands directly instead of `strata console`.
