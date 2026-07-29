# strata — Fix-it Backlog

Concrete, actionable fixes surfaced while working through `_lesson.md`. Plain
checkboxes, no ADR needed for these — small, mechanical, low-risk changes.
Work through `_lesson.md` first; come back and knock these out afterward.

- [x] **Generate "valid kinds" lists from `PlatformKind` instead of hand-copying them.**
  (from `_lesson.md` C4) **Done 2026-07-29.** Fixed `INTERNAL_KINDS` in
  `common_models.py` (it was only tracking 2 of the 5 real internal kinds —
  `version-lock`/`version`/`promotion-record` were being mislabeled as
  user-authorable by `strata schema list`); added `USER_AUTHORABLE_KINDS` as
  the single derived source of truth. Fixed the 3 wrong docs:
  `docs/platform/commands.md` (added `tenant`, removed internal-only
  `platform_model`), `.squad/templates/platform.instructions.md` (removed
  nonexistent `datacenter`, added `network`/`dns`/`tenant`), `docs/GLOSSARY.md`
  (removed nonexistent `workflow`, listed all 5 internal kinds explicitly).
  Added a "Kind docs coverage" step to `scripts/Check.ps1` that runs
  `strata schema list --output json` and fails CI if any of the 4
  exact-match docs drift from `PlatformKind`, or if `docs/GLOSSARY.md`
  mentions a kind that doesn't exist at all — verified it actually catches
  drift by re-introducing the `datacenter` bug and confirming the check
  fails.

- [x] **Document the three audit/status delivery mechanisms and when to use which.**
  (from `_lesson.md` D1) **Done 2026-07-29.** Added a "Which Delivery
  Mechanism To Use" section to `docs/decisions/0018-deployment-audit-traceability.md`
  (right after "Decision Outcome") laying out the local deploy-log, SIEM/webhook
  forwarding, and the gitops deployment manifest side by side in a table —
  what each one carries, who can read it and from where, and when to reach
  for it. Also documents the known narrow gap (no mechanism gives the full
  audit record from a different machine unless the deploy-log itself is
  pushed to a remote) and points to the existing Layer 4 remote-persistence
  path as the natural extension point if that's ever needed, rather than
  building a fourth mechanism speculatively.

- [x] **Extract `run_new_command.py`'s template logic into a `TemplateService`.**
  (from `_lesson.md` D6) **Done 2026-07-29.** Moved the pure discovery/resolution
  functions — `_resolve_template_path`, `_collect_available_templates`,
  `_collect_templates_with_descriptions`, `_read_bundle_description`,
  `_resolve_solution_template`, `_resolve_at_repo_path`, `_collect_dep_candidates`,
  `_extract_jinja_vars` — into the existing `src/strata/services/template_resolver.py`
  module (already the established home for `--template`/`sln init` scaffold
  resolution, so this consolidates two template-resolution code paths into one
  place instead of inventing a second parallel abstraction). Renamed without
  the leading underscore since they're now public service functions; the
  `strata new`-specific resolver was named `resolve_new_template_path` to stay
  distinct from the module's existing `resolve_template()` (different resolution
  order/sources — `sln init` only looks at package `examples/`, `strata new`
  checks workspace + package `dot.strata/templates/` + solution.json).
  `run_new_command.py` now imports these instead of defining them — its own
  scope shrank from ~330 lines of module-level helpers to just the interactive
  `_prompt_missing_vars` (uses `click.prompt`, correctly stays in the command/UI
  layer) plus the `NewCommand` class itself.
  **Scope note:** the stateful render/execution methods (`_run_file_execution`,
  `_run_bundle_execution`, `_run_solution_bundle_execution`,
  `_scaffold_missing_deps`, `_scaffold_single_dep`) were deliberately left on
  the command — they mutate `self._errors`/`self._messages`/`self._output_data`
  per `BaseCommand`'s established error-accumulation convention, and extracting
  them would require redesigning that into a return-value-based API, a bigger
  and riskier change than this todo's "low urgency" scope warranted.
  Updated `tests/strata/commands/test_commands_new.py`'s direct imports of
  `_resolve_at_repo_path`/`_collect_dep_candidates` to the new location.
  Verified: `ruff`/`mypy` clean, 54 tests pass across
  `test_services_template_resolver.py` + `test_commands_new.py`, full
  `Check.ps1` green.

