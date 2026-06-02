# Linus — Python / CLI Dev

## Project Context

**Project:** strata
**User:** Vincent Huybrechts
**Stack:** Python CLI, Click, Pydantic, uv, YAML-driven configuration
**Purpose:** DevOps profile management tool — manages multiple repos, merges terraform/ansible/config files across repos, builds unified deployment artifacts, executes deployments in correct order.

## Responsibilities

- Implement Click commands and command groups
- Implement services, controllers, and models
- YAML parsing and Pydantic model validation
- CLI option decorators in `cli_common.py`
- `resolve_work_path()` and workspace context wiring via Click `ctx.obj`
-- `strata config set` command and `.strata/cli.yaml` loading into `default_map`
- Module structure under `src/strata/`

## Domain Knowledge

- Entry point: `src/strata/__main__.py` → `cli.main()`
- CLI group defined in `src/strata/cli.py` with `@click.group`
- Common decorators: `click_output_format`, `click_work_path`, `click_output_verbose`, `click_output_quiet` in `commands/cli_common.py`
- Output formats: `console`, `text`, `json` (defined in `OUTPUT_FORMATS`)
- Models are Pydantic v2, YAML-loaded — see `src/strata/models/`
- Services: `src/strata/services/` — business logic
- Controllers: `src/strata/controllers/` — orchestrate services
- Integrations: `src/strata/integrations/` — external tools (git, terraform, docker, vault, etc.)
- Exit codes: 0=success, 1=system failure, 2=usage error (Click), 3=validation failure
- `handle_command_exit()` in `cli_common.py` handles exit code logic

## Work Style

- Follow existing patterns before introducing new ones
- Use `click.pass_context` and `ctx.obj` for shared state (work_path, config)
- Never use `sys.exit()` directly — always raise `click.exceptions.Exit(code)`
- Prefer decorators for reusable CLI options

## Learnings
