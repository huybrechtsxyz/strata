# Livingston — History

## Core Context

Tester / QA for xyz-platform. pytest, Click CliRunner, Pydantic model testing, integration mocking.
User: Vincent Huybrechts. Stack: Python 3.13, pytest, nox, Click testing.
Key paths: `tests/xyz_platform/`, `conftest.py`, `noxfile.py`.

## Learnings

### 2026-05-18 — devcontainer scaffold tests

**What was added:** `TestSolutionControllerScaffoldDevcontainer` class in
`tests/xyz_platform/controllers/test_controllers_solution.py` (7 tests).

**Patterns followed:**
- All tests use `tmp_path` fixture; no real disk I/O beyond the temp dir.
- `get_pkg_templates_path` is patched via
  `patch("xyz_platform.controllers.solution_controller.get_pkg_templates_path", return_value=<Path>)`
  so each test controls exactly which template files exist.
- A `_make_templates(templates_root)` static helper writes minimal
  `devcontainer/devcontainer.template.json` and `devcontainer/post-create.sh`
  into the patched templates root; other scaffold sections (solution, configuration,
  integrations) simply skip because their subdirs don't exist.
- `_make_ctrl(work_path)` creates `.platform/` and sets `ctrl._solution` directly —
  matching the project-wide convention of avoiding full init/save round-trips.
- `_scaffold_platform_dir()` is called directly (single-underscore private method,
  callable from tests).
- Idempotency tested by pre-writing the destination file and asserting its content
  is unchanged after the scaffold call.
- Graceful-skip tested by creating the templates root without a `devcontainer/`
  subdir and asserting `.devcontainer/` is never created.

**Test file location:** `tests/xyz_platform/controllers/test_controllers_solution.py`
**Import added:** `from pathlib import Path` and `from unittest.mock import patch`

### 2026-05-19 — sln group test pattern

**Pattern for testing sln subcommands:**
- Test class naming: `TestSln<Verb>` (e.g., `TestSlnInit`, `TestSlnClean`, `TestSlnStatus`, `TestSlnExport`).
- CLI invocation must include the group prefix: `runner.invoke(main, ["sln", "init", ...])` — not `["init", ...]`.
- Existing command tests (`test_commands_init.py`, `test_commands_clean.py`, `test_commands_status.py`) were updated in-place: class renamed, invocation updated.
- New subcommand tests (e.g., `test_commands_sln_export.py`) live in `tests/strata/commands/` — same directory as other command tests.
- New subcommand modules live under `src/strata/commands/sln/` — import path: `from strata.commands.sln.export_template_command import ...`.
- **25 sln command tests passing after this session.**
