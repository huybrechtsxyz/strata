# Five exit codes: 0 success, 1 system, 2 usage, 3 validation, 4 lock conflict

- Status: implemented
- Date: 2025-07-16
- Extended: 2026-07-17 (ADR-0020 added exit code 4 — lock conflict)
- Implementation completed: 2026-07-17 (all 4 steps, 10 tests passing)

## Context and Problem Statement

strata runs in CI pipelines where the exit code is the primary signal of success or failure. Different failure modes require different responses from the pipeline: a usage error (wrong flag) should fail fast and loudly; a validation failure may warrant a different notification or blocking PR merge; a system crash is a bug.

Click provides exit code `0` (success) and `2` (usage error) by default. Should strata add additional exit codes, and if so, which ones?

## Considered Options

- **Two codes** — `0` success, non-zero failure (Click default: `2` for all errors)
- **Three codes** — `0` success, `1` any failure, `2` usage error
- **Four codes** — `0` success, `1` system failure, `2` usage error, `3` validation failure
- **Five codes** — `0` success, `1` system failure, `2` usage error, `3` validation failure, `4` lock conflict
- **Many codes** — one code per error type (e.g., `5` = missing file, `6` = auth failure)

## Decision Outcome

Chosen: **Five codes** (originally four; exit code `4` added by ADR-0020), because lock conflicts have a distinct operational response — retry after delay — that differs from all other failure modes. A CI pipeline must not conflate a lock conflict (transient, safe to retry) with a system failure (alert required) or a validation failure (config fix required).

The five codes and their intended CI responses:

| Code | Meaning                                        | CI response                                                    |
| ---- | ---------------------------------------------- | -------------------------------------------------------------- |
| `0`  | Success                                        | Proceed                                                        |
| `1`  | System failure (crash, permissions, timeout)   | Alert — file a bug                                             |
| `2`  | Usage error (bad arguments, file not found)    | Fix the script                                                 |
| `3`  | Validation failure (schema, cross-ref)         | Fix the config, block PR                                       |
| `4`  | Lock conflict (another deployment in progress) | Retry after delay — **`deploy run` and `deploy destroy` only** |

### Consequences

- Good: CI pipelines can distinguish `exit 3` (validation failed — notify and block PR) from `exit 1` (strata crashed — file a bug) without parsing log output.
- Good: `strata validate` returning `exit 3` in a GitHub Actions step with `continue-on-error: true` lets the workflow collect results before deciding whether to fail.
- Good: Click's `UsageError` already uses `2` — no override needed for usage errors.
- Good: `exit 4` allows CI pipelines to implement automatic retry-with-backoff for lock conflicts without treating them as failures.
- Good: The five codes are documented in `docs/platform/exit-codes.md` and surfaced in `--help` text via Click `epilog=`.
- Bad: More exit codes to document and remember than a simple zero/non-zero convention.
- Bad: Distinguishing `1` from `3` requires the command to catch and classify exceptions correctly — discipline in error handling is required.
- Bad: Exit code `4` is only valid for `deploy run` and `deploy destroy`; other commands must never return `4` even if they encounter a lock.

## Pros and Cons of the Options

### Two codes

- Good: Simple — "zero means good, anything else means bad".
- Bad: A CI step cannot distinguish "my YAML has a typo" from "strata segfaulted" without reading logs.

### Three codes

- Good: Adds `1` as a general failure alongside Click's `2`.
- Bad: Still merges validation failures (expected, actionable) with system failures (unexpected, requires debugging).

### Many codes

- Bad: Exit codes above 3 are rarely checked by CI tools, and the maintenance burden of keeping a large code table consistent is high.
- Bad: Pipelines typically check `== 0` or `!= 0` — granular codes beyond three or four are rarely actionable in practice.

## More Information

`handle_command_exit(command, success)` in `cli_common.py` maps command outcomes to the correct exit code. All commands use this function — `sys.exit()` is never called directly.

Exit code `3` specifically is used by: `strata validate`, `strata deploy health` (when health checks fail), and `strata build plan` (when plan shows changes in strict mode).

Exit code `4` is used exclusively by `strata deploy run` and `strata deploy destroy` when a deployment lock is already held by another process. Implementation: raise `LockConflictError` in the lock-acquisition path; the top-level error handler catches it and calls `sys.exit(4)`. No other commands may return exit code `4`.

Related: [Exit codes reference](../platform/exit-codes.md), [CI Integration](../platform/ci-integration.md), [ADR-0020 CLI Parameter Consistency Standard](0020-cli-parameter-consistency-standard.md)

