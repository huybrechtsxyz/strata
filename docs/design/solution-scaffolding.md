# Solution Scaffolding — `strata sln init`/`strata sln update` — Design

- Status: **Fully implemented (2026-09-30)** — all 3 phases done.
  `strata sln init`/`strata sln update` are real, working CLI commands
  (`ScaffoldController`, `scaffold_templates.py`, the two ported
  templates + `.strata/README.md`, wired into `cli.py`), cross-linked
  from [audit-trail.md](audit-trail.md) and
  [v2-schema-overview.md](v2-schema-overview.md).
- Date: 2026-09-30
- Related: [docs/design/audit-trail.md](audit-trail.md) (Layer 1 — PR
  template + extraction — is this design's motivating, first real
  consumer), [ADR-0015](../decisions/0015-solution-manifest-and-document-discovery.md)
  (`strata.yaml` — v2's own, different bootstrap document from v1's
  `.strata/solution.json`), [docs/design/gap_fit_v1.md](gap_fit_v1.md) (ADR-0020's
  Tier 2 classification of `init`/etc. as "local interactive bootstrap
  only... does not block either consumer's redeploy path" — still true
  here, this is deliberately not a CI-critical-path feature)

## Problem

[audit-trail.md](audit-trail.md)'s real-usage scoping (2026-09-30) found
Layer 1 (PR template + issue template scaffolding) is real and active in
`config-deploy` today — `.github/pull_request_template.md` and
`.github/ISSUE_TEMPLATE/deployment-change-request.yml` both exist,
matching v1's design exactly. v2 has **no scaffolding command at all**
today — `src/strata/commands/` is 4 flat command files (`validate`/
`values`/`build`/`deploy`), no command group, no package-shipped template
tree, no "create/refresh the files strata itself owns in this repo"
concept.

v1's real mechanism for this (`strata sln init`/`strata sln update`,
`SolutionController` in `e:\SourcesXYZ\strata\src\strata\controllers\
solution_controller.py`) is a good fit to convert, per direct request —
read directly:

- **`init(name)`**: creates `.strata/`, `solution.json`, a
  `.code-workspace` file, then deep-copies the package's own
  `templates/solution/` directory tree into the workspace — every file
  copied idempotently (skipped if the destination already exists).
- **`update()`**: re-copies only files under a fixed set of **package-owned
  prefixes** (`README.md`, `.gitignore`, `.strata/integrations/`,
  `.strata/templates/`, `.devcontainer/`, `.github/`) — always
  overwriting them — while explicitly leaving a **user-owned** set
  (`.strata/cli.yaml`, `.strata/logging.yaml`, `.vscode/`, root
  `README.md`) untouched. Everything else scaffolded that isn't in either
  list is implicitly treated as user-owned (skipped).
- **Token substitution**: Jinja2, but with `DebugUndefined` (lenient) —
  a variable not in the substitution context is left visible in the
  output as literal `{{ var }}` rather than raising, since scaffold
  templates are rendered with only a small, fixed context
  (`SOLUTION_NAME`, `STRATA_VERSION`). `.j2` and `.md` files are copied
  **verbatim, never rendered** — `.j2` files are themselves raw Jinja2
  templates meant for a *later*, end-user-driven render pass (unrelated
  to this one); `.md` files are documentation/skill content whose own
  example code (which may contain literal `{{ }}`) must not be touched.
- **`dot.` path-segment convention**: every directory/file in the
  template source tree that should land as a real dotfile/dotdir in the
  target repo is named with a `dot.` prefix instead (`dot.github/` ->
  `.github/`, `dot.gitignore` -> `.gitignore`) and renamed at copy time.
  Not explicitly commented in v1's source on *why*, but the practical
  reason is real regardless of whether it was the original motivation:
  a template source tree containing literal `.github/`/`.vscode/`/
  `.devcontainer/` directories would be treated as *strata's own*
  GitHub/VS Code/devcontainer config by every tool that scans for those
  by name (editors, CI systems, `git status` conventions) during
  strata's own development — `dot.` prefixing sidesteps that ambiguity
  entirely, for the cost of one rename step at copy time.

## Proposed design

### Scope: mechanism is general, initial content is narrow