- [x] **Migrate `generate`/`mask` in `cli_secret.py` onto `BaseCommand` +
  `INIT_REQUIRED = False`.** (from `_lesson.md` D7) **Done 2026-07-29 — note:
  the `INIT_REQUIRED` flag itself no longer exists** (eliminated repo-wide by
  ADR-0030 Phase 2 — confirmed via `Check.ps1`'s own "ADR 0030 migration
  guards" step, which fails CI if `INIT_REQUIRED` ever reappears). The current,
  correct pattern for "doesn't need a workspace" commands is overriding
  `_initialize()` to delegate to `BaseCommand._initialize_session()` instead
  of `super()._initialize()` — exactly what `ValidateCommand`, `GuideCommand`,
  and `NewCommand` already do. Migrated `generate`/`mask` accordingly:
  - New `GenerateSecretCommand`/`MaskSecretCommand` classes in
    `generate_secret_command.py`/`mask_secret_command.py`, `OPERATION =
    "secret_generate"`/`"secret_mask"`, `SHOW_CHROME = False` (no header/footer
    banner — matches the `base_promote_command.py`/`base_versions_command.py`
    precedent for utility-style commands).
  - **Deliberately preserved two existing, tested behavior contracts** rather
    than blindly adopting 100% of `BaseCommand`'s defaults: (1) invalid
    `--format`/`--length`/`--char` combos still raise `click.UsageError` →
    exit code 2 (not the generic exit-1 `handle_command_exit` path) — verified
    3 existing tests assert `exit_code == 2` specifically; (2) `--output text`
    and console (default) modes still print the **bare value only** (no
    envelope, no chrome) so output stays pipeable — overrode
    `_is_structured_output()` to return `True` only for `--output json`,
    since the original code's own comment said "text or console — just the
    bare value so it can be piped directly" and an existing test
    (`test_generate_output_text_explicit`) already locks this in.
  - **What did change (the actual point of the fix):** `--output json` now
    uses the shared envelope (`{"success", "command", "data": {...}, ...}`)
    instead of the old flat hand-rolled shape (`{"secret": ..., "format":
    ...}`) — updated 7 existing JSON-output tests across both
    `test_commands_secret.py` and `test_cli_secret.py` (duplicate test files,
    per T1) to unwrap `envelope["data"]`.
  - Verified: ruff/mypy clean, 118 tests pass across both secret test files,
    full `Check.ps1` green.
  - **Spot-check finding (not fixed, see new todo below):** `cli_version.py`
    imports `BaseCommand` but `version_command` is still a bare Click
    function with hand-rolled JSON — same pattern, different file. Other
    `cli_*.py` files are all consistently `BaseCommand`-based already;
    `cli_completion.py`/`cli_help.py`/`cli_mcp.py` are legitimately exempt
    (Click's own shell-completion/help machinery, not business commands).

- [x] **Migrate `strata version` (`cli_version.py`) onto `BaseCommand`.**
  (spot-check finding from the D7 fix above) **Done 2026-07-29 — resolved
  differently than proposed: removed the `version` subcommand entirely**
  rather than migrating it onto `BaseCommand`. Design discussion first
  established, empirically (wrote and ran throwaway Click scripts to verify
  rather than guessing), that combining `--output` with an eager `--version`
  flag is fragile/order-dependent in Click (`--output json --version` sees
  the value, `-v --output json` doesn't — same flags, different order, only
  one works) — and no mainstream CLI (git, docker, npm, kubectl) supports
  `--output` alongside a bare `--version` flag anyway, so there was nothing
  worth preserving there. Also, `strata version` (singular) vs. `strata
  versions` (plural, an unrelated version-lock/promotion feature) was a real,
  confusing naming collision.
  - Removed `main.add_command(version_command, name="version")`, its import,
    and its `_HELP_SECTIONS` listing from `cli.py`; deleted
    `src/strata/commands/cli_version.py` and its dedicated test file.
  - `-v`/`--version` remains exactly as before (Click's built-in
    `version_option`, already wired) — simple, standard, matches convention.
  - `--check-updates` (separate top-level eager flag, already existed)
    remains the way to check for updates — unaffected by this change.
  - `strata version` now correctly errors with "No such command 'version'.
    Did you mean 'versions'?" — no more ambiguity.
  - Added `TestVersionFlag`/`TestCheckUpdatesFlag` test classes to
    `test_cli.py` (better coverage than the deleted file had — now tests
    the actual top-level flags, including the eager-short-circuit-before-
    invalid-subcommand case) and fixed `test_all_groups_registered` to
    assert `version` is *not* in the registered commands.
  - Updated the 5 live references that invoked `strata version` as a
    command: `scripts/Check.ps1` (smoke test), `scripts/Build.ps1`,
    `.github/actions/setup-strata/action.yml`, `docs/platform/commands.md`,
    `docs/platform/workflow.md`. Left 2 historical ADR mentions
    (0020, 0030) untouched — same "historical design record, lower
    urgency" precedent established for I7.
  Verified: full `Check.ps1` green (including Sphinx build), 73 tests pass
  across `test_cli.py` + both `versions` test files.

- [x] **Fix stale `deploy status` doc pointers (docs-only, no code needed).**
  (from `_lesson.md` I7 — already tracked as ADR-0060 Phase 1, listed here too
  for a plain-checklist view) **Done 2026-07-29.** Fixed all 4 targets:
  - `output_deploy_command.py`'s docstring — now notes `deploy status` is
    deprecated and points at `deploy run` / `--refresh` instead of implying
    it's a normal companion command.
  - `docs/help/deployment.md` — replaced the `strata deploy status` CLI
    example with `strata env output` / `strata env status`.
  - `docs/guides/deploying.md` — replaced the "check current infrastructure
    state" example with `strata env status` (its own docstring — resource
    count, last-apply serial, cached-output freshness, drift — matches this
    guide's description almost verbatim, confirming it's the right
    replacement, not `env output`).
  - `docs/platform/provisioner-plugin-api.md` — **found a deeper bug while
    fixing this one, not just staleness.** The doc claimed overriding
    `status()` supports `strata deploy status` and `strata env status` —
    traced both commands' actual code and neither calls `provisioner.status()`
    at all (`deploy status` calls output-fetching methods; `env status` calls
    `terraform show -json` directly). The only real caller of
    `deployer.status()` is `strata deploy health` (confirmed in
    `health_deploy_command.py`, alongside `deployer.output()`). Corrected the
    doc to name the actual caller and added a note explaining the two
    similarly-named commands do *not* go through this method, to prevent
    future plugin authors from being misled by the naming coincidence.
  Verified: full `Check.ps1` green including the Sphinx build (not skipped
  this time, to confirm no broken doc references).

- [x] **Deduplicate manifest-artifact collector methods shared between build and deploy.**
  (from `_lesson.md` I1) **Done 2026-07-29.** Confirmed `_collect_platform_artifact`,
  `_collect_repository_info`, `_collect_provider_info` were byte-for-byte
  identical in `base_deploy_command.py` and `run_build_command.py` (only
  cosmetic differences — local vs. module-level model imports, looser type
  hints in the build side). `_collect_image_info` was **not** duplicated
  (deploy-only, reads runtime `self._stage_results` that don't exist at
  build time) — left untouched on `BaseDeployCommand`.
  - New `src/strata/services/manifest_artifact_collector.py` — 3 pure
    functions taking explicit params (`deployment_service`, `build_path`,
    `work_path`, `solution_controller`) instead of `self`, since the two
    command classes share no common base beyond `BaseCommand`. Matches this
    session's `template_resolver.py` precedent (plain function module, not a
    mixin/new base class).
  - Both `base_deploy_command.py` and `run_build_command.py` now have the 3
    methods collapsed to one-line delegates to the shared functions —
    **method names/signatures unchanged**, so the 5 existing direct-call
    tests in `test_commands_build_manifest.py` and the
    `BaseDeployCommand._collect_artifacts` mock in
    `test_commands_deploy_audit.py` needed zero changes.
  - Added `tests/strata/services/test_services_manifest_artifact_collector.py`
    — new direct unit coverage for the extracted logic (17 tests), including
    cases the old duplicated-and-only-indirectly-tested code never had
    dedicated coverage for (invalid JSON content, symbolic-ref vs.
    detached-HEAD commit resolution, missing backend/properties).
  - Cleaned up now-unused imports as a side effect: `hashlib` from
    `base_deploy_command.py`, `hashlib` + the 3 local
    `from strata.models.deployment_manifest_model import ...` statements
    from `run_build_command.py`.
  Verified: ruff/mypy clean (one single-file mypy artifact at an unrelated
  line confirmed pre-existing and absent from the real `./src ./tests`
  sweep), 179 tests pass across the new service test file +
  `test_commands_build_manifest.py` + `test_commands_deploy_audit.py` +
  `test_commands_deploy.py` + `test_commands_build.py`, full `Check.ps1`
  green including the Sphinx build.

