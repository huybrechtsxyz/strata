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

| Flag          | Type                                | Default  | Description                                                                                                                     |
| ------------- | ----------------------------------- | -------- | ------------------------------------------------------------------------------------------------------------------------------- |
| `--work-path` | PATH                                | CWD walk | Root of the strata workspace.                                                                                                   |
| `--file / -f` | PATH                                | None     | Inspect a specific YAML file and show kind-specific guidance. Switches to file inspection mode. Supports `STRATA_FILE` env var. |
| `--output`    | Choice[console, text, json, ndjson] | console  | Output format.                                                                                                                  |
| `--verbose`   | flag                                | False    | Emit structured log lines to console.                                                                                           |
| `--quiet`     | flag                                | False    | Suppress all output.                                                                                                            |

Decorators applied (in order, bottom-up on the Click function):
1. `@click_file`
2. `@click_work_path`
3. `@click_output_format`
4. `@click_output_verbose`
5. `@click_output_quiet`

`@click_file` is the existing `cli_common.py` decorator (shared with `validate`). No new decorator needed.

### What is NOT a flag

- **No `--profile`** — guide always reads the active profile from `solution.json`. The guide's purpose is to tell users what the workspace looks like right now, not a hypothetical.
- **No `--fix`** — v1 is advisory only.
- **No `--phase`** — no filtering; show the full checklist always.

### Mode switching

`--file` and the workspace checklist are **mutually exclusive modes**. When `--file` is provided, the workspace setup checklist is suppressed entirely and replaced by file inspection output. Workspace context (name, path) is still shown in the header if the solution is loadable.

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

## 2b. Per-Phase Evaluation Steps

This section defines the exact algorithm `_evaluate_checklist(solution)` runs for each phase. Phases are evaluated in order 1 → 7. Inputs available throughout: `solution: Optional[SolutionModel]` and `work_path: Path`.

**Detail** is only appended to the checklist line when non-None. Format: `{marker} {label} ({detail})`.

**Blocking rule**: a phase listed as "blocked by phase N" short-circuits to `⬜` with no detail when phase N's status is `⬜`. A phase is never blocked by `⚠️`.

---

### Phase 1 — Workspace initialized

_No prerequisite._

| Step | Condition                                                  | Result                                                                                           |
| ---- | ---------------------------------------------------------- | ------------------------------------------------------------------------------------------------ |
| 1    | `(work_path / ".strata" / "solution.json")` does not exist | `⬜`, detail: `None` — `solution_exists = False`                                                  |
| 2    | File exists but `SolutionService` raises on load           | `⚠️`, detail: `"solution.json could not be parsed"` — `solution_exists = True`, `solution = None` |
| 3    | Loaded, `meta.name` is empty                               | `⚠️`, detail: `"workspace name is empty"`                                                         |
| 4    | Loaded, name non-empty                                     | `✅`, detail: `None`                                                                              |

`_load_solution()` is only called when the file exists (step 2+). Step 1 is a plain `Path.exists()` check before any service call. This is the only way to distinguish `⬜` (file absent) from `⚠️` (file broken).

_Workspace name is shown in the header line, not repeated in the checklist._

---

### Phase 2 — Repositories registered

_Blocked when `solution is None` (covers both phase 1 ⬜ and phase 1 ⚠️)._

| Step | Condition                                     | Result                                      |
| ---- | --------------------------------------------- | ------------------------------------------- |
| 1    | `solution is None`                            | `⬜`, detail: `None`                         |
| 2    | `solution.spec.repositories` is empty or None | `⬜`, detail: `None`                         |
| 3    | repositories present                          | `✅`, detail: `str(len(repos))` — e.g. `"3"` |

---

### Phase 3 — Repositories on disk

_Blocked by phase 2._

| Step | Condition                      | Result                                                                         |
| ---- | ------------------------------ | ------------------------------------------------------------------------------ |
| 1    | phase 2 is `⬜`                 | `⬜`, detail: `None`                                                            |
| 2    | All `Path(repo.path).exists()` | `✅`, detail: `None`                                                            |
| 3    | Some paths missing             | `⚠️`, detail: `"{found}/{total} cloned — {', '.join(missing names)} not found"` |

