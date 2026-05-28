# GitHub Copilot Instructions — strata

Python DevOps CLI tool. Click + Pydantic v2 + structlog. Python 3.13. Package managed with `uv`.

---

## Architecture Layers

Code is organized into six layers. Respect the dependency direction — lower layers never import higher ones.

```
commands/     ← Click CLI entry points (thin wrappers, call BaseCommand subclasses)
controllers/  ← Orchestrate multiple services + integrations for a single operation
services/     ← Load, validate, and expose a single YAML model type
integrations/ ← Subprocess wrappers for external tools (git, terraform, docker, etc.)
models/       ← Pydantic v2 models for YAML documents
utils/        ← Pure utilities (no business logic, no service imports)
```

---

## Models

- Always use `PlatformName` (from `common_models`) for `name` fields — never plain `str`.
- Always use `PlatformKind` enum for `kind` fields.
- Always use `PlatformVersion` enum for `apiVersion` fields.
- All models extend `pydantic.BaseModel`. Use Pydantic v2 APIs only — no v1 shims.
- Use `Annotated` + `StringConstraints` for constrained string types.
- Use `model_validator(mode="after")` for cross-field validation.
- Use `field_validator` with `@classmethod` for single-field validation.
- **Do not** call `Path.exists()` inside model validators — models must load without a real filesystem.

---

## Services

- Every service extends `BaseService` (`services/base_service.py`).
- Use `BaseService.load(path)` — never instantiate services directly. It handles caching via `service_cache.py`.
- Two-phase validation: Phase 1 = Pydantic structural, Phase 2 = `_validate_dynamic(configuration_model, work_path)` for cross-references.
- `@repo_name/path` references are resolved via `utils/system.py::resolve_path(base_path, ref, repo_map)`. Never inline this logic.
- `repo_map` comes from `ConfigurationService.get_repo_map()`. It must be built before any `@`-reference is resolved.
- Services accumulate errors into `self._errors` — never raise from inside validate loops.

---

## Controllers

- Every controller extends `BaseController` (`controllers/base_controller.py`).
- Controllers accumulate errors/messages via `self._add_error()` / `self._add_message()`.
- Controllers orchestrate; services validate. No YAML loading inside controllers.
- **Controllers never import or instantiate other controllers.** Inter-controller dependencies are forbidden. If a workflow requires the output of multiple controllers, the **command** layer is responsible for instantiating each controller, calling them in order, and passing outputs between them. Controllers are unaware of each other.

---

## Integrations

- Every integration extends `BaseIntegration` (`integrations/base_integration.py`).
- Integrations are singletons — use the factory: `IntegrationFactory.create(config)`.
- Never call subprocess directly — use `self._run_integration(args, cwd, timeout)`.
- Always check `is_available()` before executing integration operations.
- Declare required integrations in `BaseCommand.get_required_integrations()`.

---

## CLI Commands

- All top-level commands are flat: `strata <group> <command>`.
- Every command module lives in `commands/` and registers to the `main` Click group in `cli.py`.
- Registered command groups: `sln`, `config`, `log`, `repo`, `profile`, `ref`, `values`, `validate`, `version`, `help`, `new`, `schema`, `build`, `deploy`, `diff`, `vars`, `tools`.
- The `sln` group manages solution lifecycle: `sln init`, `sln clean`, `sln status`, `sln export`.
- The `main` Click group loads workspace defaults from `.strata/cli.yaml` into `ctx.default_map` at startup. Always use `@click.pass_context` and read `work_path` from `ctx.obj`.
- **Never** use `sys.exit()` — always raise `click.exceptions.Exit(code)`.
- Use `handle_command_exit(command, success)` from `cli_common.py` to map to exit codes.
- Apply standard decorators from `cli_common.py`: `@click_work_path`, `@click_output_format`, `@click_output_verbose`, `@click_output_quiet`.
- Every concrete command extends `BaseCommand` (`commands/base_command.py`).

### Exit Codes

| Code | Meaning |
|------|---------|
| `0` | Success |
| `1` | System / execution failure (crash, missing file, init error) |
| `2` | Usage error — invalid CLI arguments (Click default) |
| `3` | Validation failure — file processed but invalid |

---

## Work Path Resolution

Work path is resolved once before `main()` runs and stored in `ctx.obj["work_path"]`. Resolution order:

1. `--work-path` flag (explicit, anywhere in `sys.argv`)
2. `STRATA_WORK_PATH` environment variable
3. Walk up from CWD looking for `.strata/` directory — the ancestor containing `.strata/` is returned
4. Error: "Not inside a strata workspace. Run `strata sln init`."

`resolve_work_path()` lives in `utils/system.py`. Never pass `work_path` as a constructor arg or chain — read it from `ctx.obj`.

---

## Workspace State

- The `.strata/` folder in the workspace root is the state directory.
- `solution.json` — the solution registry (`SolutionModel`), managed by `SolutionService`.
- `cli.yaml` — user preferences loaded at startup into Click's `default_map`. Manage with `strata config set|unset|list`.
- `platform.json` — the build output artifact (`PlatformArtifactModel`), written by `BuildController` (deferred).

---

## Exceptions

- All exceptions extend `PlatformError` (`exceptions/base_exception.py`).
- Use domain-specific subclasses: `ModelValidationError`, `PlatformFileNotFoundError`, `ServiceNotValidatedError`, `InvalidReferenceError`.
- Always include `message`, optionally `error_code` and `details`.
- Never raise bare `Exception` or `ValueError` in business logic — use the platform exception hierarchy.

---

## Logging

- Always use structured logging: `from strata.logger import get_logger; logger = get_logger(__name__)`.
- Pass context as keyword args: `logger.info("message", key=value, other=value)`.
- Never use `print()` for application output — use the logger or Click's `echo`.
- Log levels: `DEBUG` for internals, `INFO` for user-visible progress, `WARNING` for recoverable issues, `ERROR` for failures.

---

## YAML Document Format

All YAML config files follow Kubernetes-style structure:

```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: <kind>          # deployment | workspace | configuration | environment | ...
meta:
  name: <name>        # PlatformName: lowercase, letters/numbers/underscores/hyphens
  annotations:
    description: ...
  labels:
    version: "1.0.0"
spec:
  ...
```

Cross-repo file references use `@repo_name/relative/path.yaml` notation.

---

## Testing

- Tests live in `tests/strata/`.
- Use plain pytest classes (e.g. `class TestConfigSet:`) — never `unittest.TestCase`.
- CLI commands: use `from click.testing import CliRunner`; invoke via `runner.invoke(main, [...])`.
- Never call real external tools in tests — mock `subprocess` and integration methods.
- Test both valid and invalid YAML inputs for all model loading paths.
- Verify exit codes explicitly: `assert result.exit_code == 0`.

---

## CI / Docker

- CI runs via GitHub Actions in `.github/workflows/` using composite actions in `.github/actions/`.
- `install-python` action: installs Python + `uv`, runs `uv sync --frozen`.
- `test-python` action: runs lint, format-check, type-check, and pytest.
- Package is built with `uv build` (wheel + sdist); `dist/` is uploaded as a GitHub artifact.
- `Dockerfile.cli` — production CLI image. `Dockerfile.docs` — documentation site image.
