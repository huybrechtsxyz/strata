# Four exit codes: 0 success, 1 system, 2 usage, 3 validation

- Status: accepted
- Date: 2025-07-16

## Context and Problem Statement

strata runs in CI pipelines where the exit code is the primary signal of success or failure. Different failure modes require different responses from the pipeline: a usage error (wrong flag) should fail fast and loudly; a validation failure may warrant a different notification or blocking PR merge; a system crash is a bug.

Click provides exit code `0` (success) and `2` (usage error) by default. Should strata add additional exit codes, and if so, which ones?

## Considered Options

- **Two codes** — `0` success, non-zero failure (Click default: `2` for all errors)
- **Three codes** — `0` success, `1` any failure, `2` usage error
- **Four codes** — `0` success, `1` system failure, `2` usage error, `3` validation failure
- **Many codes** — one code per error type (e.g., `4` = missing file, `5` = auth failure)

## Decision Outcome

Chosen: **Four codes**, because the distinction between a system crash (`1`), a bad CLI invocation (`2`), and a processed-but-invalid config file (`3`) is operationally significant and can be acted on differently in CI without parsing output.

### Consequences

- Good: CI pipelines can distinguish `exit 3` (validation failed — notify and block PR) from `exit 1` (strata crashed — file a bug) without parsing log output.
- Good: `strata validate` returning `exit 3` in a GitHub Actions step with `continue-on-error: true` lets the workflow collect results before deciding whether to fail.
- Good: Click's `UsageError` already uses `2` — no override needed for usage errors.
- Good: The four codes are documented in `docs/platform/exit-codes.md` and surfaced in `--help` text.
- Bad: More exit codes to document and remember than a simple zero/non-zero convention.
- Bad: Distinguishing `1` from `3` requires the command to catch and classify exceptions correctly — discipline in error handling is required.

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

Related: [Exit codes reference](../platform/exit-codes.md), [CI Integration](../platform/ci-integration.md)