- [x] **Generalize the "not valid on builtin store" validators in `store_models.py`.**
  (from `_lesson.md` I2) **Done 2026-07-29.** Confirmed all 4 validators —
  `SecretStoreModel.validate_generate_not_on_builtin`,
  `validate_rotate_not_on_builtin`, `VariableStoreModel` and
  `FeatureStoreModel`'s `validate_default_not_on_builtin` — had the identical
  9-line shape, differing only in the field checked, the model's builtin-store
  set, and the suggested-stores text. Extracted a shared
  `_validate_field_not_on_builtin(*, kind_noun, key, field_name, field_value,
  store, builtin_types, suggested_stores)` helper; each `@model_validator`
  method now stays defined (Pydantic v2 requires them per-model — can't be
  fully eliminated) but its body collapses to a single call, ~9 lines → ~10
  lines of keyword-argument call but zero duplicated logic/message template.
  Existing tests (`test_store_models.py`, `test_secret_rotation.py`) assert
  `"built-in store type" in str(exc_info.value)` — message text preserved
  exactly, zero test changes needed. Added
  `tests/strata/models/test_store_models_validate_helper.py` — 4 new direct
  unit tests for the helper itself (no-op cases, exact message shape,
  per-kind-noun variation), since the shared logic itself had no dedicated
  test before (only indirectly exercised through each model).
  Verified: ruff/mypy clean, 139 tests pass across the new helper test file +
  `test_store_models.py` + `test_secret_rotation.py` + `test_controllers_value.py`,
  full `Check.ps1` green including the Sphinx build.

