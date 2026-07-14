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

### Option D: Concrete `execute()` with always-run phases and `_execute()` override point

- `BaseCommand` keeps `execute()` as a **concrete, non-overrideable orchestrator**.
  Subclasses implement `_execute()` (renamed from `_run()`) for business logic.
- `execute()` always runs all four phases plus finalize. Each phase is wrapped in
  `try/except`; errors accumulate in `self._errors`. No phase is skipped due to a
  prior phase failure.
- Individual phases (`_initialize`, `_before_execute`, `_after_execute`) may be
  overridden for customisation — but they never control flow.
- `_finalize` is structurally guaranteed: it is always the last statement in the
  concrete `execute()`. It cannot be skipped by any subclass.
- Pro: `_finalize` is guaranteed without any per-command boilerplate.
- Pro: Phase infrastructure (timing, logging, config) always runs — even when business
  logic raises unexpectedly.
- Pro: Simple mental model: "each phase always runs; errors accumulate; finalize reports."
- Pro: No `INIT_REQUIRED` flags needed — commands needing no workspace context override
  `_initialize()` to skip the solution.json check, or inherit `StatelessCommand`.
- Con: `_execute()` may run even when `_initialize()` failed (e.g. workspace not found).
  Phase implementations must be resilient to partial setup.
- Mitigation: Per-phase try/except produces specific, actionable errors for each failed
  phase rather than a single cascading failure message.

## Decision Outcome

Chosen: **Option D — concrete `execute()`, always-run phases, `_execute()` override point**.

### Core principle

`BaseCommand.execute()` is a concrete orchestrator that always runs all lifecycle phases
in sequence. Each phase is wrapped in `try/except`; errors accumulate in `self._errors`.
No phase controls whether the next phase runs — that is `execute()`'s job, and the
answer is always "yes".

Subclasses override `_execute()` for business logic. Individual phases may be overridden
for customisation but never for flow control.

### Why Option D over Option B

Option B (abstract `execute()`, per-command lifecycle wiring) solves the visibility
problem but introduces a new risk: per-command boilerplate is still code the author can
get wrong. A command that writes `execute()` incorrectly still skips `_finalize`.

Option D removes the override point entirely. `_finalize` is structurally guaranteed —
it is always the last statement in `BaseCommand.execute()`, unreachable by any subclass
override. The lifecycle is enforced by architecture, not by convention.

### `BaseCommand` contract

```python
class BaseCommand:
    """Base command class. execute() is the concrete lifecycle orchestrator."""

    OPERATION: str = "base_command"
    SHOW_CHROME: ClassVar[bool] = True

    def execute(self) -> bool:
        """Run all lifecycle phases. Always reaches _finalize.

        Not intended for override. Subclasses implement _execute() for business
        logic. Individual phases may be overridden for customisation.
        """
        success = True

        # Phase 1: workspace, timing, logging, config setup
        try:
            if not self._initialize(show_header=self.SHOW_CHROME):
                success = False
        except Exception as exc:
            self._errors.append(f"Initialization failed: {exc}")
            self.logger.exception("_initialize raised", error=str(exc))
            success = False

        # Phase 2: pre-execution validation and requirement checks
        try:
            if not self._before_execute():
                success = False
        except Exception as exc:
            self._errors.append(f"Pre-execution failed: {exc}")
            self.logger.exception("_before_execute raised", error=str(exc))
            success = False

        # Phase 3: core business logic — the subclass override point
        try:
            if not self._execute():
                success = False
        except Exception as exc:
            self._errors.append(f"Execution failed: {exc}")
            self.logger.exception("_execute raised", error=str(exc))
            success = False

        # Phase 4: post-execution cleanup
        try:
            if not self._after_execute():
                success = False
        except Exception as exc:
            self._errors.append(f"Post-execution failed: {exc}")
            self.logger.exception("_after_execute raised", error=str(exc))
            success = False

        # Phase 5: audit, structured output, footer — always runs
        self._finalize(success=success, show_footer=self.SHOW_CHROME)
        return success

    def _execute(self) -> bool:
        """Phase 3 — core business logic.

        Override in subclasses. Return True on success, False on failure (add
        details to self._errors). Raise exceptions for unexpected errors —
        execute() will catch and record them.
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} must implement _execute()."
        )
```

### Standard command pattern

Commands implement only `_execute()`. No `execute()` override, no phase-wiring
boilerplate, no risk of forgetting `_finalize`:

