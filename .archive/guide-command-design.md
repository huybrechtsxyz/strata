# Design Spec: `strata guide`

_Date: 2026-06-10_
_Author: Danny (Lead Architect)_
_Requested by: Vincent Huybrechts_
_Scope: v1 — read-only advisory, no skip/won't-do logic_

---

## 1. CLI Surface

### Command signature

```
strata guide [OPTIONS]
```

`guide` is a top-level command (registered directly on `main`, not under any group).

### Options

| Flag          | Type                                | Default  | Description                           |
| ------------- | ----------------------------------- | -------- | ------------------------------------- |
| `--work-path` | PATH                                | CWD walk | Root of the strata workspace.         |
| `--output`    | Choice[console, text, json, ndjson] | console  | Output format.                        |
| `--verbose`   | flag                                | False    | Emit structured log lines to console. |
| `--quiet`     | flag                                | False    | Suppress all output.                  |

Decorators applied (in order, bottom-up on the Click function):
1. `@click_work_path`
2. `@click_output_format`
3. `@click_output_verbose`
4. `@click_output_quiet`

These are all standard `cli_common.py` decorators — no new decorators needed.

### What is NOT a flag

- **No `--profile`** — guide always reads the active profile from `solution.json`. The guide's purpose is to tell users what the workspace looks like right now, not a hypothetical.
- **No `--fix`** — v1 is advisory only.
- **No `--phase`** — no filtering; show the full checklist always.

### Exit codes

| Code | Condition                                         |
| ---- | ------------------------------------------------- |
| `0`  | Always — guide is advisory, never a pipeline gate |

`INIT_REQUIRED = False` — the command degrades gracefully when `.strata/solution.json` is absent (shows Phase 1 as ⬜ and stops).

---

## 2. Checklist Items

### Phase table

| #   | Label                      | Data source                                                                                                    | ✅ Done                              | ⚠️ Partial / Attention                          | ⬜ Not started                    |
| --- | -------------------------- | -------------------------------------------------------------------------------------------------------------- | ----------------------------------- | ---------------------------------------------- | -------------------------------- |
| 1   | Workspace initialized      | `Path(work_path / ".strata" / "solution.json")` exists + parses to `SolutionModel` with non-empty `meta.name`  | File exists, parses, name non-empty | File exists but unparseable (caught exception) | `.strata/solution.json` absent   |
| 2   | Repositories registered    | `SolutionModel.spec.repositories` (list length)                                                                | length > 0                          | —                                              | length == 0 or field is None     |
| 3   | Repositories on disk       | For each repo in `spec.repositories`, check `Path(repo.path).exists()`                                         | all paths exist                     | some paths missing (show count + names)        | phase 2 is ⬜ (no repos)          |
| 4   | Profile created            | `SolutionModel.spec.profiles` list length                                                                      | length > 0                          | —                                              | length == 0 or field is None     |
| 5   | Profile activated          | Any profile in `spec.profiles` has `active == True`                                                            | exactly one active                  | —                                              | none active                      |
| 6   | File references registered | Sum of `configfile_paths`, `envfile_paths`, `secretfile_paths`, `datafile_paths` lengths on the active profile | total > 0                           | total == 0 (show which types are empty)        | phase 5 is ⬜ (no active profile) |
| 7   | Build artifact exists      | `Path(work_path / "build")` exists AND contains at least one file (any depth)                                  | dir exists + has files              | dir exists but empty                           | dir absent                       |

### Phase dependency and rendering rules

- Phases are always rendered in order 1 → 7.
- A phase whose prerequisite is ⬜ is also rendered as ⬜ and its label is unchanged. The prerequisite dependency is implied by order, not annotated explicitly in v1.
- A phase whose prerequisite is ⚠️ is still evaluated independently. Example: repos 2/3 cloned (⚠️) does not prevent evaluating phases 4+.
- No ❌ marker in v1. Errors (e.g. `solution.json` unparseable) are rendered as ⚠️ with a detail note.

