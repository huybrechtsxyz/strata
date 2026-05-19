# Squad Decisions

## Active Decisions

### 2026-05-19 — Separate VS Code task sets for SDK vs config repos
- **Decision:** VS Code `tasks.json` in configuration/operator repos must NOT include SDK development tasks (`Check: lint + format + types`, `uv run xyz-platform`). Operator repos contain only `xyz` CLI tasks: `xyz: validate`, `xyz: deploy run`, `xyz: build run`, and a generic `xyz` fallback.
- **Rationale:** Config repo users are operators, not SDK developers. The platform SDK's `tasks.template.json` is for SDK dev workspaces and correctly retains SDK tasks.
- **Implications:** Any config/operator repo generated or updated by `xyz init` or related scaffolding uses operator-focused tasks only. The `configFile` promptString (default `@haven/deploy/deploy-prd.yaml`) is the standard input for file-targeting tasks in config repos.
- **Proposed by:** Linus

### 2026-05-19 — README Quick Start consolidation (pending Danny review)
- **Decision:** Collapsed `## Quick Install` and `## Quick Start` in `README.md` into a single `## Quick Start` section (4 commands: install, init, validate, deploy). Dev-install detail (`uv sync`) retained as inline note; deeper workflow lives in `docs/platform/getting-started.md`.
- **Rationale:** Old Quick Start (6 commands) was overwhelming for first-time readers. Two separate sections caused scroll/redundancy. New Getting Started guide carries the full workflow.
- **Implications:** Users needing dev install detail should follow Getting Started guide. Consider removing any stale `docs/README.md#quick-start` references. **Requires Danny's review and acceptance.**
- **Proposed by:** Reuben

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

### ~~2026-04-23 — CLI surface for solution management: `xyz solution <verb>`~~ (SUPERSEDED 2026-05-05)
- **Superseded by:** 2026-05-05 flat CLI structure decision (see below)
- ~~Decision: Use `xyz solution init`...~~

### ~~2026-05-04 — CLI surface for repository management: `xyz solution repo <verb>`~~ (SUPERSEDED 2026-05-05)
- **Superseded by:** 2026-05-05 flat CLI structure decision (see below)
- ~~Decision: Use `xyz solution repo add|remove|list`...~~

### 2026-05-05 — Flat top-level CLI structure (no solution wrapper)
- **Decision:** All top-level commands are flat: `xyz <group> <command>`. No `solution` wrapper noun. Canonical commands: `xyz init`, `xyz clean`, `xyz repo *`, `xyz profile *`, `xyz ref *`, `xyz config *`, `xyz audit *`.
- **Rationale:** `solution` wrapper added depth without clarity gain. Each noun group (`repo`, `profile`, `ref`) is already scoped by name. Flat structure matches the project's target audience (CLI-native DevOps users).
- **Implications:** `cli_solution.py` deleted. `cli.py` registers groups directly at root. Any new top-level features (`build`, `deploy`) follow the same flat pattern.

### 2026-05-05 — `build` and `deploy` commands deferred
- **Decision:** `xyz build` and `xyz deploy` are deferred. Not in scope for the current milestone.
- **Rationale:** Core workspace management (init, repo, profile, ref) must be stable first.
- **Implications:** No build/deploy code. When added, they register as flat top-level commands in `cli.py`.

## Governance

- All meaningful architectural changes require a decision entry here
- Danny triages and records decisions — other agents propose via decisions/inbox/
- Keep decisions focused on direction, not implementation detail
