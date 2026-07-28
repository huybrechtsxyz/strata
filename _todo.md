# strata — Fix-it Backlog

Concrete, actionable fixes surfaced while working through `_lesson.md`. Plain
checkboxes, no ADR needed for these — small, mechanical, low-risk changes.
Work through `_lesson.md` first; come back and knock these out afterward.

- [ ] **Generate "valid kinds" lists from `PlatformKind` instead of hand-copying them.**
  (from `_lesson.md` C4) Four docs each hand-maintain their own copy of the
  `kind:` catalog and three of them are wrong: `docs/platform/commands.md` is
  missing `tenant` and wrongly includes internal-only `platform_model`;
  `.squad/templates/platform.instructions.md` lists a `datacenter` kind that
  doesn't exist anywhere in the codebase; `docs/GLOSSARY.md` lists a `workflow`
  kind that doesn't exist either (it's unbuilt ADR-0049). Only
  `.github/copilot-instructions.md` and `.github/instructions/strata.instructions.md`
  match reality. Fix: derive these lists from `PlatformKind`
  (`src/strata/models/common_models.py`) at doc-build time (or at minimum, a
  script/check that fails CI if a doc's hand-typed kind list drifts from the
  enum) instead of re-typing them by hand in N places.
