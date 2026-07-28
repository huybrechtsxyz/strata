# Subprocess execution consolidation — one path through `run_command()`, with SIGTERM and timeout parity

- Status: proposed
- Date: 2026-07-28

## Context and Problem Statement

During the global project review (`_lesson.md`, item D2), strata was found to have **at
least three independent subprocess execution implementations with real behavioral
drift, not just stylistic duplication**:

1. **`run_command()`'s buffered path** (`src/strata/utils/system.py`, the default —
   used when no `line_callback` and `show_output=False`) — calls plain
   `subprocess.run(command, ..., timeout=timeout, cwd=cwd)`. Has timeout support. **Does
   NOT register the process with the shutdown coordinator**
   (`register_process`/`deregister_process` from `shutdown_coordinator.py`, ADR-0028's
   SIGTERM graceful shutdown mechanism) — those calls only exist in the streaming path
   below.
2. **`run_command()`'s streaming path** (same file, used only when `line_callback` is
   set or `show_output=True`) — uses `subprocess.Popen` directly, and DOES call
   `register_process(proc)`/`deregister_process(proc)` around it, giving real
   SIGTERM-safe graceful shutdown per ADR-0028.
3. **`script_deployer.py`'s `_execute_script`** — a THIRD, independent implementation:
   calls `subprocess.run(cmd, cwd=..., env=..., capture_output=True, text=True,
   timeout=timeout)` directly, bypassing `run_command()` entirely. Has a timeout, but no
   shutdown-coordinator registration (same gap as #1).
4. **`terraform_builder.py`'s `_execute_format_script`** (the `format=script` build
   output profile) — a FOURTH implementation: calls `subprocess.run(["python",
   str(script_path)], env=env, capture_output=True, text=True)` with **no `timeout`
   parameter at all** — confirmed via direct code read. A hung user script here hangs
   the entire `strata build run` indefinitely. No shutdown-coordinator registration
   either.

Critical technical detail for the fix: `register_process()`/`deregister_process()` (in
`shutdown_coordinator.py`) are typed to accept a `subprocess.Popen` object specifically
— they cannot be used with the result of `subprocess.run()`, because `subprocess.run()`
does not expose the underlying `Popen` object to the caller before/during execution.
This means fixing `run_command()`'s buffered path is not a one-line change — it requires
converting that path's internals from `subprocess.run(...)` to an explicit
`subprocess.Popen(...)` + `.communicate(timeout=...)` (or equivalent), so the `Popen`
object exists and can be registered/deregistered, mirroring what the streaming path
already does. This is a real, moderate refactor, not a trivial fix.

Separately (`_lesson.md` item D3, same review): `run_command()` also has no
stdin-injection support today. This is what forced the secret-hashing cookbook
(documented earlier the same day) to bypass `run_command()` entirely and pipe stdin
through the user's own shell instead. Adding an `input: Optional[str] = None`
parameter is trivial for the buffered path — `subprocess.run(..., input=input)` /
`proc.communicate(input=input, timeout=timeout)` support it directly, no manual pipe
wiring needed. Since this ADR already converts the buffered path from
`subprocess.run()` to `Popen` + `.communicate()` for SIGTERM registration, adding
stdin support in the same change is close to free — bundled here rather than tracked
as a separate change, to avoid touching the same function twice.

This ADR records the recommended direction only. It does not implement anything.

## Decision Drivers

- Safety first: the `format=script` builder's missing timeout is a live hang risk today
  (item #4 above) — this needs fixing regardless of any broader consolidation.
- ADR-0028 (SIGTERM graceful shutdown, status: implemented) claims general coverage, but
  its actual coverage is narrower than advertised — only the less-commonly-used
  streaming path in `run_command()` has it. Consolidating other call sites onto
  `run_command()` as it stands today would NOT close this gap, only relocate it.
- Fix order matters: fixing `run_command()`'s own buffered-path gap must happen BEFORE
  migrating other call sites onto it, or the migration doesn't actually deliver the
  SIGTERM benefit it appears to promise.
- Minimize behavioral surprises for existing callers of `run_command()` — the
  buffered-path internals can change (Popen instead of `subprocess.run()`) as long as
  the public `CommandResult` contract (returncode/stdout/stderr/duration_ms) and
  timeout/error semantics stay identical.
- Bundle the D3 stdin-injection addition into this same buffered-path change rather
  than opening a second ADR for it — it's a mechanical addition (one new optional
  parameter), not a separate design decision, and the refactor already touches the
  exact code that would need to change for it.

