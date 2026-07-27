# Workspace

What the `.strata/` directory is and why it matters.

## Directory Layout

After `strata sln init`, your workspace root contains:

```
.strata/
  solution.json        # workspace registry — repos, profiles, refs
  cli.yaml             # persistent CLI defaults (output format, work-path, etc.)
  logging.yaml         # logging configuration — console + rotating file
  configuration.yaml   # last merged config output (debug artifact — do not commit)
  logs/
    application.json   # structured JSON log file for this workspace
  integrations/        # integration-specific help and config templates
  .gitignore           # controls what is committed vs. ignored
```

## File Roles

| File                    | Type          | Purpose                                                                     |
| ----------------------- | ------------- | --------------------------------------------------------------------------- |
| `solution.json`         | Runtime state | Source of truth for repos, profiles, refs                                   |
| `cli.yaml`              | User config   | Persistent defaults for CLI flags                                           |
| `logging.yaml`          | User config   | Adjust log levels, sinks, and rotation                                      |
| `configuration.yaml`    | Build output  | Debug snapshot of the last merged config — do not rely on this in pipelines |
| `logs/application.json` | Runtime log   | Append-only JSONL structured log                                            |

## Multi-Repo Layout

The workspace root is NOT a code repository. It is the directory from which you
run `strata` commands. Your code repos live inside it (or alongside it, registered
with `@repo_name/` paths). The workspace root is identified by the presence of
`.strata/solution.json`.

## What `strata sln init` Does and Does Not Do

DOES:
- Creates `.strata/` with scaffold files
- Writes `solution.json` with the given `--name`
- Creates `logs/` directory

DOES NOT:
- Touch any of your existing files or directories
- Clone repositories (use `strata repo add` then `strata repo sync`)
- Create any profiles (use `strata profile add`)

See also: `strata help --topic quickstart`, `strata help --topic cross-repo`