```python
class StatusDeployCommand(BaseCommand):
    OPERATION = "deploy_status"

    def __init__(self, ...) -> None:
        super().__init__(...)
        ...

    def _execute(self) -> bool:
        # All business logic here.
        ...
        return True
```

### Phase customisation pattern

Commands that need different initialisation (e.g. `strata sln init` which must skip
the `.strata/` existence check) override only the phase that differs. The flow is still
guaranteed by `BaseCommand.execute()`:

```python
class InitSlnCommand(BaseCommand):
    OPERATION = "sln_init"

    def _initialize(self, show_header: bool = True) -> bool:
        # Custom initialisation that does NOT check for solution.json.
        self._start_time = datetime.now()
        self._configure_session_logging()
        if show_header and self._is_console_output():
            self.show_console_header()
        return True

    def _execute(self) -> bool:
        return self._create_workspace()
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

    def execute(self) -> bool:
        try:
            return self._execute()
        except Exception as exc:
            self._errors.append(str(exc))
            return False

    def _execute(self) -> bool:
        raise NotImplementedError
```

> `ValidateCommand` is a natural candidate for `StatelessCommand`: it requires a file
> path, not a workspace. The current `INIT_REQUIRED = False` flag plus workspace wiring
> adds complexity for no benefit.

## Migration Plan

The change is backward compatible for commands that implement `_run()` — rename
`_run()` to `_execute()` in each command. Applied incrementally:

1. **Phase 1 — Base class changes** (single PR):
   - Rewrite `execute()` in `base_command.py` as the always-run orchestrator (five
     phases, per-phase try/except, no short-circuit).
   - Add `_execute()` with a `raise NotImplementedError` default.
   - Remove `_run_phases()` helper — no longer needed.
   - All existing commands that implement `_run()`: rename method to `_execute()`.
   - All existing commands that override `execute()` directly: convert override logic
     to phase overrides (`_initialize`, `_before_execute`, etc.) and rename business
     logic to `_execute()`.
   - **No behavioral change for standard commands.** Tests pass without modification.

2. **Phase 2 — Eliminate `INIT_REQUIRED`** (per command group PR):
   - Commands that set `INIT_REQUIRED = False` should override `_initialize()` to skip
     the solution.json existence check instead.
   - Migrate purely stateless commands (`strata version`, `strata schema`) to
     `StatelessCommand`.
   - Remove `INIT_REQUIRED` from `BaseCommand` once all usages are converted.

3. **Phase 3 — Enforce via linting** (CI addition):
   - Add a check: no `BaseCommand` subclass may define `execute()` (reserved for the
     base). This prevents future authors from re-introducing the skip-finalize problem.

## Consequences

**Good:**
- `_finalize` (audit + structured output) is structurally guaranteed on every exit
  path. It cannot be skipped because it is always the last statement in the concrete
  `BaseCommand.execute()`. No per-command boilerplate required.
- Phase infrastructure (timing, logging, config) always runs — a `_execute()` that
  raises unexpectedly will still produce a full audit entry and structured output
  envelope.
- `INIT_REQUIRED = False` class variables are eliminated over time; commands that need
  no workspace context opt out structurally by using `StatelessCommand`.
- Commands are minimal: implement `_execute()` and nothing else for the standard case.
- Per-phase try/except produces specific, actionable error messages from each failing
  phase rather than a single first-failure message with subsequent phases silenced.

**Bad / Trade-offs:**
- `_execute()` runs even when `_initialize()` failed. Phase implementations must be
  resilient to partial setup. In practice, `_execute()` will also fail quickly and add
  its own error — both errors are reported in `_finalize`, giving the user full context.
- Subclasses can no longer override `execute()` for non-standard flows. Deviations must
  be expressed as phase overrides (`_initialize`, `_before_execute`, etc.). This is the
  correct constraint — it prevents the original problem of accidentally skipping finalize.

## Reference: Sterling Implementation

The sterling codebase (same author, same layered-architecture pattern) uses Option B:
abstract `BaseCommand` with an explicit `execute()` in each command. Strata diverges
from this with Option D, trading per-command lifecycle visibility for structural
`_finalize` guarantees and zero boilerplate.

Sterling patterns that still apply in Option D:
- `_emit_stage / _emit_task_line / _emit_summary_line` console helper methods —
  carried over unchanged.
- Phase override for non-standard initialisation (`init_command.py` overrides
  `_initialize()`) — identical pattern.
- Stateless commands as plain classes with a minimal `execute()` — same concept,
  expressed as `StatelessCommand` in strata.

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