## Considered Options

### Option 1 — Do nothing

- Con: the `format=script` missing timeout is a live, demonstrated hang risk; not
  acceptable to leave as-is.

**Rejected.**

### Option 2 — Migrate `script_deployer.py` and the `format=script` builder onto `run_command()` as it exists today, without first fixing `run_command()`'s buffered-path SIGTERM gap

- Con: insufficient. This would consolidate the code but not the safety guarantee; all
  call sites would still lack SIGTERM-safe shutdown, just via one shared function
  instead of three separate ones. Looks like a fix, isn't fully one.

**Rejected.**

### Option 3 — Fix `run_command()`'s buffered path first, then migrate the other call sites onto it (RECOMMENDED)

Fix `run_command()`'s buffered path first (convert to `Popen` +
`register_process`/`deregister_process`, matching the streaming path's pattern), THEN
migrate `script_deployer.py` and the `format=script` builder onto `run_command()`.

- Pro: closes the real gap (SIGTERM coverage) and the real bug (missing timeout) in one
  coordinated pass.
- Pro: leaves exactly one subprocess execution implementation in the codebase going
  forward.

**This is the winning option.**

### Option 4 — Build a new, from-scratch subprocess execution wrapper to replace `run_command()` entirely

- Con: unnecessary. `run_command()`'s public contract (`CommandResult`, timeout param,
  streaming mode) is sound; only its buffered-path internals need to change. No reason
  to introduce a second new abstraction when fixing the existing one is sufficient.

**Rejected.**

## Decision Outcome

Ship **Option 3**, in this order:

1. Fix `run_command()`'s buffered path to use `Popen` + process registration, verified
   against existing `run_command()` tests to ensure no behavior change in the public
   contract.
2. Fix the `format=script` builder's missing timeout as an independent, can-ship-
   immediately safety fix (does not need to wait for the full migration).
3. Migrate `script_deployer.py`'s `_execute_script` onto `run_command()`.
4. Migrate `terraform_builder.py`'s `_execute_format_script` onto `run_command()`
   (picking up both correct timeout enforcement and SIGTERM coverage in the same
   change).

Option 2 is explicitly rejected/superseded by Option 3 — recorded here so a future
contributor doesn't accidentally do the incomplete version.

### Consequences

- Good: closes the real, demonstrated hang risk in the `format=script` builder (no
  timeout at all today).
- Good: closes ADR-0028's actual SIGTERM coverage gap — the buffered path (the common
  case) gains the same graceful-shutdown behavior the streaming path already has,
  instead of the gap being silently relocated by a naive consolidation.
- Good: ends with exactly one subprocess execution implementation
  (`run_command()`) instead of four independently-drifting ones.
- Neutral: `run_command()`'s public contract (`CommandResult`, timeout parameter,
  streaming mode) does not change — only its buffered-path internals do.
- Bad (accepted): this is a real, moderate refactor of `run_command()`'s buffered
  path (subprocess.run → Popen + communicate), not a one-line change — it needs its
  own careful test verification before the migrations in Phases 3–4 proceed.
- Bad (accepted): `run_command()` may need a new `env` override parameter to support
  `script_deployer.py`'s migration (see Detailed Design) — a small scope addition
  discovered as part of this consolidation, not a blocker to the overall design.

## Detailed Design