Port the **mechanism** (idempotent init, package-owned-refresh update,
lenient Jinja2 substitution, `dot.` renaming) in full — it's real,
non-trivial, and this is exactly why v1's version is a good conversion
candidate. Do **not** port v1's full `templates/solution/dot.github/`
tree (Copilot agent/skills/prompts/instructions, CI workflow) — that's a
separate, unrelated concern (AI-agent customization + CI bootstrapping),
not this design's motivating problem. Initial shipped content is the two
audit-trail Layer 1 files, plus one small package-owned file so `update`
has something real to do from day one rather than only a theoretical
rule with nothing to exercise it:

```
src/strata/templates/solution/
├── dot.strata/
│   └── README.md            # package-owned — always refreshed by `update`
└── dot.github/
    ├── pull_request_template.md          # user-owned — write-once
    └── ISSUE_TEMPLATE/
        └── deployment-change-request.yml  # user-owned — write-once
```

`.strata/README.md` documents, in the target repo itself, what `.strata/`
is for and that it's package-managed (mirrors v1's own self-documenting
scaffold convention) — genuinely useful content, not a placeholder
invented only to exercise the mechanism, and it gives `sln update` a
real, first-class file to refresh immediately, not just after some
future addition. More can be added later (CI workflow, `.vscode/`
scaffold, etc.) without redesigning the mechanism — each addition is
just another file under `templates/solution/`, classified by which
top-level directory it lands in.

### New CLI surface: `strata sln init`/`strata sln update`

A new Click group (`strata/commands/sln_command.py`), matching v1's
naming — first command group in v2 (every existing command is a flat
top-level command). Per ADR-0020's own Tier 2 classification (still
accurate: neither real consumer's CI ever calls `init`/`repo`/`profile`/
`config`), this is deliberately **not** on the CI-critical path — a
one-time bootstrap / occasional-refresh operation a human runs.

- `strata sln init <name>`: creates `strata.yaml` (v2's real bootstrap
  document, ADR-0015) **only if it doesn't already exist** — never
  overwrites an existing one, even to change `meta.name` (simpler than
  v1's own `init()`, which reloads and rewrites `meta.name` on a re-run;
  not `.strata/solution.json` + a `.code-workspace` file, both
  v1-specific and not part of v2's design at all) — then runs the same
  scaffold refresh `update` does.
- `strata sln update`: requires `strata.yaml` to already exist (error,
  not silently running init); refreshes package-owned files
  (`.strata/`), leaves user-owned files (`.github/`) alone if already
  present.

Resolves Open Question #3: `init` and `update` share one underlying
scaffold operation (see "New controller" below) — the only difference
is whether `strata.yaml` gets created first. Re-running `init` on an
already-initialised solution is therefore always safe and always a
no-op-or-refresh, never an error.

### Package-owned vs. user-owned split — resolved by directory, not a prefix list

**`.github/` is always user-owned; `.strata/` is always package-owned.**
Resolves Open Question #1: unlike v1 (which treats all of `.github/` as
package-owned, always overwritten), v2 flips this deliberately —
`.github/pull_request_template.md`/`ISSUE_TEMPLATE/deployment-change-request.yml`
are the kind of file a repo commonly tailors per-org, so `sln update`
must never silently destroy that customization. `.strata/` (v2's own
state/config area, same spirit as v1's `.strata/integrations/`/
`.strata/templates/`) is the opposite — strata's own opinion should
always win there, since nothing under it is meant to be hand-edited.

This is simpler than v1's fixed prefix list (`README.md`/`.gitignore`/
`.devcontainer/`/`.github/`/two `.strata/` subpaths, individually
enumerated) — a two-directory rule covers today's real content and every
foreseeable future addition without needing a new prefix added to a list
each time: `dest_relative_path()`'s own top-level path segment
(`.github` vs `.strata`, before renaming) decides ownership directly.

### Worked example — `init`, a user customization, then `update`

A new repo, `strata sln init acme-platform` run in an empty directory:

```
strata.yaml                                    # created (user-owned, ADR-0015)
.strata/
└── README.md                                  # created (package-owned)
.github/
├── pull_request_template.md                   # created (user-owned)
└── ISSUE_TEMPLATE/
    └── deployment-change-request.yml          # created (user-owned)
```

The team then customizes their PR template (adds an org-specific
compliance field) — a normal, expected edit:

```diff
 # .github/pull_request_template.md
 ## Risk level
+## SOC2 control reference
+<!-- Link the specific control this change satisfies -->
```

Months later, someone upgrades the installed `strata` package (which has
since improved its own `.strata/README.md` content) and runs
`strata sln update` to pick up the change. Outcome:

| File                                                   | Package-owned?                                                                          | Result                                                                                                                                    |
| ------------------------------------------------------ | --------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------- |
| `strata.yaml`                                          | No (never touched by `update` at all — only `init` ever writes it, and only if missing) | Untouched                                                                                                                                 |
| `.strata/README.md`                                    | Yes                                                                                     | **Overwritten** with the new package content — the customization-risk this file never has, since nothing in it is meant to be hand-edited |
| `.github/pull_request_template.md`                     | No                                                                                      | **Untouched** — the team's SOC2 field survives, exactly the case Open Question #1 exists to protect                                       |
| `.github/ISSUE_TEMPLATE/deployment-change-request.yml` | No                                                                                      | Untouched (already existed, never customized in this example, but the rule is the same either way)                                        |

This is the complete behavior matrix `update` needs to satisfy — every
file it touches falls into exactly one of these two rows, decided purely
by which top-level directory it's under.

### New controller: `strata/controllers/scaffold_controller.py`

`ScaffoldController.init(root: Path, name: str)`/`.update(root: Path)`,
mirroring `solution_controller.py`'s existing placement/shape (a
controller owns filesystem-level orchestration) but a clean split from
it — v2's `SolutionController` is purely discovery/loading, a different
responsibility than v1's monolithic one that also scaffolds. Keeping
scaffolding as its own controller avoids growing `SolutionController`
into a second concern.

Both public methods share one private `_scaffold(root)` step (per Open
Question #3's resolution): `init()` is `_ensure_manifest(root, name)`
(write `strata.yaml` only if missing) followed by `_scaffold(root)`;
`update()` is a manifest-existence check followed by the exact same
`_scaffold(root)` call. `_scaffold()` itself walks the template tree
once, and for each file: `.github/` (user-owned) is written only if the
destination doesn't already exist; `.strata/` (package-owned) is always
written.

### New utility: `strata/utils/scaffold_templates.py`

Deliberately **not** added to the existing `strata/utils/templater.py` —
that file is tightly scoped to `output.template` (build-time-validate +
deploy-time-strict-render, per its own docstring) and mixing a third,
lenient-undefined Jinja2 environment into the same file would blur which
environment applies where. New, narrowly-scoped file (same reasoning as
`path_conventions.py`'s recent precedent — a fresh utils file per
feature, pure logic, no models/integrations dependency):

```python
def render_scaffold(content: str, context: dict[str, str]) -> str:
    """Lenient Jinja2 render — an undeclared variable stays literal
    ('{{ var }}') rather than raising. Scaffold templates only ever
    supply a small, fixed context (SOLUTION_NAME, STRATA_VERSION)."""

def dest_relative_path(template_relative_path: str) -> str:
    """Rename every 'dot.'-prefixed path segment to '.' (dot.github/ ->
    .github/, dot.gitignore -> .gitignore)."""

def is_package_owned(dest_relative_path: str) -> bool:
    """True when the *renamed* destination path's top-level segment is
    '.strata' (always refreshed by `sln update`); False for '.github'
    (or anything else — never refreshed, `init`-only)."""
```

### Packaging: `pyproject.toml`

`[tool.setuptools.package-data]` currently ships only `py.typed` — needs
`"strata" = ["py.typed", "templates/**/*"]` so a real installed wheel
(not just an editable/source checkout) actually contains the template
tree. Untested today because nothing ships non-`.py` package data yet.

## Deliberately out of scope

- **v1's full `dot.github/` tree** (Copilot agent/skills/prompts/
  instructions, `workflows/deploy.yml`) — unrelated to audit-trail Layer
  1; a separate future decision if v2 wants to scaffold AI-agent
  customization or CI workflows too.
- **`.devcontainer/`, `.vscode/` scaffolding** — not evidenced as needed
  by this design's motivating problem.
- **JSON Schema generation/export** (`_generate_schemas()`, v1's
  `.strata/schemas/*.json`) — v2 has no equivalent mechanism or consumer
  for it today; a separate feature if ever needed.
