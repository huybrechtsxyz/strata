#!/usr/bin/env python3
"""Failure classification — shared vocabulary for what went wrong.

Three kinds of failure, because they need three different responses:

- `UsageError` — the caller asked for something impossible (wrong directory,
  bad argument). Fix the invocation.
- `ValidationError` — input was read and understood, but is invalid. Fix the
  configuration.
- `SystemError` — the environment failed (I/O, permissions). Fix the machine.

Deliberately carries **no exit codes**. The classification is domain
knowledge; turning it into a process exit status is one front-end's concern,
and v1 already had others — `strata serve` and `strata mcp` — which would want
HTTP statuses rather than exit codes. `strata.commands.exit_codes` owns that
mapping, so controllers can raise these without knowing what a process
returns.
"""

from strata.utils.diagnostics import Diagnostics


class StrataError(Exception):
    """Base class for every failure strata raises deliberately."""


class UsageError(StrataError):
    """The command or call was made wrongly — including outside a solution.

    Distinct from `ValidationError` on purpose: "there is no strata.yaml here"
    means the caller is in the wrong place, not that any document is invalid.
    Conflating them would make a mistyped path look like broken configuration,
    and would push a non-finding into the findings output.
    """


class ValidationError(StrataError):
    """Documents were read and found invalid.

    Carries the findings so the caller can render them before deciding what to
    do — which is why failures are raised rather than returned as booleans.
    """

    def __init__(self, diagnostics: Diagnostics, message: str = "Validation failed"):
        super().__init__(message)
        self.diagnostics = diagnostics


class SystemError(StrataError):
    """The environment failed — unreadable path, I/O, permissions."""