For the next-step hint (phase 3), emit one line per missing repo. If the repo has a URL registered, emit `git clone <repo.url> <repo.path>`. If the repo is a local type (no URL), emit `# local repo not found: <repo.path>` — no clone command is possible.

---

### Phase 4 — Profile created

_Blocked when `solution is None`._

| Step | Condition                                 | Result                                                                      |
| ---- | ----------------------------------------- | --------------------------------------------------------------------------- |
| 1    | `solution is None`                        | `⬜`, detail: `None`                                                         |
| 2    | `solution.spec.profiles` is empty or None | `⬜`, detail: `None`                                                         |
| 3    | Profiles present                          | `✅`, detail: `", ".join(p.name for p in profiles)` — e.g. `"prd, stg, dev"` |

_Phase 4 is independent of phase 3. Partial repo cloning does not block profile evaluation._

---

### Phase 5 — Profile activated

_Blocked by phase 4 ⬜._

| Step | Condition                       | Result                                              |
| ---- | ------------------------------- | --------------------------------------------------- |
| 1    | phase 4 is `⬜`                  | `⬜`, detail: `None`                                 |
| 2    | No profile has `active == True` | `⬜`, detail: `None`                                 |
| 3    | One active profile found        | `✅`, detail: active profile's `name` — e.g. `"prd"` |

---

### Phase 6 — File references registered

_Blocked by phase 5 ⬜. Phase 5 ⚠️ cannot occur (phase 5 is always ✅ or ⬜), so the "only ⬜ blocks" rule is implicit here._

| Step | Condition                             | Result                                                                                  |
| ---- | ------------------------------------- | --------------------------------------------------------------------------------------- |
| 1    | phase 5 is `⬜`                        | `⬜`, detail: `None`                                                                     |
| 2    | Active profile's total ref count == 0 | `⚠️`, detail: `"0 registered on active profile '{name}'"`                                |
| 3    | Total ref count > 0                   | `✅`, detail: `"{total} registered on active profile '{name}' ({non-zero type counts})"` |

_Type counts format: `"config: 2, env: 1, secret: 1"` — only include types where count > 0._

For the next-step hint (phase 6), substitute the real active profile name into `--profile <active>`.

---

### Phase 7 — Build artifact exists

_No prerequisite._

| Step | Condition                                          | Result                              |
| ---- | -------------------------------------------------- | ----------------------------------- |
| 1    | `(work_path / "build")` does not exist             | `⬜`, detail: `None`                 |
| 2    | Directory exists but contains no files (any depth) | `⚠️`, detail: `"directory is empty"` |
| 3    | Directory contains at least one file               | `✅`, detail: `None`                 |

_Phase 7 is always evaluated regardless of earlier phases — it only depends on the filesystem._

---

### Dependency map

```
                          solution_exists? solution loaded?
  ├─ Phase 1 (no prereq)              └─────────────────────────────┐
  ├─ Phase 2 ← blocked when solution is None (phase 1 ⬜ OR ⚠️)
  │    └─ Phase 3 ← blocked by phase 2 ⬜
  ├─ Phase 4 ← blocked when solution is None
  │    └─ Phase 5 ← blocked by phase 4 ⬜
  │         └─ Phase 6 ← blocked by phase 5 ⬜
  └─ Phase 7 (no prereq)
```

The "⚠️ never blocks" rule holds for phases 3–7 (partial state is not a blocker). The exception is phase 1 ⚠️ (parse error), which leaves `solution = None` and therefore transitively blocks phases 2–6 via the `solution is None` guard, even though phase 1's status is ⚠️ not ⬜.

---

## 2c. File Inspection Mode (`--file`)

When `--file <path>` is provided, `_run_execution()` calls `_evaluate_file_checklist()` instead of `_evaluate_checklist()`. The workspace setup checklist is not shown.

### File path resolution