- [ ] **Revisit ADR-0020 — reconcile `strata new`'s shipped interface with the standard, and re-check its "completed" status.**
  (from `_lesson.md` I4) ADR-0020 mandates templates as a `--template TEMPLATE`
  flag, never positional, with `strata new NAME --template namespace` as its
  own worked example — but `cli_new.py` actually has `template` and `name` as
  two positional arguments (template first), the opposite of both the rule
  and the ADR's own documented reference for this exact command. Two things
  need doing, not just one: (1) decide whether to migrate `cli_new.py` to
  match the ADR (a real breaking CLI change — needs a deprecation path, e.g.
  accept both positional and `--template` for a release or two before
  removing positional support, not a same-day patch), and (2) separately
  correct ADR-0020's "completed" status, since its own roadmap only ever
  promised incremental migration, not full conformance — "completed" oversells
  what's actually true today.

- [ ] **Write missing tests for `put`/`get`/`rotate`/`status`/`list` secret commands.**
  (from `_lesson.md` T1) `tests/strata/commands/test_commands_secret.py` and
  `test_cli_secret.py` cover `generate`/`mask` exhaustively (format, length,
  password-validation, JSON-output variants) but have **zero** tests for the
  other five secret subcommands. This is the most sensitive command group in
  the CLI (writes/reads/rotates actual secret values) with no coverage on its
  core write-path. Highest-priority test gap in the codebase.