- **Every other `strata sln *` subcommand** v1 has
  (`add_deployment`/`remove_deployment`/`list_deployments`/
  `scan_deployments`/`export_template`/`doctor`) — none relate to
  scaffolding; out of scope for this design entirely.
- **v1's `.strata/solution.json` + `.code-workspace` file** — v1-specific
  bootstrap artifacts with no v2 equivalent (ADR-0015 already gives v2 a
  different, simpler bootstrap: `strata.yaml` alone, found by walking up).

## Open questions for review

1. **Should the two scaffolded files be package-owned (always
   overwritten on `sln update`) or user-owned (written once, never
   touched again)?** **Decided (2026-09-30): user-owned — `.github/` is
   never overwritten by `update`.** v1 treats all of `.github/` as
   package-owned, a real, evidenced behavior, but one with a real
   consequence: a repo that has since customized its own
   `pull_request_template.md` (very plausible — PR templates are
   commonly tailored per-org) would have that customization **silently
   destroyed** the next time someone runs `strata sln update`. Resolved
   as a directory-level rule, not a per-file decision: `.github/` is
   always user-owned; `.strata/` is always package-owned (see
   "Package-owned vs. user-owned split" above) — `.strata/README.md`
   ships as the first real package-owned file, so `update` has
   something concrete to refresh from day one (see "Worked example").
2. **Command naming: keep `sln init`/`sln update`, or a v2-native name?**
   No competing convention exists in v2 yet (first command group) — v1's
   naming is a reasonable, recognizable default; revisit only if a
   naming convention emerges from a later command group.
3. **Should `init` be idempotent/safe to re-run on an existing
   `strata.yaml`**, **or should re-running it be an error**?
   **Decided (2026-09-30): idempotent, resolved by the same directory
   rule as Open Question #1** — `strata.yaml` itself is user-owned (never
   overwritten; re-running `init` on an existing solution leaves its
   `meta.name`/identity untouched, simpler than v1's own `init()`, which
   reloads and rewrites `meta.name`), while `.strata/` is package-owned
   and always (re-)written. This means `init`'s scaffold step and
   `update()` are now the *same* operation — see "New controller" below
   — `init` is just "create `strata.yaml` if missing, then run the same
   refresh `update()` does."

## Implementation Plan

### Phase 1 — Mechanism — ~~IMPLEMENTED (2026-09-30)~~

- `strata/utils/scaffold_templates.py`: `render_scaffold()`,
  `dest_relative_path()`, `is_package_owned()`.