| Path format            | Resolution                                                                                                                                                                                        |
| ---------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Relative path          | Resolved from CWD (not `work_path`)                                                                                                                                                               |
| Absolute path          | Used as-is                                                                                                                                                                                        |
| `@repo/path` reference | Resolved via `solution.spec.repositories` repo map. Requires initialized workspace. If solution is `None`, emit `⚠️` on phase 1 with detail: `"@repo reference requires an initialized workspace"` |

### File inspection phase table

| #   | Label              | Check                                                | ✅ Done          | ⚠️ Attention                         | ⬜ Not started        |
| --- | ------------------ | ---------------------------------------------------- | --------------- | ----------------------------------- | -------------------- |
| 1   | File readable      | Path exists + YAML parses without error              | yes             | YAML parse error (include line/col) | path not found       |
| 2   | Kind recognized    | `kind:` field present + value in `PlatformKind` enum | recognized kind | value present but not a known kind  | `kind:` field absent |
| 3   | apiVersion present | `apiVersion:` field == `strata.huybrechts.xyz/v1`    | correct         | present but wrong value             | absent               |
| 4   | Name present       | `meta.name` present and non-empty string             | valid           | present but empty string            | absent               |
| 5   | Spec present       | `spec:` block exists and is a non-empty mapping      | yes             | present but empty                   | absent               |

**Blocking rule:** phases 2–5 are blocked by phase 1 (`⬜` if file is unreadable — no point checking structure).

### Per-kind next-step hints

After the file phase checklist, show up to two action blocks: **validate** and **register/use**.

The validate hint is always: `strata validate -f <resolved_path>` (validate auto-detects kind).

The register/use hint is kind-specific:

| Kind            | Register / use command                                              | Help topic        |
| --------------- | ------------------------------------------------------------------- | ----------------- |
| `configuration` | `strata ref config add <name> @<repo>/path.yaml --profile <active>` | `environments`    |
| `environment`   | `strata ref env add <name> @<repo>/path.yaml --profile <active>`    | `environments`    |
| `deployment`    | Add to `workspace.yaml` under `spec.deployments`                    | `deployments`     |
| `namespace`     | Referenced from a deployment spec via `spec.namespaces`             | `environments`    |
| `network`       | Add to `workspace.yaml` under `spec.networks`                       | `environments`    |
| `dns`           | `strata ref config add <name> @<repo>/path.yaml --profile <active>` | `environments`    |
| `firewall`      | `strata ref config add <name> @<repo>/path.yaml --profile <active>` | `environments`    |
| `module`        | Referenced from a deployment spec via `spec.modules`                | `deployments`     |
| `workspace`     | Used directly by `strata build run` — no registration needed        | `getting-started` |
| `provider`      | Referenced in `workspace.yaml` under `spec.providers`               | `getting-started` |
| `resource`      | Referenced from a deployment spec via `spec.resources`              | `deployments`     |
| unknown         | — (show kind list instead)                                          | `quickstart`      |

For register hints: if workspace is initialized and has an active profile, substitute the real active profile name for `<active>`. Otherwise show the literal `<active>` placeholder.

### File mode console output

```
File: path/to/my-config.yaml  (kind: configuration)
Workspace: my-platform  (e:\src\my-config-repo)

File structure:

  ✅ File readable
  ✅ Kind: configuration
  ✅ apiVersion: strata.huybrechts.xyz/v1
  ✅ Name: my-config
  ✅ Spec present

→ Validate:

   strata validate -f path/to/my-config.yaml

→ Register with your active profile:

   strata ref config add my-config @<repo>/path/to/my-config.yaml --profile prd

   See: strata help --topic environments
```

**When kind is unknown:**
```
  ⚠️  Kind: not recognized ("blah") — expected one of: configuration, deployment,
      dns, environment, firewall, module, namespace, network, provider, resource, workspace
```

**When file is not found:**
```
File: path/to/missing.yaml  (not found)
Workspace: my-platform  (e:\src\my-config-repo)

File structure:

  ⬜ File readable — path/to/missing.yaml not found

→ Check the path and try again.
```

### File mode JSON shape

