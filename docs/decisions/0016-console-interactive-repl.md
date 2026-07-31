# `strata console` — Interactive Workspace Console

- Status: completed
- Date: 2026-06-24
- Revised: 2026-07-27
- Parent: [0014-onboarding-experience.md](0014-onboarding-experience.md) (items #6, #7, #8, #9)

## Status Note

`strata console` is fully implemented and ships as a top-level command. It works as designed.

It is **not the recommended first-choice workflow** for most users:

- **VS Code users** — the extension's Help pane, readiness checklist, and `@strata /guide` chat participant cover the same use case with less context-switching.
- **Terminal-native / SSH users** — `strata guide --next` and `strata guide --do` are composable, scriptable, and SSH-safe.
- **Cold-start onboarding** — `strata sln init --guided` is the recommended entry point.

`strata console` remains available for users who prefer a persistent session (local development, demo environments, exploratory onboarding). It does not conflict with the above and correctly describes its purpose.

**See also:**
- `strata guide --next` / `strata guide --do` — single-shot guided workflow
- `strata sln init --guided` — cold-start wizard

---

## Summary

A new `strata console` command that launches a persistent interactive session for workspace management. The console keeps workspace state in memory, offers command completion, and provides a guided onboarding experience where users can scaffold, validate, and explore without leaving the session.

`strata guide` remains unchanged as the single-shot readiness checker. `strata console` is the interactive evolution — it composes `guide`, `validate`, `new`, and other commands into one stateful session.

## Context

The current `strata guide` command is effective at diagnosing workspace state (8-phase checklist, file analysis, hints system) but suffers from a fundamental UX gap: it's stateless. Users must:

1. Run `strata guide` → read output
2. Copy-paste the suggested command → run it in a separate shell
3. Run `strata guide` again → see progress
4. Repeat

Each invocation re-loads the solution, re-evaluates all phases, and re-renders from scratch. There's no session continuity — the user is the state machine.

**The console solves this by:**
- Keeping workspace state in memory (solution model, checklist, detected files)
- Re-evaluating incrementally after each action
- Offering commands inline (scaffold, validate, open) without shell context-switching
- Providing progressive guidance (the next step updates automatically)

## What Exists Today

| Component                     | State                       | What it does                                                          |
| ----------------------------- | --------------------------- | --------------------------------------------------------------------- |
| `cli_guide.py`                | CLI wiring                  | Click command registration, option decorators                         |
| `guide/show_guide_command.py` | `GuideCommand(BaseCommand)` | Full execution: checklist evaluation, file analysis, hints, rendering |
| `data/guide-hints.yaml`       | Static data                 | Per-phase hints with override support                                 |
| `SolutionController`          | Backing state               | Solution model, active profile, repo map                              |
| `rich`                        | Dependency                  | Already in pyproject.toml (≥13.0) but unused by guide                 |
| `prompt_toolkit`              | **Missing**                 | Not in dependencies — needs adding                                    |

## Decision Drivers

- The onboarding experience should feel like pair-programming, not documentation lookup
- `strata guide` stays as-is for CI and scripting (no breaking change)
- Console commands should compose existing CLI capabilities, not duplicate them
- Session state should be lightweight — restart is cheap, not catastrophic
- Works on Windows (PowerShell, cmd) and Unix terminals equally

## Command Naming Decision

| Candidate          | Verdict    | Reasoning                                                                                           |
| ------------------ | ---------- | --------------------------------------------------------------------------------------------------- |
| `console`          | ✅ Selected | Implies interactive + stateful session (like `rails console`). Experienced users also reach for it. |
| `guide` (overload) | ❌          | Would require group conversion + breaking the existing single-shot behavior                         |
| `shell`            | ❌          | Confused with actual terminal shell                                                                 |
| `repl`             | ❌          | Too jargon-y, doesn't convey purpose                                                                |
| `pilot`            | ❌          | Unusual for CLI tools                                                                               |

`strata console` is a **new top-level command** alongside `guide`. Both appear in `strata --help`:
- `guide` — Show setup progress and suggest the next action (single-shot)
- `console` — Interactive workspace session with guided onboarding

## Design

### Command Structure

New standalone command (no group conversion needed):

```
strata console        ← enters the interactive session
strata guide          ← unchanged single-shot readiness checker
strata guide -f X     ← unchanged file-mode analysis
```

### Architecture

```
commands/
  cli_console.py            ← NEW: Click command registration
  cli_guide.py              ← UNCHANGED
  console/
    run_console_command.py  ← NEW: ConsoleCommand, launches REPL loop
  guide/
    show_guide_command.py   ← UNCHANGED

controllers/
  guide_controller.py       ← NEW: extracted stateful logic from GuideCommand

utils/
  (no new utils — prompt_toolkit handles input)
```

### Workflow Definition

The onboarding sequence is defined as data in `.strata/workflow.yaml`, not hardcoded in Python. A built-in default ships with strata (created by `sln init` or auto-generated on first `console` launch). Teams can customize it — add steps, reorder, change commands, or add their own domain-specific onboarding.

#### Default Workflow File (`.strata/workflow.yaml`)

```yaml
# Workspace onboarding workflow — drives `next` and `do` in the console.
# Customize per-project by editing steps, adding new ones, or changing commands.

steps:
  - id: workspace_init
    name: Workspace initialized
    check: solution_exists            # built-in check function
    command: "strata sln init {name}"
    hint: "Initialize the workspace to create .strata/ and solution.json"
    see_also: "strata help --topic quickstart"

  - id: repos_registered
    name: Repositories registered
    check: repos_registered
    depends_on: [workspace_init]
    command: "strata repo add {name} {url}"
    hint: "Register at least one configuration repository"
    see_also: "strata help --topic repos"

  - id: repos_on_disk
    name: Repositories on disk
    check: repos_cloned
    depends_on: [repos_registered]
    command: null                      # dynamic — git clone per missing repo
    hint: "Clone registered repositories to their configured paths"
    see_also: "strata help --topic repos"

  - id: profile_created
    name: Profile created
    check: profile_exists
    depends_on: [workspace_init]
    command: "strata profile add {name} --activate"
    hint: "Create a profile to organize file references"
    see_also: "strata help --topic profiles"

  - id: profile_activated
    name: Profile activated
    check: profile_active
    depends_on: [profile_created]
    command: "strata profile activate {name}"
    hint: "Activate a profile to set the working context"
    see_also: "strata help --topic profiles"

  - id: files_registered
    name: File references registered
    check: files_registered
    depends_on: [profile_activated]
    command: "strata ref config add {name} @{repo}/path.yaml --profile {active}"
    hint: "Register configuration files against the active profile"
    see_also: "strata help --topic environments"

  - id: build_exists
    name: Build artifact exists
    check: build_exists
    command: "strata build run"
    hint: "Run a build to generate deployment artifacts"
    see_also: "strata help --topic build"

  - id: inventory_generated
    name: Platform inventory generated
    check: sbom_exists
    depends_on: [build_exists]
    command: "strata build sbom -f {file}"
    hint: "Generate a platform inventory (SBOM) from the build"
    see_also: "docs/platform/builders.md"
```

#### Workflow Schema

```python
@dataclass
class WorkflowStep:
    id: str                           # unique identifier
    name: str                         # human-readable label (shown in checklist)
    check: str                        # name of a registered check function
    command: str | None               # strata command to execute (None = dynamic)
    depends_on: list[str] = []        # step IDs that must be complete first
    hint: str = ""                    # guidance text shown by `next`
    see_also: str = ""                # reference link
    skippable: bool = False           # can the user skip this step?
```

#### Check Functions Registry

Check functions are registered in `GuideController` and looked up by name from the workflow:

```python
WORKFLOW_CHECKS: dict[str, Callable[..., CheckResult]] = {
    "solution_exists": check_solution_exists,
    "repos_registered": check_repos_registered,
    "repos_cloned": check_repos_cloned,
    "profile_exists": check_profile_exists,
    "profile_active": check_profile_active,
    "files_registered": check_files_registered,
    "build_exists": check_build_exists,
    "sbom_exists": check_sbom_exists,
}
```

Each check function returns a `CheckResult(status: "ok" | "pending" | "warn", detail: str | None)`. The existing evaluation logic from `_evaluate_checklist()` moves into these individual functions.

Custom workflows can only reference built-in check names. User-defined check functions are out of scope (Phase 3+).

#### How `next` and `do` Use the Workflow

1. Load `.strata/workflow.yaml` (fall back to built-in default if missing)
2. Walk steps in order
3. For each step: run its `check` function, evaluate `depends_on`
4. First step where `check` returns non-`ok` AND all `depends_on` are `ok` → that's the next step
5. `next` shows the step's `name`, `hint`, and `command`
6. `do` calls `next` internally, then executes the `command` (via JSON shell-out)
7. If all steps are `ok`: `"All steps complete. Your workspace is ready."`

#### Extensibility Examples

A team could add domain-specific steps:

```yaml
  - id: terraform_init
    name: Terraform initialized
    check: build_exists               # reuse existing check
    depends_on: [repos_on_disk]
    command: "strata tools run terraform init"
    hint: "Initialize Terraform in the infrastructure repo"
    skippable: true
```

Or reorder — put `build_exists` before `files_registered` if that fits their workflow.

### `GuideController`

Extract from `GuideCommand` into a proper controller (per architecture rules):

```python
class GuideController(BaseController):
    """Manages workspace readiness state for the console and guide."""

    def __init__(self, work_path: Path) -> None:
        super().__init__()
        self._work_path = work_path
        self._solution_controller = SolutionController(work_path)
        self._workflow: list[WorkflowStep] = []
        self._checklist: list[ChecklistPhase] = []
        self._hints: dict = {}
        self._session_history: list[str] = []

    def load_workflow(self) -> list[WorkflowStep]:
        """Load .strata/workflow.yaml, fall back to built-in default."""

    def evaluate(self) -> list[ChecklistPhase]:
        """Run check functions for each workflow step. Returns checklist."""

    def find_next_step(self) -> Optional[NextStep]:
        """Walk workflow, return first incomplete step with deps satisfied."""

    def execute_step(self, step: WorkflowStep) -> dict:
        """Shell out the step's command with --output json, return envelope."""

    def evaluate_file(self, file_path: Path) -> list[ChecklistPhase]:
        """Run 5-phase file analysis."""

    @property
    def is_complete(self) -> bool:
        """All phases OK."""

    @property
    def active_profile(self) -> Optional[str]:
        """Currently active profile name."""
```

### REPL Commands

| Command                 | Alias | Action                                  | Backing                                  |
| ----------------------- | ----- | --------------------------------------- | ---------------------------------------- |
| `status`                | `s`   | Show 8-phase workspace checklist        | `GuideController.evaluate()`             |
| `check <file>`          | `c`   | File-mode analysis                      | `GuideController.evaluate_file()`        |
| `next`                  | `n`   | Show next step with hint                | `GuideController.find_next_step()`       |
| `do`                    | `d`   | Execute the suggested next-step command | `subprocess.run()` via integration layer |
| `new <template> [name]` | —     | Scaffold a file via `strata new`        | Shell out to `strata new`                |
| `validate [file\|glob]` | `v`   | Run validation                          | Shell out to `strata validate run`       |
| `graph [--mode]`        | `g`   | Render dependency graph                 | Shell out to `strata validate graph`     |
| `templates`             | `t`   | List available templates                | Shell out to `strata new --list`         |
| `tools`                 | —     | Check external tool availability        | Shell out to `strata tools status`       |
| `open <file>`           | `o`   | Open file in `$EDITOR` or VS Code       | `click.launch()` or `code` CLI           |
| `help`                  | `?`   | Show command table                      | Inline                                   |
| `clear`                 | —     | Clear terminal                          | ANSI escape / `cls`                      |
| `quit`                  | `q`   | Exit REPL                               | `raise SystemExit`                       |

### REPL Input Handling

```python
from prompt_toolkit import PromptSession
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.completion import WordCompleter

session = PromptSession(
    history=InMemoryHistory(),
    completer=WordCompleter(
        ["status", "check", "next", "do", "new", "validate", "graph",
         "templates", "tools", "open", "help", "clear", "quit"],
        ignore_case=True,
    ),
)
```

### Rendering

Use `rich` for structured output inside the REPL:

| Element       | Rich component                                   |
| ------------- | ------------------------------------------------ |
| Checklist     | `Table` with status emoji + phase label + detail |
| Next step     | `Panel` with hint text + see_also                |
| File analysis | `Tree` with nested phases                        |
| Header        | `Panel` with workspace name + progress bar       |
| Errors        | `Panel(style="red")` with error text             |
| Command help  | `Table` with command/alias/description columns   |

The existing `click.echo()` rendering in `show_guide_command.py` stays unchanged for the single-shot mode. Only the REPL uses Rich.

### Auto-Refresh After Actions

After `do`, `new`, or `validate` commands, the REPL automatically re-evaluates the checklist and shows updated status:

```
console> new configuration my-platform --path config/
  ✅ Created config/my-platform-config.yaml

  [auto-refresh]
  Phase 6 updated: ⬜ → ✅ File references registered (1 file)

console> next
  → strata new environment dev --path envs/
```

### Session Lifecycle

```
┌─────────────────────────────────────────────────────┐
│ strata console                                       │
├─────────────────────────────────────────────────────┤
│ 1. Initialize GuideController (load solution)        │
│ 2. Evaluate checklist (8 phases)                     │
│ 3. Render header + status + next step                │
│ 4. Enter prompt loop                                 │
│    ├─ Parse input → dispatch command                 │
│    ├─ Execute command (inline or shell-out)           │
│    ├─ Auto-refresh if state-changing command          │
│    └─ Re-render prompt                               │
│ 5. On quit/Ctrl+C → clean exit                       │
└─────────────────────────────────────────────────────┘
```

### Error Handling

- Unknown commands: `"Unknown command '{x}'. Type '?' for help."`
- Failed shell-outs: capture exit code + stderr, display in red panel, don't crash REPL
- Ctrl+C during command: cancel current action, return to prompt
- Ctrl+C at prompt: exit REPL (same as `quit`)
- Ctrl+D at prompt: exit REPL (EOF)
- Workspace not initialized: REPL still works — checklist shows phase 1 pending, `next` suggests `sln init`

## Implementation Plan

### Step 1: Add `prompt_toolkit` dependency

Add `prompt-toolkit>=3.0` to `pyproject.toml` dependencies.

### Step 2: Extract `GuideController`

Move stateful logic from `GuideCommand` into `controllers/guide_controller.py`:
- `_evaluate_checklist()` → `GuideController.evaluate()`
- `_find_next_step()` → `GuideController.find_next_step()`
- `_evaluate_file_checklist()` → `GuideController.evaluate_file()`
- `_load_hints()` / `_apply_tokens()` → controller private methods
- `_render_*` methods stay in the command (rendering is command-layer concern)

`GuideCommand.execute()` becomes a thin wrapper that instantiates the controller and calls render methods.

### Step 3: Create `strata console` command

New command registration in `cli.py`:
- `cli_console.py` with `@click.command(name="console")`
- Registered via `main.add_command(console_command, name="console")`
- `strata guide` remains untouched

### Step 4: Implement console REPL (`console/run_console_command.py`)

- `ConsoleCommand(BaseCommand)` — launches the prompt loop
- Uses `GuideController` for state
- Dispatches REPL commands via match/case
- Shell-outs use `subprocess.run(capture_output=True)` for inline display
- Rich rendering for checklist, panels, tables

### Step 5: Rich rendering for REPL

Replace `click.echo()` checklist with Rich `Table`:
```
┌─── Workspace: my-platform ──────────────────────────┐
│ ████████░░░░░░░░░░░░ 4/8 phases complete            │
├─────────────────────────────────────────────────────┤
│ ✅ Workspace initialized                             │
│ ✅ Repositories registered (2)                       │
│ ✅ Repositories on disk                              │
│ ✅ Profile created (default)                         │
│ ⬜ Profile activated                                 │
│ ⬜ File references registered                        │
│ ⬜ Build artifact exists                             │
│ ⬜ Platform inventory generated                      │
├─────────────────────────────────────────────────────┤
│ → Next: strata config set profile.active default     │
└─────────────────────────────────────────────────────┘
```

### Step 6: Auto-refresh + session history

- Track commands executed in `GuideController._session_history`
- After state-changing commands (`do`, `new`, `validate`), re-evaluate
- Show delta: which phases changed status

## Exit Codes

| Condition                                    | Exit Code         |
| -------------------------------------------- | ----------------- |
| REPL exited normally (quit/Ctrl+C)           | 0                 |
| Workspace not found                          | 1                 |
| REPL crashed (unhandled exception)           | 1                 |
| Single-shot mode (`show`) with all phases OK | 0                 |
| Single-shot mode with pending phases         | 0 (informational) |

## Edge Cases

| Case                              | Behavior                                                                       |
| --------------------------------- | ------------------------------------------------------------------------------ |
| No `.strata/` directory           | REPL still starts — phase 1 is "pending", next suggests `sln init`             |
| `$EDITOR` not set                 | `open` command tries `code` (VS Code CLI), then falls back to platform default |
| Terminal doesn't support color    | Rich auto-detects; falls back to plain text                                    |
| Very small terminal (< 40 cols)   | Rich wraps; no crash                                                           |
| Running inside CI (`CI=true` env) | Skip REPL, run single-shot mode automatically                                  |
| `prompt_toolkit` not installed    | Graceful error: "Install prompt-toolkit for interactive mode"                  |

## Dependencies

| Package          | Version | Purpose                                         |
| ---------------- | ------- | ----------------------------------------------- |
| `prompt-toolkit` | ≥3.0    | REPL input, history, completion                 |
| `rich`           | ≥13.0   | Already present — panels, tables, progress bars |

## Testing Strategy

- **Unit tests for `GuideController`**: checklist evaluation with mocked solution state, hint loading, token substitution
- **Integration tests for REPL dispatch**: mock `subprocess.run`, verify correct commands are assembled
- **CLI tests**: `CliRunner` invokes `strata console --help` + `strata guide` (unchanged)
- **No automated REPL interaction tests** — prompt_toolkit is notoriously hard to test in CI. Manual QA for the interactive loop.

## Relationship to Other Commands

| Command                 | Relationship                                      |
| ----------------------- | ------------------------------------------------- |
| `strata validate graph` | REPL's `graph` command shells out to this         |
| `strata validate run`   | REPL's `validate` command shells out to this      |
| `strata new`            | REPL's `new` command shells out to this           |
| `strata tools status`   | REPL's `tools` command shells out to this         |
| `strata sln init`       | Can be triggered via `do` when phase 1 is pending |
| `strata config`         | Can be triggered via `do` for profile activation  |

## Phase 2: Interactive File Creation (Wizards)

Phase 1 delivers the console REPL with shell-out to `strata new`. Phase 2 adds **interactive wizards** — form-like flows that ask questions and scaffold files from answers, all without leaving the console.

### Motivation

`strata new configuration my-platform` requires the user to already know:
- What kind of file to create
- What to name it
- Which fields are required vs optional
- What valid values look like (enums, references to other files)

A wizard removes that burden. The console asks, the user answers, the file appears — correctly structured, correctly referenced, correctly placed.

### UX Pattern

```
console> create

  What do you want to create?
  ❯ configuration
    environment
    deployment
    namespace
    module
    resource
    provider

  Name: my-platform
  Description (optional): Main platform configuration

  Which repositories? (select with space, enter to confirm)
  ❯ ☑ xyz-configuration
    ☑ xyz-infrastructure
    ☐ xyz-svc-traefik

  Output path [config/]: config/

  ✅ Created config/my-platform-config.yaml
  ✅ Registered in solution

  [auto-refresh]
  Phase 6 updated: ⬜ → ✅ File references registered
```

### Design Principles

| Principle              | Meaning                                                                     |
| ---------------------- | --------------------------------------------------------------------------- |
| Progressive disclosure | Start with required fields only. Offer "advanced options?" at the end.      |
| Smart defaults         | Pre-fill from workspace context (existing repos, profiles, naming patterns) |
| Validation inline      | Reject bad input immediately ("Name must be lowercase alphanumeric")        |
| Composable             | Wizard output feeds directly into `GuideController` for auto-refresh        |
| Skippable              | `create configuration my-platform` (with name) skips the name prompt        |
| Escapable              | Ctrl+C at any point cancels without side effects                            |

### Wizard Registry

Each `kind` gets a wizard definition:

```python
@dataclass
class WizardField:
    name: str
    prompt: str
    field_type: Literal["text", "select", "multiselect", "confirm"]
    required: bool = True
    default: str | None = None
    choices: list[str] | Callable[[], list[str]] | None = None
    validator: Callable[[str], str | None] | None = None  # returns error msg or None

@dataclass
class WizardDefinition:
    kind: str
    fields: list[WizardField]
    template: str  # Jinja2 template for the output YAML
    output_path: Callable[[dict[str, Any]], Path]  # resolve from answers
```

Wizards are data-driven — adding a new kind means adding a definition, not new code.

### Implementation Sketch

```python
class WizardRunner:
    """Executes a wizard definition interactively using prompt_toolkit."""

    def __init__(self, session: PromptSession, definition: WizardDefinition) -> None:
        ...

    async def run(self, prefilled: dict[str, str] | None = None) -> Path | None:
        """Walk through fields, collect answers, render template, write file."""
        ...
```

- `select` / `multiselect` use prompt_toolkit's `radiolist_dialog` / `checkboxlist_dialog` (or inline arrow-key selection)
- `text` uses standard prompt with validation callback
- `confirm` uses yes/no prompt
- Choices can be dynamic (e.g., list of repos from solution state)

### Console Commands (Phase 2 additions)

| Command                | Alias | Action                                                                 |
| ---------------------- | ----- | ---------------------------------------------------------------------- |
| `create [kind] [name]` | `cr`  | Launch wizard for the given kind. Omit kind for interactive selection. |
| `edit <file>`          | `e`   | Re-run wizard for an existing file (pre-fills from current values)     |

### Dependencies on Phase 1

- Requires `GuideController` (for auto-refresh after creation)
- Requires the console REPL loop (for prompt integration)
- Requires Rich (for select/multiselect rendering)

### Scope Boundary

Phase 2 does NOT include:
- Modifying existing files beyond re-running the wizard (`edit`)
- Multi-file transactions (create environment + all its modules in one flow)
- AI-assisted field suggestions

Those are Phase 3+ candidates.

## Open Questions

### Phase 1

1. **Shell-out via JSON envelope (resolved).** Shell out with `--output json` and parse the structured envelope (`success`, `data`, `errors`). This avoids shared-state corruption while returning rich structured data. The console parses `json.loads(stdout)` instead of scraping text. Overhead per call (~50-200ms) is imperceptible in interactive use. Some commands may need their `_output_data` enriched to include everything the console needs (e.g., `strata new` should return the created file path in its envelope). Audit existing `_output_data` payloads during implementation and extend where needed.

2. **Prompt string design (resolved).** Start simple: `strata> `. Avoid overloading the prompt with workspace name, progress, or profile — that information belongs in the `status` command output, not the prompt. Can revisit later if users ask for it.

3. **`do` / `next` driven by workflow definition (resolved).** The 8-phase checklist is really a workflow — ordered steps with dependencies, checks, and actions. Instead of burying this in code, define it as a YAML file at `.strata/workflow.yaml`. A built-in default ships with strata (matching today's 8 phases). Users or teams can customize the workflow by editing the file — adding steps, reordering, changing commands. `next` walks the workflow, finds the first incomplete step whose dependencies are satisfied, and shows its action. `do` always re-evaluates (no caching) — it calls `next` internally and executes the result. See the new **Workflow Definition** section in Design for the file format.

4. **`GuideController` shared refactor (resolved).** Yes — both `guide` and `console` use the same `GuideController`, which loads the same `workflow.yaml`. The controller is the shared brain; the commands are different UIs on top of it. `strata guide` instantiates the controller, calls `evaluate()`, and renders via `click.echo()` (unchanged output). `strata console` instantiates the controller, keeps it alive across the REPL loop, and renders via Rich. This is an explicit backward-compatible refactor: `GuideCommand.execute()` becomes a thin wrapper that delegates to `GuideController`. Existing tests and output stay identical.

5. **File path completion (resolved).** Yes — use prompt_toolkit's `NestedCompleter` to provide context-aware completion. Commands get `WordCompleter` for the command name, then switch to `PathCompleter` for arguments that expect file paths (`check`, `open`, `validate`, `new`). Additionally, filter YAML files only for strata-specific commands, and scope the path root to the workspace. prompt_toolkit supports this natively — no custom code needed beyond wiring the completer map.

6. **Hot-reload after `sln init` (resolved).** Auto-reload. After any state-changing command (`do`, `new`, `sln init`), the auto-refresh cycle already re-evaluates the checklist. Extend this to also re-initialize the `SolutionController` if `.strata/solution.json` appears or changes. The controller gets a `reload()` method that re-reads the solution from disk and reloads the workflow. If full hot-reload proves too complex during implementation, fall back to showing a warning: `"⚠ Workspace state changed. Run 'reload' to pick up changes."` — with a `reload` REPL command as the manual trigger.

7. **`--output json` interaction (resolved).** Not applicable. `strata console` does not accept `--output`, `--verbose`, or `--quiet` flags — it's an interactive console window, not a reportable command. The standard output decorators (`@click_output_format`, `@click_output_verbose`, `@click_output_quiet`) are simply not applied to `cli_console.py`. The only decorator needed is `@click_work_path`. Shell-outs from *inside* the console still use `--output json` to get structured data back, but that's an internal detail — the console command itself has no output mode.

8. **Session history persistence (resolved).** Yes — persist to `.strata/console-history`. The file is local-only (added to `.gitignore` by `sln init`). prompt_toolkit's `FileHistory` handles this natively — it appends one entry per command, so writes are minimal (one append per Enter). The console adds a `history clear` REPL command that deletes the file. No other management needed — the file is plain text, users can also just delete it manually.

9. **Rich rendering optional (resolved).** Yes — support `--no-color` flag on `strata console`. Rich already respects `NO_COLOR` env var (the [no-color.org](https://no-color.org) standard) and auto-detects dumb terminals, so most cases are handled for free. Add a `--no-color` Click flag that sets `Console(no_color=True)` in Rich. Minimal effort — one flag, one constructor arg, done.

10. **Watch mode for auto-refresh (resolved).** Yes — use `watchdog` (or stdlib `pathlib` polling as fallback) to watch `*.yaml` files under the workspace. On change, auto-refresh the checklist silently in the background and show a brief notification at the next prompt: `"[auto-refresh] Phase 6 updated: ⬜ → ✅"`. Debounce to ~2-3 seconds to avoid thrashing. Default off; activated with a `watch` REPL command (toggle on/off). No new dependency if we use a simple polling thread checking mtimes every ~60 seconds — good enough for interactive use and avoids platform-specific filesystem event quirks. If `watchdog` is already pulled in transitively, prefer it. Otherwise, poll.

### Phase 2

11. **Jinja2 dependency for wizard templates (resolved).** Yes — add Jinja2. Wizard templates use Jinja2 exclusively (`{{ var }}`, `{% if %}`, `{% for %}`). No support for `${VAR}`, `$VAR`, or `{var}` syntax — one templating engine, no ambiguity. The full codebase migration (including `TemplateProcessor`, builders, scaffold templates, and solution controller) is tracked in [ADR 0017 — Jinja2 Template Engine](0017-jinja2-template-engine.md). No backward compatibility needed — clean break.

12. **`edit <file>` reverse-parsing (resolved).** Load the file through its Pydantic model first. If validation fails, show the Pydantic errors and tell the user to fix them before the wizard can open — `"⚠ 3 validation errors in config/my-platform-config.yaml. Fix these first:"` followed by the error list. Pydantic v2 has no auto-fix mode, so it's warn-and-stop. If validation passes, map the model fields back to wizard answers (the model IS the answer set — field names match). Extra fields can't exist because all models use `extra="forbid"`. Unknown fields would have already failed validation.

13. **Wizard `--dry-run` (resolved).** No. Users have git — they can `git diff` to see what was created and `git checkout -- <file>` to undo it. Adding dry-run is extra complexity for a problem git already solves. The wizard creates one file; that's easy to review and revert.

14. **`do` command editing (resolved).** No — `do` executes immediately. `next` already shows the command, so the user sees what's coming. If they want to tweak it, they can type the command manually instead of using `do`. Adding an inline editor is complexity for a rare case. Can revisit if users ask for it.