- **`run_command()` buffered path**: replace `subprocess.run(command, ...,
  timeout=timeout, cwd=cwd)` with an explicit `subprocess.Popen(...)` +
  `proc.communicate(timeout=timeout)` (catching `subprocess.TimeoutExpired` the same
  way the streaming path already does — `proc.kill()` then re-`communicate()`/`wait()`),
  wrapped in `register_process(proc)` / `finally: deregister_process(proc)`. Preserve
  the exact existing `CommandResult` output shape and existing behavior for
  `check=True` (raises `CalledProcessError`) and the `FileNotFoundError` → returncode
  127 fallback.
- **Stdin injection (D3, bundled in)**: add `input: Optional[str] = None` to
  `run_command()`'s signature. Buffered path passes it straight to
  `proc.communicate(input=input, timeout=timeout)`. Streaming path only sets
  `stdin=subprocess.PIPE` when `input is not None`, writes and closes `proc.stdin`
  immediately after `Popen()` returns, before the drain threads start. The value must
  never appear in `cmd_display` or the `logger.debug("Executing command", ...)` call —
  only the argv is logged, `input` never is, for any caller (this matters for secret
  values passed via stdin).
- **`terraform_builder.py::_execute_format_script`**: add a `timeout` parameter (with a
  sensible default, e.g. reuse whatever default `run_command()` uses or a
  build-appropriate value — pick a reasonable default rather than leaving it unbounded)
  once migrated onto `run_command()`.
- **`script_deployer.py::_execute_script`**: replace its direct `subprocess.run(...)`
  call with a call to `run_command()`, passing through `cwd`, `env` (note: `run_command()`
  needs to support an `env` override if it doesn't already — check whether
  `run_command()` currently allows overriding environment variables, since
  `script_deployer.py` merges `self.resolved_values.as_compose_env()` plus `STRATA_*`
  vars into its subprocess env today; if `run_command()` doesn't support an env
  override param, that's an additional small addition needed as part of this
  migration, not a blocker to the overall design), and `timeout`.
- **Test impact**: existing `run_command()` unit tests must keep passing unchanged
  (public contract stability); add new tests for buffered-path SIGTERM registration
  (verify `register_process`/`deregister_process` are called), and for the
  `format=script` builder's new timeout enforcement (a script that sleeps past the
  timeout should now be killed and reported as a timeout, not hang the test suite).

## Implementation Phases

### Phase 1 (safety fix, can ship independently and immediately)

- Add a timeout to `terraform_builder.py::_execute_format_script` even before the
  broader migration — this is the most urgent piece per `_lesson.md` D2.

### Phase 2

- Fix `run_command()`'s buffered path (Popen + process registration), verify no
  regression against existing tests.
- Add `input` stdin-injection support in the same change (D3) — same buffered-path
  code, same Popen conversion, negligible incremental cost. Add a test confirming
  `input` is never logged.

### Phase 3

- Migrate `script_deployer.py::_execute_script` onto `run_command()` (may require
  adding an `env` override parameter to `run_command()` if one doesn't already exist).

### Phase 4

- Migrate `terraform_builder.py::_execute_format_script` onto `run_command()`,
  replacing the standalone Phase 1 timeout fix with the shared implementation.

## References

- `_lesson.md`, item D2 — the finding that seeded this ADR (subprocess execution
  duplicated across four call sites, with a confirmed missing-timeout bug in the
  `format=script` builder and a narrower-than-advertised SIGTERM coverage gap in
  `run_command()`'s own buffered path).
- [ADR-0027: Command timeout for long-running operations](0027-command-timeout-for-long-running-operations.md) —
  the existing timeout mechanism this ADR extends to the currently-unbounded
  `format=script` builder.
- [ADR-0028: SIGTERM graceful shutdown and lock release](0028-sigterm-graceful-shutdown-and-lock-release.md) —
  status "implemented," but this finding shows its actual coverage is narrower than
  that status suggests: only `run_command()`'s streaming path has
  `register_process`/`deregister_process` wrapping today, not the buffered path (the
  common case) or the two other independent `subprocess.run` call sites.