```json
{
  "file": {
    "path": "path/to/my-config.yaml",
    "kind": "configuration | null",
    "name": "my-config | null"
  },
  "workspace": {
    "name": "my-platform | null",
    "path": "e:\\src\\my-config-repo",
    "solution_id": "uuid | null"
  },
  "checklist": [
    {
      "phase": 1,
      "label": "File readable",
      "status": "ok | warn | pending",
      "detail": "string | null"
    }
  ],
  "next_steps": [
    {
      "action": "validate",
      "hint": "strata validate -f path/to/my-config.yaml",
      "see_also": null
    },
    {
      "action": "register",
      "hint": "strata ref config add my-config @<repo>/path.yaml --profile prd",
      "see_also": "strata help --topic environments"
    }
  ]
}
```

Note: both workspace mode and file mode use `next_steps` (array). Workspace mode: 0 entries when complete, 1 entry otherwise. File mode: 1 entry (validate only) for unknown kinds, 2 entries (validate + register) for known kinds.

### New private methods (additions to `GuideCommand`)

| Method                     | Signature                                                                                        | Purpose                                                                                                  |
| -------------------------- | ------------------------------------------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------- |
| `_resolve_file_path`       | `(raw: str, solution: Optional[SolutionModel]) → Path`                                           | Resolves relative, absolute, and `@repo/` paths. Raises `ValueError` on unresolvable `@repo/` reference. |
| `_evaluate_file_checklist` | `(path: Path) → tuple[List[ChecklistItem], Optional[str], Optional[str]]`                        | Returns checklist + detected kind + detected name.                                                       |
| `_find_file_next_steps`    | `(kind: Optional[str], active_profile: Optional[str], resolved_path: Path) → List[NextStepItem]` | Returns ordered action list (validate first, register second).                                           |
| `_render_file_console`     | `(checklist, next_steps, file_path, kind, workspace_name, work_path) → None`                     | File mode console output.                                                                                |
| `_render_file_json`        | `(checklist, next_steps, file_path, kind, name, workspace_name, solution_id) → dict`             | File mode JSON dict.                                                                                     |

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
  "next_steps": [
    {
      "phase": 6,
      "label": "File references registered",
      "hint": "strata ref config add <name> @<repo>/path/to/config.yaml --profile prd",
      "see_also": "strata help --topic environments"
    }
  ],
  "complete": false
}
```

Status values:
- `"ok"` → ✅
- `"warn"` → ⚠️
- `"pending"` → ⬜

`next_steps` is `[]` (empty array) when `complete == true`.

`detail` is `null` when there is nothing extra to say about a phase (e.g. a plain ✅ or plain ⬜).

Both workspace mode and file mode use `next_steps` as an array. Workspace mode always has 0 or 1 entries; file mode has 1 (validate) or 2 (validate + register) entries.

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
@click_file
@click_work_path
@click_output_format
@click_output_verbose
@click_output_quiet
def guide_command(file, work_path, output, verbose, quiet):
    """Show setup progress and suggest the next action for this workspace."""
    command = GuideCommand(file=file, work_path=work_path, output=output, verbose=verbose, quiet=quiet)
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

| Method                | Signature                                                                                                   | Purpose                                                                                                                                                                                                                    |
| --------------------- | ----------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `_load_solution`      | `() → Optional[SolutionModel]`                                                                              | Checks path existence first. If file absent, returns `None` (caller marks phase 1 ⬜). If file exists, calls `SolutionService.load_from_json(path)` — returns `None` on parse error (caller marks phase 1 ⚠️). Never raises. |
| `_evaluate_checklist` | `(solution: Optional[SolutionModel], solution_exists: bool) → List[ChecklistItem]`                          | Runs all 7 phase checks. `solution_exists` distinguishes ⬜ (absent) from ⚠️ (parse error) on phase 1.                                                                                                                       |
| `_find_next_step`     | `(checklist: List[ChecklistItem], solution: Optional[SolutionModel], hints: dict) → Optional[NextStepItem]` | Returns hint for first non-ok phase. Needs `solution` to build phase 3 dynamic git-clone lines.                                                                                                                            |
| `_render_console`     | `(checklist, next_step, workspace_name, work_path) → None`                                                  | Emits all `click.echo()` calls.                                                                                                                                                                                            |
| `_render_json`        | `(checklist, next_step, workspace_name, solution_id) → dict`                                                | Returns the JSON-serialisable dict stored in `self._output_data`.                                                                                                                                                          |

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
    see_also: Optional[str] = None
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
| 5   | `src/strata/data/guide-hints.yaml`                | **NEW**       | Built-in default hints for all 7 workspace phases, all file inspection phases, and all kind-specific register/use commands                   |
| 6   | `src/strata/services/solution_service.py`         | **READ ONLY** | `load_from_json(path)` already exists — no changes                                                                                           |
| 7   | `src/strata/models/solution_model.py`             | **READ ONLY** | All fields already modelled — no changes                                                                                                     |

Total: 4 new files, 1 modified file. Zero new models, zero new services.

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

**AD-GUIDE-11 — `--file` switches mode entirely; workspace checklist is suppressed.**

Showing both the workspace checklist and the file inspection in the same output would create an ambiguous "what should I focus on?" problem. The user invoked guide with a specific file — that is their focus. The workspace header (name + path) is preserved as context, but the checklist body is replaced by file-specific output. Users who want both views run the command twice.

**AD-GUIDE-12 — File inspection is structural-only; it does not invoke `ValidateCommand`.**

The file checklist phases (1–5) only check YAML parseability and field presence — they do not execute schema validation. Full schema validation is `strata validate -f <path>`, which the guide surfaces as the "validate" next-step hint. Running validation inside guide would duplicate `ValidateCommand` logic, add subprocess risk, and potentially produce misleading error messages in an advisory tool. The guide tells you *how* to validate; `validate` does the actual work.

**AD-GUIDE-13 — Default hints live in `src/strata/data/guide-hints.yaml`, not in Python source.**

Hint text is content, not logic. Keeping it in a YAML data file means it can be read and edited without touching Python, diffed clearly in PRs, and found by anyone looking for "what does phase 6 say?". The file is loaded once at execution time via `Path(__file__).parent.parent.parent / "data" / "guide-hints.yaml"` (three `.parent` calls from `strata/commands/guide/show_guide_command.py` to reach `strata/`) — no `importlib.resources` complexity needed. If the file is missing entirely (packaging bug), raise `PlatformFileNotFoundError` — this is not a recoverable user error. `pyproject.toml` already declares `"strata.data" = ["*.yaml", "*.txt"]`, so `guide-hints.yaml` is included in the wheel automatically — no `pyproject.toml` change needed.

**AD-GUIDE-14 — Project overrides live in `.strata/guide.yaml`; merge is shallow per key, never full replacement.**

The override file follows the same convention as `.strata/cli.yaml` (workspace config, not a platform document — no `apiVersion`/`kind`/`meta`). Only the keys present in the override are applied; missing keys fall through to the built-in default. This means a project only needs to specify what differs. The merge is shallow at the hint level: if a project overrides `phases.6.hint`, only that string is replaced — `phases.6.see_also` is still pulled from the default. No deep-merge of nested structures beyond that.

---

## 8. Hint Customization

### Built-in defaults — `src/strata/data/guide-hints.yaml`

Shipped with the package. Defines hint text for every phase and kind. Example structure:

```yaml
# Built-in guide hints. Override per-project via .strata/guide.yaml.

