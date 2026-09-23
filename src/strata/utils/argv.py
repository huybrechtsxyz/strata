#!/usr/bin/env python3
"""Rendering the command line safely.

Any place that echoes the invocation — a console header, an audit record, a
crash banner — is a place a secret can leak, because some commands take
secret material as arguments (`strata secret put KEY --value <plaintext>`).
v1 hit exactly this and added masking after the fact; doing it here means
every future echo of argv is safe by construction.

Sensitivity is decided by `strata.logging.redaction.is_sensitive`, the same
rule that censors log fields, so there is one list of what counts as secret
rather than one per call site.
"""

import sys

from strata.logging.redaction import REDACTED, is_sensitive

#: Flags whose *name* is innocuous but whose value is secret in context.
#: `--value` only means a secret for `secret put`, so it cannot be added to
#: the general key rule without censoring unrelated log fields.
_SENSITIVE_FLAGS = frozenset({"value"})


def _flag_is_sensitive(token: str) -> bool:
    """True when `token` is an option whose value must be masked."""
    if not token.startswith("-"):
        return False
    name = token.split("=", 1)[0].lstrip("-").replace("-", "_")
    return name in _SENSITIVE_FLAGS or is_sensitive(name)


def redact_argv(argv: list[str]) -> list[str]:
    """Return a copy of `argv` with secret option values masked.

    Handles both spellings: `--token abc` (two tokens) and `--token=abc`
    (one). A positional value is only masked when it directly follows a
    sensitive flag, so ordinary arguments are untouched.

    Args:
        argv: Raw tokens, typically `sys.argv`.

    Returns:
        A new list; the input is not modified.
    """
    redacted: list[str] = []
    mask_next = False

    for token in argv:
        if mask_next:
            redacted.append(REDACTED)
            mask_next = False
            continue

        if _flag_is_sensitive(token):
            if "=" in token:
                redacted.append(f"{token.split('=', 1)[0]}={REDACTED}")
            else:
                redacted.append(token)
                mask_next = True
            continue

        redacted.append(token)

    return redacted


def command_line(argv: list[str] | None = None) -> str:
    """Return the invocation as a single redacted string.

    The program is reported as `strata` rather than its resolved path, which
    is what the user typed and what they would retype to reproduce.
    """
    tokens = list(sys.argv if argv is None else argv)
    if tokens:
        tokens = ["strata", *tokens[1:]]
    return " ".join(redact_argv(tokens))