- [ ] **Add a `pytest`/coverage step to `scripts/Check.ps1`.**
  (from `_lesson.md` T2) `Check.ps1` currently runs ruff lint/format, mypy, a
  CLI smoke test, docs-index coverage, and a Sphinx build — but never runs the
  actual test suite. This is the root cause that let T1's coverage gap persist
  invisibly. Add a `uv run pytest` (with `--cov` reporting, no hard threshold
  needed initially) step so future gaps surface in the standard check flow
  instead of requiring a manual audit to find.

- [ ] **Clean up the vestigial `IMPL_MISSING` try/except guards in policy tests.**
  (from `_lesson.md` T4 — low priority, cosmetic) ~26 files have a
  `try: import ... IMPL_MISSING = False / except ImportError: IMPL_MISSING =
  True` + `pytest.mark.skipif(IMPL_MISSING, ...)` pattern left over from when
  the referenced policies didn't exist yet. All the imports succeed today (54
  passed, 0 skipped verified via direct pytest run), so the except-branch is
  dead code. Not urgent — purely a readability cleanup, no functional impact.

- [ ] **Fix the recurring mypy `--check-untyped-defs` blind spot.**
  (from `_lesson.md` T5) The same file list has shown up in every `Check.ps1`
  run this session: `test_controllers_lifecycle.py`, `test_services_deployment.py`,
  `cve_max_severity_policy.py` (a real source file, not a test), `test_deployers_compose.py`,
  `test_commands_new.py`, `test_commands_deploy_version_file.py`,
  `test_commands_deploy.py`. Add type annotations to the untyped function
  bodies mypy is flagging (or enable `--check-untyped-defs` and fix what comes
  up) — low-cost, has just been recurring unaddressed.

- [ ] **Add a consistent "not yet implemented" visual marker for draft guides.**
  (from `_lesson.md` X2) `docs/guides/at-scale.md`'s draft status is a single
  easy-to-miss blockquote line. Define one consistent admonition-style marker
  for guides describing unbuilt functionality and audit the other guides for
  whether they need it too.

- [ ] **Cross-link overlapping guides and ADRs.**
  (from `_lesson.md` X3) Guides and ADRs describing the same topic (e.g.
  `deployment-manifests.md` guide vs. ADR-0021; `how-deployment-locking-works.md`
  guide vs. ADR-0007) don't link to each other. Add a lightweight convention —
  e.g. a header note in the guide pointing back to its originating ADR — so
  readers know which is current without guessing.

- [ ] **Add a drift check between `docs/INDEX.md` and `docs/index.rst`.**
  (from `_lesson.md` X4) Both are hand-maintained lists of "what docs exist"
  with no automated check tying them together — the same failure mode already
  proven real for the ADR index (X1) and `PlatformKind` doc lists (C4). Add a
  script/CI check that flags when one references a doc the other doesn't (or
  document explicitly that `docs/INDEX.md` is intentionally a curated subset,
  if that's the actual intent, so future contributors don't assume drift is a
  bug).

- [ ] **De-duplicate `strata-onboarding.md` between `docs/skills/` and `.github/skills/`.**
  (from `_lesson.md` X5) Two copies of the same onboarding-skill content exist
  in this repo (`docs/skills/strata-onboarding.md` and
  `.github/skills/strata-onboarding.md`) with no single source of truth —
  drift risk if one is updated and not the other. (The third copy, under
  `src/strata/templates/solution/dot.github/skills/`, is a scaffold template
  stamped into new user workspaces and is intentionally separate — leave that
  one alone.) Pick one canonical copy and have the other reference/symlink/
  generate from it. Also consider linking it from the main onboarding chain
  (README → `docs/INDEX.md` → `docs/platform/getting-started.md`), which
  currently doesn't mention it at all.