### Phase 3 detail string

```
2/3 cloned — {comma-separated list of repo names not found on disk}
```

### Phase 6 detail string

```
{N} registered on active profile '{profile_name}' ({types list})
```

Where `{types list}` is: `config: N, env: N, secret: N, data: N` — only include non-zero types.
When total is 0: `0 registered on active profile '{profile_name}'`

---

## 3. "Next Step" Selection Logic

The "next step" block shows the **first phase from the top that is not ✅**.

⚠️ phases are included in the search — they count as "not fully done". This means if repos are partially cloned (⚠️), the next step will be about cloning the missing repos, even if phases 4–6 are ✅.

If all 7 phases are ✅, emit a completion message instead.

### Next step hint table

| First non-✅ phase             | Console hint                                                                                                            |
| ----------------------------- | ----------------------------------------------------------------------------------------------------------------------- |
| 1 — Workspace not initialized | `strata sln init <name>` · See: `strata help --topic quickstart`                                                        |
| 2 — No repos registered       | `strata repo add <name> <url>` · See: `strata help --topic repos`                                                       |
| 3 — Repos not all on disk     | `git clone <url> <path>` for each missing repo · See: `strata help --topic repos`                                       |
| 4 — No profiles               | `strata profile add <name> --activate` · See: `strata help --topic profiles`                                            |
| 5 — No active profile         | `strata profile activate <name>` · See: `strata help --topic profiles`                                                  |
| 6 — No refs registered        | `strata ref config add <name> @<repo>/path/to/config.yaml --profile <active>` · See: `strata help --topic environments` |
| 7 — No build artifact         | `strata build run` · See: `strata help --topic build`                                                                   |
| All ✅                         | `All setup phases complete. Your workspace is ready to deploy.`                                                         |

For phase 3, the hint must include the missing repo name(s) and their registered URLs so the user can copy-paste the `git clone` command. Emit one line per missing repo:

```
git clone <repo.url> <repo.path>
```

---

## 4. Output Format

### Console output

```
Workspace: my-platform  (e:\src\my-config-repo)

Setup progress:

  ✅ Workspace initialized
  ✅ Repositories registered (3)
  ⚠️  Repositories on disk (2/3 cloned — xyz-svc-traefik not found)
  ✅ Profile created (prd, stg, dev)
  ✅ Profile activated (prd)
  ⚠️  File references (0 registered on active profile 'prd')
  ⬜ Build artifact

→ Next step: Register config files with your active profile:

   strata ref config add <name> @<repo>/path/to/config.yaml --profile prd

   See: strata help --topic environments
```

Rules:
- Header line: `Workspace: {name}  ({work_path})`. If workspace is not initialized, `Workspace: (uninitialized)  ({work_path})`.
- Each checklist item indented two spaces.
- `⚠️` followed by two spaces (the emoji is double-width; normalise the column).
- `⬜` followed by one space.
- `✅` followed by one space.
- Blank line before and after the checklist block.
- Next step block separated by a blank line, prefixed with `→ `.
- The hint command(s) indented three spaces.
- `See:` line indented three spaces.

When `--output console` and workspace is uninitialized, omit all phases below phase 1 (they are all ⬜ by definition, and listing them would be noise). Show only phase 1 as ⬜ and the next-step hint for init.

### JSON output shape

```json
{
  "workspace": {
    "name": "my-platform | null",
    "path": "e:\\src\\my-config-repo",
    "solution_id": "uuid | null"
  },
  "checklist": [
    {
      "phase": 1,
      "label": "Workspace initialized",
      "status": "ok | warn | pending",
      "detail": "string | null"
    }
  ],
  "next_step": {
    "phase": 6,
    "label": "File references registered",
    "hint": "strata ref config add <name> @<repo>/path/to/config.yaml --profile prd",
    "see_also": "strata help --topic environments"
  },
  "complete": false
}
```