## Implementation Plan — Exit Code 4

Three files, strictly ordered.

### Context

`LockTimeoutError` already exists in `base_lock_backend.py` and IS the lock conflict scenario. The problem is signal loss: `_acquire_lock()` in `base_deploy_command.py` catches `LockTimeoutError`, returns `None`, and the failure type is indistinguishable from any other `None` return by the time `handle_command_exit` is called. Exit code 1 is currently emitted instead of 4.

### Step 1 — Add `LockConflictError` to `base_lock_backend.py`

**File:** `src/strata/integrations/lock/base_lock_backend.py`

Add a new parent class between `PlatformError` and `LockTimeoutError`:

```python
class LockConflictError(PlatformError):
    """Raised when a deployment lock cannot be acquired because it is held.

    Parent of ``LockTimeoutError``. Catch this class to handle all lock-conflict
    scenarios without knowing the specific failure mode.
    """
    pass
```

Change `LockTimeoutError` to extend `LockConflictError` instead of `PlatformError`:

```python
class LockTimeoutError(LockConflictError):   # was: PlatformError
    ...
```

`LockBackendError` stays as `PlatformError` — it signals the lock backend itself is broken (unreachable, permissions error), not a lock conflict. These are two separate failure modes: one is transient and retry-safe (conflict); the other is a system error.

### Step 2 — Add `_lock_conflict` flag to `BaseDeployCommand`

**File:** `src/strata/commands/deploy/base_deploy_command.py`

Add to `__init__`:
```python
self._lock_conflict: bool = False
```

Add method alongside `has_validation_errors` patterns:
```python
def has_lock_conflict(self) -> bool:
    """True when the last execute() failed due to a deployment lock conflict."""
    return self._lock_conflict
```

In `_acquire_lock()`, change the `except LockTimeoutError` clause to catch `LockConflictError` and set the flag:

```python
# Before:
except LockTimeoutError as exc:
    self._errors.append(str(exc))
    ...
    return None

# After:
except LockConflictError as exc:
    self._lock_conflict = True       # ← NEW: signal to handle_command_exit
    self._errors.append(str(exc))
    if self._is_console_output():
        holder = getattr(exc, "holder", "unknown")
        click.echo(
            f"\n🔒  Could not acquire lock — held by {holder!r}. "
            "Run `strata deploy lock status` for details, or use --force-lock to override."
        )
    self.logger.error("deploy_lock_conflict", error=str(exc))
    return None
```

Also import `LockConflictError` in the import block alongside `LockTimeoutError`.

### Step 3 — Update `handle_command_exit` in `cli_common.py`

**File:** `src/strata/commands/cli_common.py`

Add a lock-conflict check **before** the validation-error check:

```python
def handle_command_exit(command, success: bool) -> None:
    # Lock conflict takes priority — checked before validation errors
    if not success and hasattr(command, "has_lock_conflict") and command.has_lock_conflict():
        raise click.exceptions.Exit(4)

    # Mark as failure if there are validation errors
    if success and hasattr(command, "has_validation_errors") and command.has_validation_errors():
        success = False

    if not success:
        if hasattr(command, "has_validation_errors") and command.has_validation_errors():
            raise click.exceptions.Exit(3)
        else:
            raise click.exceptions.Exit(1)
```

No changes needed to `cli_deploy.py` — it already calls `handle_command_exit(command, success)` for both `deploy run` and `deploy destroy`.

### Step 4 — Tests

**New test file:** `tests/strata/commands/deploy/test_exit_code_4.py`

Three scenarios to cover:

| Test                              | Setup                                                               | Expected exit |
| --------------------------------- | ------------------------------------------------------------------- | ------------- |
| `test_lock_conflict_exits_4`      | `_acquire_lock` returns `None` and `has_lock_conflict()` is `True`  | `sys.exit(4)` |
| `test_lock_backend_error_exits_1` | `_acquire_lock` returns `None` and `has_lock_conflict()` is `False` | `sys.exit(1)` |
| `test_successful_deploy_exits_0`  | `_acquire_lock` succeeds                                            | `sys.exit(0)` |

Use `pytest.raises(SystemExit)` and assert `.code == 4`, `.code == 1`, `.code == 0` respectively.

### Scope Constraint

Exit code 4 must **only** be reachable via `deploy run` and `deploy destroy`. The `has_lock_conflict()` method lives on `BaseDeployCommand`, not `BaseCommand`, so it is unreachable for all other command types. `handle_command_exit` uses `hasattr` guard — safe for all callers.