header: null          # null = no custom header
complete: "All setup phases complete. Your workspace is ready to deploy."

phases:
  1:
    hint: "strata sln init <name>"
    see_also: "strata help --topic quickstart"
  2:
    hint: "strata repo add <name> <url>"
    see_also: "strata help --topic repos"
  3:
    hint: null          # hint is built dynamically (git clone per missing repo)
    see_also: "strata help --topic repos"
  4:
    hint: "strata profile add <name> --activate"
    see_also: "strata help --topic profiles"
  5:
    hint: "strata profile activate <name>"
    see_also: "strata help --topic profiles"
  6:
    hint: "strata ref config add <name> @<repo>/path/to/config.yaml --profile <active>"
    see_also: "strata help --topic environments"
  7:
    hint: "strata build run"
    see_also: "strata help --topic build"

kinds:
  configuration:
    register: "strata ref config add <name> @<repo>/path.yaml --profile <active>"
    see_also: "strata help --topic environments"
  environment:
    register: "strata ref env add <name> @<repo>/path.yaml --profile <active>"
    see_also: "strata help --topic environments"
  deployment:
    register: "Add to workspace.yaml under spec.deployments"
    see_also: "strata help --topic deployments"
  namespace:
    register: "Referenced from a deployment spec via spec.namespaces"
    see_also: "strata help --topic environments"
  network:
    register: "Add to workspace.yaml under spec.networks"
    see_also: "strata help --topic environments"
  dns:
    register: "strata ref config add <name> @<repo>/path.yaml --profile <active>"
    see_also: "strata help --topic environments"
  firewall:
    register: "strata ref config add <name> @<repo>/path.yaml --profile <active>"
    see_also: "strata help --topic environments"
  module:
    register: "Referenced from a deployment spec via spec.modules"
    see_also: "strata help --topic deployments"
  workspace:
    register: "Used directly by strata build run — no registration needed"
    see_also: "strata help --topic getting-started"
  provider:
    register: "Referenced in workspace.yaml under spec.providers"
    see_also: "strata help --topic getting-started"
  resource:
    register: "Referenced from a deployment spec via spec.resources"
    see_also: "strata help --topic deployments"
