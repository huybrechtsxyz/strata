# Danny — History

## Core Context

Lead / Architect for xyz-platform. Python DevOps CLI tool built with Click + Pydantic.
User: Vincent Huybrechts. Stack: Python 3.13, uv, Click, Pydantic v2, structlog, pytest.

## Learnings

### 2026-04-22 — Full architecture review

**CLI:** `cli.py` is an empty shell — all 7 command groups commented out, zero active subcommands.
No command files exist in `commands/` except `base_command.py` and `cli_common.py`.
The "session" terminology in cli.py comments diverges from the "project" terminology now intended.

**Models:** Complete and solid. 16 model files covering every YAML kind. `ConfigurationModel` is the
richest — providers, topologies, layering, security, repositories all Pydantic-validated.
`ProjectModel` handles the `.xyz_platform/project.json` workspace state file.

**Services:** Very solid. `BaseService` has 2-phase validate (Pydantic + `_validate_dynamic`),
load-with-cache via `service_cache.py`, lifecycle hooks. All domain services implemented:
`ConfigurationService` (singleton, deep-merge glob-pattern loading), `WorkspaceService`,
`DeploymentService`, `EnvironmentService`, `ProjectService`, `PlatformService`.

**Controllers:** 4 implemented — `IntegrationController`, `LifecycleController`,
`RepositoryController`, `ValueController` (with `inject_tf_vars` context manager).
Missing: `BuildController` and `DeployController`.

**`@repo/path` resolution:** Solid. `resolve_path()` in `utils/system.py` handles `@repo_name/rest`
via `repo_map`. `get_repo_map()` on both `ConfigurationModel` and `ConfigurationService`.
All services that resolve cross-repo refs build `repo_map` before resolution.
**Gap:** No "repos fetched?" guard — missing repos give silent `FileNotFoundError`, not a
user-facing "run xyz sync first" message.

**`work_path` resolution:** Decided in `decisions.md` (flag → env → CWD walk → error) but
**not implemented**. No `_find_work_path()` function exists anywhere. CLI startup does no
initialization orchestration (`ConfigurationService.add_configurations()` is never called).

**Top 5 priorities:** (1) work_path resolution + CLI init, (2) xyz project commands,
(3) xyz validate command, (4) BuildController + xyz build, (5) xyz deploy orchestration.
