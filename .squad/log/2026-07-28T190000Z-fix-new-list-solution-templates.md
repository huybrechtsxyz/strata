# Session Log — Fix: `strata new --list` missing solution-level templates

**Date:** 2026-07-28
**Who worked:** Linus (Python/CLI Dev)

## What happened

Bug: `strata new --list` only discovered templates by walking the filesystem
(`.strata/templates/` package + workspace template dirs). Solution-level
templates declared in `solution.json`'s `spec.templates[]` (e.g. a bootstrap
scaffold like `onboard-customer`) were never listed, even though they worked
correctly when invoked directly via `strata new <name> <NAME>`.

## Fix

`src/strata/commands/new/run_new_command.py`:
- New `_load_solution_spec()` method — best-effort solution.json load, hoisted
  so it runs before the `--list` early-return path.
- `_collect_available_templates()` and `_collect_templates_with_descriptions()`
  gained an optional `solution_templates` parameter, merging solution.json
  templates in as a third source alongside package/workspace filesystem
  templates. Tagged `type: "bundle (solution)"`, synthesized description
  `"Solution template: <name> (<N> file(s))"`. Last-write-wins on name
  collision.

`tests/strata/commands/test_commands_new.py`:
- Added `test_list_includes_solution_level_template` and
  `test_template_not_found_lists_solution_level_template`.

## Outcome

- 37 passed, 0 failed in `tests/strata/commands/test_commands_new.py`.
- Lint/type checks clean on both files.
- Flagged (not actioned): the `--list` "Scaffold bundles (strata sln init
  --template <name>):" header is now misleadingly worded for solution-level
  templates, which are actually invoked via `strata new <name> <NAME>`.
  Routed to decisions inbox as an open finding for a future wording pass.

## Decisions

None — surgical bugfix, no architectural decision required.