```

Phase 3's `hint: null` is a sentinel — the hint is always built dynamically from the list of missing repos and cannot be a static string.

### Project overrides — `.strata/guide.yaml`

Optional. Only specify what differs from the defaults. Keys not present fall through to `guide-hints.yaml`.

```yaml
# .strata/guide.yaml — project-specific guide customization
# All keys optional. Missing keys use built-in defaults.

header: "Welcome to XYZ Platform. Run 'strata guide' to check your setup status."
complete: "XYZ Platform workspace is ready. Proceed to the deployment runbook."

phases:
  6:
    hint: |
      strata ref config add app @xyz-configuration/config/app.yaml --profile prd
      strata ref env add env @xyz-configuration/environments/prd.yaml --profile prd
    see_also: "https://confluence.xyz.com/display/PLAT/strata-refs"

kinds:
  configuration:
    register: "strata ref config add <name> @xyz-configuration/config/<name>.yaml --profile <active>"
    see_also: "https://confluence.xyz.com/display/PLAT/configuration"
  deployment:
    register: "Add to xyz-workspace/workspace.yaml under spec.deployments"
    see_also: "https://confluence.xyz.com/display/PLAT/deployments"
```

### Loading algorithm (`_load_hints`)

```
1. Load src/strata/data/guide-hints.yaml  → hints (dict)
2. path = work_path / ".strata" / "guide.yaml"
3. If path does not exist → return hints as-is
4. Load path with yaml.safe_load → overrides (dict)
5. For each top-level key in overrides:
   - "header", "complete" → replace scalar directly
   - "phases" → for each phase number key:
       for each sub-key ("hint", "see_also") → replace that sub-key only
   - "kinds" → for each kind key:
       for each sub-key ("register", "see_also") → replace that sub-key only
6. Return merged hints
```

Errors in `.strata/guide.yaml` (bad YAML, wrong types) are caught and logged at `WARNING` level. The command continues with built-in defaults — a malformed override file never breaks `strata guide`.

### Token substitution

Hints may contain tokens that are substituted at render time:

| Token      | Replaced with                                                                   |
| ---------- | ------------------------------------------------------------------------------- |
| `<active>` | Active profile name from `solution.json`, or literal `<active>` if none         |
| `<name>`   | `meta.name` value from the inspected file (file mode only), or literal `<name>` |
| `<repo>`   | Left as-is — user fills this in (repo name is context-specific)                 |
| `<path>`   | Resolved file path (file mode only)                                             |
