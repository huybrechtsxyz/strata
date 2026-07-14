# Explicit Command Lifecycle: ABC-Enforced Phases and Thin Overrides

- Status: proposed
- Date: 2026-07-11

## Context and Problem Statement

Strata's `BaseCommand` uses the **Template Method** pattern: `execute()` is a concrete
method on the base class that calls `_initialize → _before_execute → _run →
_after_execute → _finalize` in a fixed sequence. Subclasses override only the phases
they need (usually just `_run()`).

This was the right starting point, but three problems have emerged as the codebase has
grown to 80+ commands:

1. **Invisible lifecycle.** A new contributor reading a command class such as
   `ValidateCommand` or `BuildRunCommand` sees `_run()` but has no idea what happens
   before or after it without reading the base class. The contract is implicit.

2. **Inconsistent `execute()` overrides.** Several commands override `execute()` to
   diverge from the standard flow (add extra phases, skip `_after_execute`, etc.).
   Because `execute()` is concrete on the base, nothing prevents partial overrides from
   accidentally skipping `_finalize` — leaving audit entries unwritten and structured
   output unemitted on failure paths.

3. **No structural contract for commands that do not need the full lifecycle.** A
   schema-only validation command (`strata validate`) or a version-display command
   (`strata version`) has no workspace requirement. Forcing them through the full
   `_initialize → solution.json check → audit log setup` path causes unnecessary
   failures and confusing error messages ("Not a strata workspace. Run 'strata sln init'
   first.") when `INIT_REQUIRED = False` flags are easy to forget.

A comparison with the `sterling` codebase (same author, same layered architecture)
shows a cleaner approach: `BaseCommand` is an abstract class, `execute()` is an
`@abstractmethod`, and each command writes its own four-line phase walk. This makes the
lifecycle **explicit and local** — readable in the command file, not hidden in the base.

## Decision Drivers

- Lifecycle phases must be visible in each command file (no hidden execution order).
- `_finalize` (audit entry + structured output envelope) must be called on every exit
  path, including early returns from failed `_initialize` or `_before_execute`.
- Commands that do not need workspace initialisation must be expressible without
  `INIT_REQUIRED` flags.
- The change must be backward-compatible for all existing commands: no forced mass
  rewrite required before the decision is accepted.
- The base class must enforce the contract, not just document it.

## Considered Options

### Option A: Document the existing template-method contract more thoroughly
- Add comprehensive docstrings and a CONTRIBUTING guide section.
- Leave `execute()` concrete.
- Pro: Zero migration cost.
- Con: Does not fix the problem — the lifecycle is still invisible in each command
  file, and partial `execute()` overrides still risk skipping `_finalize`.
- **Rejected:** Documentation cannot enforce correctness.

### Option B: Make `BaseCommand` abstract; `execute()` is an `@abstractmethod`
- `BaseCommand` declares `execute()` as abstract.
- Each command writes its own `execute()` that walks the five phases explicitly.
- A helper `_run_phases()` is provided on the base to reduce boilerplate for the
  standard (no-deviation) case.
- Commands that deviate (e.g. `init`, which skips the `.strata/` existence check)
  simply write their own `execute()` without calling `_run_phases()`.
- Pro: Lifecycle is explicit and local in every command file.
- Pro: `_finalize` is called by the command — forgetting it is a type-check / test
  failure, not a silent runtime skip.
- Pro: No `INIT_REQUIRED` flags needed — commands that create the workspace just
  override `_initialize()` to skip the directory check, exactly as they do today.
- Con: Each command gains ~10 lines of lifecycle boilerplate.
- Mitigation: `_run_phases()` reduces this to 3-4 lines for the common case.

### Option C: Replace with a decorator / context-manager lifecycle
- A `@command_lifecycle` decorator on each `execute()` injects phase calls
  automatically.
- Pro: Zero boilerplate per command.
- Con: Magic — the lifecycle is invisible again, just in a different place.
- **Rejected:** Violates the explicitness requirement.

## Decision Outcome

Chosen: **Option B — abstract `BaseCommand`, explicit `execute()` in each command**.

### Core contract

`BaseCommand` becomes an abstract base class (ABC). `execute()` is declared as
`@abstractmethod`. The base class exposes a convenience helper `_run_phases()` for the
standard case (no lifecycle deviation):

```python
# BaseCommand (base_command.py)
from abc import ABC, abstractmethod

class BaseCommand(ABC):
    OPERATION: str = "base_command"

    @abstractmethod
    def execute(self, *args: Any, **kwargs: Any) -> bool:
        """Orchestrate the five lifecycle phases and return overall success."""
        ...

    def _run_phases(self, show_chrome: bool = True) -> bool:
        """Convenience: run the standard five-phase lifecycle.

        Commands with no lifecycle deviation call this from their own execute():

            def execute(self) -> bool:
                return self._run_phases()
        """
        try:
            if not self._initialize(show_header=show_chrome):
                self._finalize(success=False, show_footer=show_chrome)
                return False

            if not self._before_execute():
                self._finalize(success=False, show_footer=show_chrome)
                return False

            success = self._run()

            self._after_execute()
            self._finalize(success=success, show_footer=show_chrome)
            return success

        except Exception as exc:
            self._errors.append(f"Unexpected error: {exc}")
            self.logger.exception("Command failed", error=str(exc))
            self._finalize(success=False, show_footer=show_chrome)
            return False

    def _run(self) -> bool:
        """Phase 3 — command core work.

        Override when using _run_phases(). Not called when execute() is
        written manually.
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} must implement _run() when using _run_phases()."
        )
```

### Standard command pattern (uses `_run_phases`)

Commands with no lifecycle deviation — the majority of strata commands — implement
only `_run()` and write a one-liner `execute()`:

```python
class StatusDeployCommand(BaseCommand):
    OPERATION = "deploy_status"

    def __init__(self, ...) -> None:
        super().__init__(...)
        ...

    def execute(self) -> bool:
        return self._run_phases()

    def _run(self) -> bool:
        # All business logic here.
        ...
        return True
```

### Deviant command pattern (custom `execute()`)

Commands that need a different lifecycle (e.g. `strata sln init` which must skip the
`.strata/` directory check, or `strata validate` which is stateless) write a full
`execute()`:

```python
class InitSlnCommand(BaseCommand):
    OPERATION = "sln_init"

    def execute(self) -> bool:
        try:
            # Phase 1: custom _initialize that skips .strata/ existence check.
            if not self._initialize():
                self._finalize(success=False)
                return False

            # Phase 2: validate template name.
            if not self._before_execute():
                self._finalize(success=False)
                return False

            success = self._create_workspace()

            self._finalize(success=success)
            return success

        except Exception as exc:
            self._errors.append(f"Unexpected error: {exc}")
            self.logger.exception("sln init failed", error=str(exc))
            self._finalize(success=False)
            return False
```

### Stateless command pattern (no workspace, no audit)

Commands that are purely informational (`strata version`, `strata schema list`) can
inherit from a lighter `StatelessCommand` base that skips workspace initialisation,
environment loading, and audit logging entirely:

```python
class StatelessCommand:
    """Lightweight base for commands that require no workspace context."""

    OPERATION: str = "stateless"

    def __init__(self, output: Optional[str] = None) -> None:
        self._output_format = output or "console"
        self._errors: list[str] = []
        self._messages: list[str] = []
        self.logger = get_logger(self.__class__.__module__)

    @abstractmethod
    def execute(self) -> bool: ...

    def _is_console_output(self) -> bool:
        return self._output_format in ("console", "")

    def _is_structured_output(self) -> bool:
        return self._output_format in ("json", "text")
```

> `ValidateCommand` is a natural candidate for `StatelessCommand`: it requires a file
> path, not a workspace. The current `INIT_REQUIRED = False` flag plus workspace wiring
> adds complexity for no benefit.

## Migration Plan

The change is fully backward compatible. It is applied incrementally:

1. **Phase 1 — Base class changes** (single PR, no behavioral change):
   - Import `ABC`, `abstractmethod` into `base_command.py`.
   - Add `@abstractmethod` to `execute()`.
   - Add `_run_phases()` helper (content is the current concrete `execute()` body,
     minus the `SHOW_CHROME` classvar reference — pass `show_chrome` explicitly instead).
   - `_run()` default remains `raise NotImplementedError`.
   - All existing commands that override `_run()` now call `_run_phases()` from their
     `execute()` — add a one-liner `execute()` to each.
   - All existing commands that already override `execute()` are unchanged.
   - **No behavioral change.** Tests pass without modification.

2. **Phase 2 — Migrate deviant commands** (per command group PR):
   - For each command group that overrides `execute()` with non-standard logic, verify
     the override is intentional and add a comment explaining the deviation.
   - Remove `INIT_REQUIRED = False` from commands that should become `StatelessCommand`
     subclasses; migrate them.

3. **Phase 3 — Enforce via linting** (CI addition):
   - Add a `ruff` rule or a custom `scripts/Check.ps1` assertion: every class in
     `src/strata/commands/` that inherits `BaseCommand` must define its own `execute()`
     method (i.e. the abstract contract is satisfied at the class level, not inherited).
   - This prevents future commands from accidentally relying on an inherited concrete
     `execute()`.

## Consequences

**Good:**
- The lifecycle is explicit in every command file. A new contributor can read
  `RunDeployCommand.execute()` and understand the full execution order without reading
  the base class.
- `_finalize` (audit + structured output) is guaranteed to run on every exit path
  because the command author wires it in their `execute()` — there is nowhere for it to
  disappear silently.
- `INIT_REQUIRED = False` class variables are eliminated over time; commands that need
  no workspace context opt out structurally by using `StatelessCommand`.
- The `_run_phases()` helper keeps the common case at one line per command, so the
  explicitness comes at near-zero boilerplate cost for standard commands.

**Bad / Trade-offs:**
- Every command file gains `def execute(self) -> bool: return self._run_phases()` —
  three lines of boilerplate. This is the price of explicitness.
- The ABC constraint means `mypy` will flag any `BaseCommand` subclass that does not
  implement `execute()`. This is intentional but may surface hidden violations on the
  first migration run.

## Reference: Sterling Implementation

The sterling codebase (same author, same layered-architecture pattern) serves as the
reference implementation for this pattern. Key files:

- `src/sterling/commands/base_command.py` — abstract `BaseCommand` with five lifecycle
  phases, shared `_emit_stage / _emit_task_line / _emit_summary_line` console helpers,
  `_finalize` always writes the audit entry.
- `src/sterling/commands/init_command.py` — example of a deviant command: overrides
  `_initialize()` to skip the `.sterling/` directory check, writes its own `execute()`.
- `src/sterling/commands/run_command.py` — example of a complex command that writes its
  own `execute()` with extra phases and per-phase error handling.
- `src/sterling/commands/validate_command.py` — example of a stateless command: does
  **not** inherit `BaseCommand`; is a plain class with a minimal `execute()` and its
  own output logic.

These patterns map directly to the three command archetypes described in this ADR.

## More Information

Related ADRs:
- [ADR-0003](0003-layered-architecture.md) — commands are thin wrappers; business logic
  belongs in controllers.
- [ADR-0004](0004-exit-code-convention.md) — `_finalize` is responsible for emitting
  the structured output envelope that carries the exit-code-relevant `success` field.
- [ADR-0018](0018-deployment-audit-traceability.md) — every command execution produces
  an audit entry via `_finalize`; this ADR ensures `_finalize` is never accidentally
  skipped.
- [ADR-0020](0020-cli-parameter-consistency-standard.md) — parameter ordering standard
  for all commands; applies at the Click decorator layer above `BaseCommand`.