Status values:
- `"ok"` → ✅
- `"warn"` → ⚠️
- `"pending"` → ⬜

`next_step` is `null` when `complete == true`.

`detail` is `null` when there is nothing extra to say about a phase (e.g. a plain ✅ or plain ⬜).

---

## 5. Code Structure

### New files

```
src/strata/commands/cli_guide.py                    ← Click wiring
src/strata/commands/guide/__init__.py               ← empty
src/strata/commands/guide/show_guide_command.py     ← GuideCommand class
```

### `cli_guide.py`

Mirrors `cli_status.py` exactly in structure:

```python
@click.command(name="guide")
@click_work_path
@click_output_format
@click_output_verbose
@click_output_quiet
def guide_command(work_path, output, verbose, quiet):
    """Show setup progress and suggest the next action for this workspace."""
    command = GuideCommand(work_path=work_path, output=output, verbose=verbose, quiet=quiet)
    success = command.execute()
    handle_command_exit(command, success)
```

### `show_guide_command.py` — `GuideCommand`

```python
class GuideCommand(BaseCommand):
    OPERATION = "guide"
    INIT_REQUIRED = False
```

Key private methods (all return values, no side effects):

| Method                | Signature                                                    | Purpose                                                                                 |
| --------------------- | ------------------------------------------------------------ | --------------------------------------------------------------------------------------- |
| `_load_solution`      | `() → Optional[SolutionModel]`                               | Calls `SolutionService.load_from_json(path)`. Returns `None` on any failure — no raise. |
| `_evaluate_checklist` | `(solution: Optional[SolutionModel]) → List[ChecklistItem]`  | Runs all 7 phase checks. Returns ordered list.                                          |
| `_find_next_step`     | `(checklist: List[ChecklistItem]) → Optional[NextStepItem]`  | Returns hint for first non-ok phase.                                                    |
| `_render_console`     | `(checklist, next_step, workspace_name, work_path) → None`   | Emits all `click.echo()` calls.                                                         |
| `_render_json`        | `(checklist, next_step, workspace_name, solution_id) → dict` | Returns the JSON-serialisable dict stored in `self._output_data`.                       |

#### `ChecklistItem` dataclass (defined in the same module)

```python
@dataclass
class ChecklistItem:
    phase: int
    label: str
    status: Literal["ok", "warn", "pending"]
    detail: Optional[str] = None
```

#### `NextStepItem` dataclass (defined in the same module)

```python
@dataclass
class NextStepItem:
    phase: int
    label: str
    hint: str         # may be multi-line for phase 3 (one git clone per missing repo)
    see_also: str
```

### `execute()` lifecycle

Follows the same pattern as `StatusCommand`:
1. `_initialize()` — BaseCommand, loads SolutionController
2. `_before_execute()` — no-op for guide (no integrations, no config merge needed)
3. `_run_execution()` — loads solution, evaluates checklist, renders output
4. `_after_execute()` — no-op
5. `_finalize(success=True)` — always `True` for guide

`_run_execution()` catches all exceptions and falls through to a graceful console message — guide must never crash the user's terminal session.

---

## 6. Touchpoints

| #   | File                                              | Change type   | Notes                                                                                                                                        |
| --- | ------------------------------------------------- | ------------- | -------------------------------------------------------------------------------------------------------------------------------------------- |
| 1   | `src/strata/commands/cli_guide.py`                | **NEW**       | Click wiring, 30 lines                                                                                                                       |
| 2   | `src/strata/commands/guide/__init__.py`           | **NEW**       | Empty package marker                                                                                                                         |
| 3   | `src/strata/commands/guide/show_guide_command.py` | **NEW**       | GuideCommand class, ~200 lines                                                                                                               |
| 4   | `src/strata/cli.py`                               | **MODIFY**    | Import `guide_command`; `main.add_command(guide_command, name="guide")`; add `"guide"` to `_HELP_SECTIONS` under `"Inspection & Validation"` |
| 5   | `src/strata/services/solution_service.py`         | **READ ONLY** | `load_from_json(path)` already exists — no changes                                                                                           |
| 6   | `src/strata/models/solution_model.py`             | **READ ONLY** | All fields already modelled — no changes                                                                                                     |

