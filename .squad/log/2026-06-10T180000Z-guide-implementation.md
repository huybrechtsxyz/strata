# Session Log — `strata guide` Implementation

**Date:** 2026-06-10T18:00:00Z
**Session type:** Feature implementation
**Agents:** Danny (architect), Linus (dev), Livingston (tester), Reuben (docs)

## Summary

Full implementation of the `strata guide` command — an advisory workspace setup checklist that maps workspace state to 7 onboarding phases and suggests the next action. Danny produced the design spec with 10 architectural decisions (AD-GUIDE-1–10). Linus created 4 new files and modified `cli.py` for registration. Livingston wrote 26 anticipatory tests covering all 16 spec scenarios across 3 test classes. Reuben updated `docs/platform/commands.md` with the full guide reference section including workspace mode, file mode, and project customisation.

## Key Decisions

- `guide` is top-level (not under `sln`) — onboarding entry point reachable with zero CLI knowledge (AD-GUIDE-1)
- `INIT_REQUIRED = False` — primary use case is uninitialized workspace (AD-GUIDE-2)
- Exit code always `0` — advisory, never a CI gate (AD-GUIDE-4)
- Phase 3 hint built dynamically from missing repos list; null sentinel in YAML (AD-GUIDE-13)
- `.strata/guide.yaml` shallow-merge overrides: scalars replace scalars, `phases`/`kinds` sub-keys merged individually (AD-GUIDE-14)
- Tool check (phase 2) and deploy history (phase 9) deferred to v2 (AD-GUIDE-6, AD-GUIDE-7)
- `ChecklistItem` / `NextStepItem` module-local dataclasses — single consumer, no extraction (AD-GUIDE-9)

## Files Touched

| Layer    | New                                                                | Modified                                                     |
| -------- | ------------------------------------------------------------------ | ------------------------------------------------------------ |
| Commands | `cli_guide.py`, `guide/__init__.py`, `guide/show_guide_command.py` | `cli.py`                                                     |
| Data     | `src/strata/data/guide-hints.yaml`                                 | —                                                            |
| Tests    | `tests/strata/commands/test_guide_command.py`                      | —                                                            |
| Docs     | —                                                                  | `docs/platform/commands.md`                                  |
| Archive  | —                                                                  | `.archive/guide-command-design.md`, `.archive/onboarding.md` |
