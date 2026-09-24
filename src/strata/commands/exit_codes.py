#!/usr/bin/env python3
"""Exit codes — the CLI's rendering of a failure classification.

The single declaration of what each code means, and the only place that turns
a `StrataError` into a process exit status. v1 spread this across three places
(a `cli.py` docstring, `handle_command_exit()`, and a re-stated `epilog` on
each command) and classified by probing the command object with
`hasattr(command, "has_validation_errors")` — untypeable, and extending it
meant editing a central if/elif chain.

Here the error type carries the classification (`strata.utils.errors`) and
this module maps it. The mapping is explicit rather than an attribute on the
exception because exit codes are one front-end's concern: v1 also shipped
`strata serve` and `strata mcp`, which want HTTP statuses from the same
errors. A test asserts every error type is mapped, so the table cannot
silently go stale.
"""

from strata.controllers.remote_resolution import RemoteResolutionError
from strata.controllers.source_sync import SourceSyncError
from strata.utils.errors import StrataError, SystemError, UsageError, ValidationError

#: Everything succeeded.
EXIT_SUCCESS = 0

#: System or execution failure — unreadable path, I/O, unexpected crash.
#: Alert-worthy: the environment is wrong, not the input.
EXIT_FAILURE = 1

#: Usage error — bad arguments, or run somewhere that is not a solution.
#: Click's own convention for argument errors, kept so scripts behave.
EXIT_USAGE = 2

#: Validation failure — input was read and understood, but is invalid.
#: The configuration needs fixing.
EXIT_VALIDATION = 3

#: Failure type -> process exit status. Declarative dispatch, like
#: `SERVICE_BY_KIND`: adding a failure means adding one entry, not editing
#: a branch chain.
EXIT_CODE_BY_ERROR: dict[type[StrataError], int] = {
    UsageError: EXIT_USAGE,
    ValidationError: EXIT_VALIDATION,
    SystemError: EXIT_FAILURE,
    RemoteResolutionError: EXIT_FAILURE,
    SourceSyncError: EXIT_FAILURE,
    StrataError: EXIT_FAILURE,
}


def exit_code_for(error: StrataError) -> int:
    """Return the process exit status for `error`.

    Walks the type's MRO so a future subclass inherits its parent's code
    rather than silently falling through to a generic failure.

    Args:
        error: The raised failure.

    Returns:
        The mapped exit code, defaulting to `EXIT_FAILURE`.
    """
    for candidate in type(error).__mro__:
        if candidate in EXIT_CODE_BY_ERROR:
            return EXIT_CODE_BY_ERROR[candidate]
    return EXIT_FAILURE