Total: 3 new files, 1 modified file. Zero new models, zero new services.

---

## 7. Architecture Decisions

**AD-GUIDE-1 — `guide` is a top-level command, not under `sln`.**

`sln` owns workspace lifecycle operations: `init`, `clean`, `status`, `export`, `update`. `guide` is a UX navigation aid — it is applicable both before and after init. Placing it under `sln` would bury it behind a namespace unfamiliar to first-time users who don't yet know the `sln` group exists. `strata guide` is the entry point for onboarding; it must be reachable with no prior knowledge.

**AD-GUIDE-2 — `INIT_REQUIRED = False`.**

The guide's most important use case is "I just cloned this repo — what do I do?" This is the uninitialized state. Requiring init before running guide would make the command circular: you need guide to learn how to init, but init is required to run guide. Setting `INIT_REQUIRED = False` mirrors `StatusCommand` and `HelpCommand`.

**AD-GUIDE-3 — Uses `SolutionService.load_from_json()`, never raw `json.load()`.**

Architecture rule: no service bypasses the service layer to read its own backing file directly. `SolutionService.load_from_json()` handles JSON parsing, Pydantic validation, and error wrapping. If the JSON is malformed, `load_from_json` raises a `ServiceLoadError` which `_load_solution()` catches and converts to a ⚠️ on phase 1.

**AD-GUIDE-4 — Exit code is always 0.**

`guide` is advisory. It is not a gating check — it does not validate configuration files. A CI pipeline that calls `strata guide` should never fail because of it. If we later want a machine-readable gate, that belongs in `strata validate`, not `guide`.

**AD-GUIDE-5 — No `--profile` flag.**

Guide always reads the active profile. This is a deliberate constraint: guide is for understanding the current workspace state as-is. Allowing `--profile` would create a "phantom state" view that doesn't reflect what deploy commands will actually use, undermining the command's purpose. Users who want to inspect a specific profile use `strata profile list` and `strata ref list`.

**AD-GUIDE-6 — Phase 2 (tools check) deferred to v2.**

Tool availability requires subprocess invocations (`git --version`, `terraform --version`, etc.), which this v1 explicitly excludes. The `IntegrationController` already provides this capability and it will be wired in v2. Deferring keeps v1 fast, pure read-only, and testable without mocking subprocess.

**AD-GUIDE-7 — Phase 9 (deploy history) deferred to v2.**

There is no clean read-only artifact for deploy history yet. Deploy state (last run, last success) is not captured in `solution.json` or any current output file. Adding this phase in v1 would require either querying external systems (violating read-only) or adding new state models (out of scope). Deferred to align with the deploy state tracking work.

**AD-GUIDE-8 — ⚠️ (partial) is not ❌ (failure).**

In v1 there is no concept of a blocking error in the guide. A repo that isn't cloned isn't a failure — the user may have just registered it and hasn't cloned it yet. A profile with no refs may be intentionally empty at this stage. Advisory-only output requires that the tool never condemn the user's state as wrong. ❌ is reserved for v2 once we understand which states are genuinely invalid.

**AD-GUIDE-9 — `ChecklistItem` and `NextStepItem` are module-local dataclasses.**

These are not used anywhere outside `show_guide_command.py`. Extracting them to a shared models file would be premature abstraction — there is exactly one consumer. They live in the same file as `GuideCommand` and are package-private.

**AD-GUIDE-10 — Console rendering is single-pass, no buffering.**

The console output is emitted directly via `click.echo()` in a single top-to-bottom pass. There is no intermediate string buffer or template. This matches the existing rendering pattern in `StatusCommand` and avoids introducing a template dependency for a feature this small.