- `strata/controllers/scaffold_controller.py`: `ScaffoldController.init()`/
  `.update()`, both calling one shared `_scaffold(root)` step (see "New
  controller" above); `_scaffold()` itself calls `is_package_owned()`
  per file — `.github/` written only if missing, `.strata/` always
  (re-)written.
- `src/strata/templates/solution/dot.strata/README.md` +
  `dot.github/pull_request_template.md` +
  `dot.github/ISSUE_TEMPLATE/deployment-change-request.yml` — the first
  ported from the real v1 templates (already confirmed real/active in
  `config-deploy`), with any strata-v1-specific wording (`strata
  deploy run -f <file>` still applies to v2 unchanged; `strata validate
  run` -> v2's real `strata validate`) adjusted to v2's actual CLI
  surface; `.strata/README.md` new content, explaining what `.strata/`
  is for in this repo and that it's package-managed.
- `pyproject.toml`: `package-data` glob added.
- Tests: exactly the "Worked example" above, as an integration test —
  `init`, hand-edit `.github/pull_request_template.md`, `update`, assert
  `.strata/README.md` changed and the hand-edit survived untouched.
  Plus: re-running `init` on an already-initialised solution is a no-op
  on `strata.yaml` (identity/name untouched) but still refreshes
  `.strata/README.md`; `dot.` renaming; lenient-undefined rendering
  leaves an unknown token literal.

### Phase 2 — CLI wiring — ~~IMPLEMENTED (2026-09-30)~~

- `strata/commands/sln_command.py`: Click group `sln`, subcommands
  `init NAME`/`update`; wired into `cli.py`.
- Tests: CLI-level invocation (via Click's test runner, matching this
  repo's existing command test conventions), `update` before `init`
  errors cleanly.

### Phase 3 — Documentation — ~~IMPLEMENTED (2026-09-30)~~

- [audit-trail.md](audit-trail.md): mark Layer 1 no longer just
  "capability catalog" — note it's now real in v2, cross-reference this
  design.
- [docs/design/v2-schema-overview.md](v2-schema-overview.md): no kind
  changes here (no new document kind), but its own command-surface
  framing may be worth a one-line mention once this ships.
- Full check suite (mypy/ruff/import-linter/pytest).

## Changelog

- 2026-09-30: Created, per request ("lets then first look at strata
  sln(solution) init command... lets start there"), as the implementation
  vehicle for [audit-trail.md](audit-trail.md)'s Layer 1 finding (PR
  template scaffolding is real and active in `config-deploy`, and
  the one piece with no v2 equivalent at all — no command surface exists
  yet). Grounded directly in v1's real, current source
  (`e:\SourcesXYZ\strata\src\strata\controllers\solution_controller.py`,
  `utils\templater.py`, `templates\solution\dot.github\`). Scoped the
  mechanism (idempotent init, package-owned-refresh update, lenient
  Jinja2, `dot.` renaming) as a full port, but the initial shipped
  content to just the two audit-trail-relevant files — v1's much larger
  `.github/` tree (Copilot agent/skills/prompts, CI workflow) is a
  separate, unrelated concern deliberately left out. Design only, nothing
  implemented yet.
- 2026-09-30: Resolved Open Question #1, per direct request ("we do not
  overwrite files in the .github folder, we should be able to overwrite
  files in the .strata folder?") — `.github/` is always user-owned
  (never touched by `update`), `.strata/` is always package-owned
  (always refreshed). Replaced v1's fixed per-prefix ownership list with
  a simpler, general directory-level rule (`is_package_owned()` checks
  only the renamed destination's top-level segment) that needs no update
  when new content is added under either directory later. Design only,
  nothing implemented yet.
- 2026-09-30: Resolved Open Question #3, per direct request ("YES. by
  very simple not overwriting strata.yaml but you can overwrite the
  .strata/ folder?") — `init` is idempotent via the same directory rule:
  `strata.yaml` is user-owned (never overwritten on a re-run, no
  `meta.name` rewrite like v1's own `init()` does), `.strata/` is
  package-owned (always refreshed). This collapses `init`'s scaffold
  step and `update()` into one shared `_scaffold()` operation — the only
  difference between the two commands is whether `strata.yaml` gets
  created first. Design only, nothing implemented yet.
- 2026-09-30: Made `update` concrete and complete, per request ("make
  sure update command is clear... should update perhaps some files? lets
  make certain the design is complete, with a small example"). Added a
  real package-owned file, `.strata/README.md` (documents `.strata/` in
  the target repo, package-managed) — before this, nothing shipped under
  `.strata/` at all, so `update` was a correct but entirely theoretical
  no-op with nothing to actually exercise it. Added a full "Worked
  example" section: `init` on an empty repo, a realistic user
  customization to the PR template, then `update` after a package
  upgrade — a table showing exactly which of the 4 files change and
  which don't, covering every case the ownership rule needs to handle.
  Updated Phase 1's plan to ship the new file and test the worked
  example directly as an integration test. Design only, nothing
  implemented yet.
- 2026-09-30: **Implemented Phase 1**, per request ("design, plan, and
  implement phase 1"). The design/plan were already complete from the
  prior passes, so this was pure implementation, zero deviations:
  `src/strata/templates/solution/dot.strata/README.md` +
  `dot.github/pull_request_template.md` +
  `dot.github/ISSUE_TEMPLATE/deployment-change-request.yml` (ported from
  v1's real, canonical package templates
  `e:\SourcesXYZ\strata\src\strata\templates\solution\dot.github\`, not
  `config-deploy`'s own copy — CLI wording adjusted to v2's actual
  surface: `strata deploy run <deployment-name>`/`strata validate`, no
  `-f`/`run`/`--deep`, and no `deploy health` reference since v2 doesn't
  have that command); `strata/utils/scaffold_templates.py`
  (`render_scaffold()`/`dest_relative_path()`/`is_package_owned()`);
  `strata/controllers/scaffold_controller.py` (`ScaffoldController.init()`/
  `.update()`, sharing one `_scaffold()` step exactly as designed);
  `pyproject.toml`'s `package-data` glob. Hit one real, pre-existing repo
  convention this design hadn't anticipated:
  `test_utils_layout.py`'s guard rejects any hardcoded `".strata"` string
  literal outside `layout.py` — fixed by importing the already-existing
  `STRATA_DIR` constant instead of retyping the literal, no design change
  needed. 17 new tests (11 utility, 6 controller — the controller tests
  include the design's own "Worked example" as a direct integration
  test, verifying all 4 files' exact fates in one pass). Full check suite
  green: mypy (111 files), ruff, import-linter (1 kept, 0 broken), pytest
  (1379 passed — same pre-existing, unrelated `config/` example-solution
  drift as the sole failure). `strata validate .v2-cfg` re-confirmed
  clean (12/12), unaffected. Phase 2 (CLI wiring: `strata sln init`/
  `update`) and Phase 3 (documentation) remain — nothing built here is
  reachable from the command line yet.
- 2026-09-30: **Implemented Phase 2**, per request ("design, plan, and
  implement phase 2"). Zero deviations from the already-written plan:
  new `strata/commands/sln_command.py` (first command group in v2) —
  `init` resolves its target directory directly (`resolve_work_path()`,
  never walks up, since it may be creating `strata.yaml` for the first
  time); `update` walks up (`find_solution_root()`), matching every other
  command's own convention, and raises `UsageError` (exit code 2) when no
  solution is found. Both wrap `ScaffoldController`'s message list into a
  small `Diagnostics` (`.info()` per message) before calling
  `run.report()` — the same two-part step-then-summary shape every other
  command already produces, rather than inventing a second reporting
  style for this one. Wired into `cli.py`. 6 new CLI-level tests (Click's
  own test runner, matching every other command test file's convention),
  plus a manual smoke test against the real installed `strata.exe`
  (confirmed the exact "Worked example" behavior end to end: fresh
  `init` reports 4 created/updated files; a re-run after hand-editing
  `.github/pull_request_template.md` reports only `.strata/README.md`
  refreshed, customization intact). Full check suite green: mypy (112
  files), ruff, import-linter (1 kept, 0 broken), pytest (1385 passed —
  same pre-existing, unrelated `config/` example-solution drift as the
  sole failure). `strata sln init`/`strata sln update` are now real,
  working commands. Phase 3 (documentation) remains.
- 2026-09-30: **Implemented Phase 3**, per request ("design, plan, and
  implement phase 3"). Documentation only, no code changes: added a
  callout to [audit-trail.md](audit-trail.md)'s Layer 1 section noting
  the PR/issue-template scaffolding half is no longer just a v1
  capability catalog entry — it's real and tested in v2 now, with a
  cross-link here; added a new `strata sln init`/`update` row to
  [v2-schema-overview.md](v2-schema-overview.md)'s command-surface table
  (`Implemented — first command group in v2; not on the CI-critical path
  (ADR-0020 Tier 2)`), plus a changelog entry there. This design is now
  fully implemented, all 3 phases done.
- 2026-10-01: Added `.vscode/` scaffolding, reversing this doc's own
  "Deliberately out of scope" call ("not evidenced as needed by this
  design's motivating problem") now that evidence exists: the new
  `src/vscode` VS Code extension. Scoped narrowly to that one motivation
  — `dot.vscode/extensions.json` recommends the extension
  (`huybrechts-xyz.strata`) plus a `README.md` explaining the directory,
  nothing else. Deliberately did **not** port v1's
  `dot.vscode/settings.json`/`tasks.json`/`launch.json`/`mcp.json` —
  read directly, v1's `settings.json` turned out to be a copy of v1's
  own *dev-repo* editor settings (ruff/pytest/python formatter config)
  rather than content meant for an end-user solution repo, and its
  `extensions.json` never actually recommended v1's own Strata
  extension at all. Classified as user-owned under the existing
  directory-level rule (`is_package_owned()` already returns `False` for
  anything outside `.strata/` — no code change needed, confirmed with a
  new `test_vscode_is_user_owned_like_github` test). `pyproject.toml`'s
  `templates/**/*` package-data glob already covers the new subfolder.
  2 new controller/CLI test assertions plus the one dedicated ownership
  test; full check suite green (mypy, ruff, import-linter, the 13
  scaffold/sln tests). Confirmed live against `strata sln init` in a
  scratch directory.


