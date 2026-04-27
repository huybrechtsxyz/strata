# Squad Decisions

## Active Decisions

### 2026-04-22 — CLI work-path resolution strategy
- **Decision:** Resolve work-path via: `--work-path` flag > `XYZ_WORK_PATH` env var > walk up from CWD looking for `.xyz_platform/` > error
- **Rationale:** Local dev needs zero-friction (walk from CWD); CI/CD needs explicit control (flag or env var)
- **Implications:** All commands receive `work_path` via `ctx.obj` — never pass it as an argument between services

### 2026-04-22 — CLI preferences stored in workspace config
-- **Decision:** Preferences (output format, verbosity) stored in `.xyz_platform/cli.yaml` via `xyz set`. Env vars (`XYZ_OUTPUT`, etc.) override. Explicit flags override those.
- **Rationale:** Workspace-scoped defaults are more ergonomic than global user config; CI/CD uses env vars
-- **Implications:** `main()` loads `.xyz_platform/cli.yaml` into Click `default_map` before any subcommand runs

### 2026-04-22 — Python CLI only (no extension/service yet)
- **Decision:** Build the Python CLI first. VS Code extension and service variants are out of scope.
- **Rationale:** Validate the core workflow before multiplying surfaces
- **Implications:** No web server, no websocket, no VS Code API dependencies in the codebase

### 2026-04-23 — CLI surface for solution management: `xyz solution <verb>`
- **Decision:** Use `xyz solution init` (and future `xyz solution add`, `xyz solution status`, etc.) — "solution" as a top-level Click noun-group.
- **Rationale:** Mirrors the existing `session` noun-group pattern already established in `cli.py`. All solution lifecycle verbs (`init`, `add`, `remove`, `list`, `status`) have a single coherent home. Option 2 (`xyz init solution`) creates a split home; Option 3 (`xyz init`) collapses the noun and blocks future extensibility.
- **Implications:** Register `@click.group(name="solution")` in `cli.py`. Each sub-command is a `BaseCommand` subclass in `commands/`. Short alias `xyz sln` deferred until there is an explicit request.

## Governance

- All meaningful architectural changes require a decision entry here
- Danny triages and records decisions — other agents propose via decisions/inbox/
- Keep decisions focused on direction, not implementation detail
