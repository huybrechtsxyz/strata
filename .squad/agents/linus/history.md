# Linus — History

## Core Context

Python / CLI Dev for xyz-platform. Implements Click commands, services, controllers, models.
User: Vincent Huybrechts. Stack: Python 3.13, uv, Click, Pydantic v2, structlog, pytest.
Key paths: `src/xyz_platform/cli.py`, `commands/cli_common.py`, `models/`, `services/`, `controllers/`.

## Learnings

### 2026-04-22 — CLI/models code review

**cli.py**
- `main()` has NO `@click.pass_context`, NO `ctx.obj`, NO `default_map` loading, NO `--work-path` flag. Every command group import is commented out. The CLI literally runs nothing beyond logging init.
- The "typical flow" comment block (xyz session init / add / sync / validate / build / deploy) is also commented out from the help text — useful breadcrumb of intended surface.
- `get_cli_version()` is called in `base_command.py:113` (`system.get_cli_version()`) but that function does NOT exist in `utils/system.py`. The real function in `utils/version.py` is `get_version()`. Runtime AttributeError if `ShowConsoleHeader()` is ever called.

**commands/base_command.py**
- ABC pattern with `execute()` and `get_required_integrations()` as abstractmethods is solid.
- Lifecycle scaffold (`_Initialize`, `_BeforeExecute`, `AfterExecute`, `_Finalize`) present but empty → subclasses must wire these themselves.
- `_Initialize()` only records `_start_time`; `_project_id` and `_execution_id` are declared but never assigned.
- PascalCase on `ShowConsoleHeader` / `ShowConsoleFooter` / `_Initialize` / `_BeforeExecute` — inconsistent with Python convention; rest of codebase uses snake_case. Minor but worth standardising.
- `work_path` stored in `self._work_path` in the command — per decisions it should come from `ctx.obj`, not be a constructor arg.

**commands/cli_common.py**
- Decorator set (`click_work_path`, `click_output_format`, `click_output_verbose`, `click_output_quiet`) is clean and reusable.
- `validate_verbose_quiet_exclusive` and `validate_output_quiet_exclusive` rely on `ctx.params` order — Click processes params left-to-right so the first of a mutually-exclusive pair will see the second as not yet set. This is a known Click ordering hazard; only catches the case where the exclusive param was declared earlier.
- `click_work_path` uses `exists=False`, so it won't error on a non-existent path — this is intentional for `xyz project init` but risky for all other commands that require the workspace to exist. Consider a second decorator (`click_work_path_required`) with `exists=True`.
- `OUTPUT_FORMATS` list comprehension `[f for f in OUTPUT_FORMATS if f]` is redundant noise since the list has no falsy entries.

**models/**
- Pydantic v2 patterns are correct throughout: `model_validate`, `model_dump_json`, `field_validator` with `@classmethod`, `model_validator(mode="after")`, `Annotated` + `StringConstraints`. No v1 compat shims detected.
- `PlatformName` regex `^[a-z][a-z0-9_-]*$` is referenced everywhere — solid shared type.
- `ScriptPathModel.validate_script_path` and `ScriptsModel.validate_and_normalize_scripts` both call `Path.exists()` at parse time. This means loading a model from YAML will blow up if any script file doesn't exist at that moment — bad for cross-machine/CI use. Consider separating schema validation from filesystem validation.
- `project_model.py` uses plain `str` for `apiVersion`/`kind` instead of `PlatformVersion`/`PlatformKind` enums — no type safety at the model boundary. Other models use the enums.
- `ProjectMetaModel.name` uses plain `str` with manual non-empty check, not `PlatformName` — inconsistent with the rest of the codebase.

**utils/configuration_loader.py**
- Purely a file-I/O + deep-merge utility. Clean separation: no schema knowledge, no glob selection, no `@repo` resolution.
- `@repo/path` references are NOT handled here. They live in `utils/system.py:resolve_path()` which takes an optional `repo_map` dict. The `repo_map` is built by `ConfigurationService.get_repo_map()` and passed down through `workspace_service` and others at validation time.
- The pattern works but `repo_map` must be fully populated before any path resolution call — no lazy resolution, no partial maps.

**services/project_service.py**
- Singleton via `__new__` with `_instances` dict + lock — same pattern as other services.
- Implements `load_from_json` and `save_to_json` only. No `init_workspace`, no `create_project`, no `add_repository`, no `activate_profile` — nothing that would back `xyz project init` or `xyz project add`.
- `_validate_dynamic` is a no-op (returns True). No cross-service validation.
- Service is ready as a persistence layer (load/save JSON) but has zero business logic methods.

**Context wiring gap**
- `main()` in `cli.py` has no `@click.pass_context`, no `ctx.obj = {}`, no `--work-path` option, and no `.xyz_platform/config.yaml` loading into `default_map`. This gap is total — zero of the three decisions are implemented in `main()`.
- To wire up per decisions: add `@click.pass_context`, accept `--work-path` with env var fallback `XYZ_WORK_PATH`, implement CWD-walk for `.xyz_platform/` sentinel, load `.xyz_platform/config.yaml` into `ctx.default_map`, store resolved path in `ctx.obj['work_path']`.
