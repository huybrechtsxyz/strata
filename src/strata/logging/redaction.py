#!/usr/bin/env python3
"""Redaction of sensitive values before anything is rendered to a log sink.

Matching is on the *key*, never the value — guessing at secret-shaped
strings produces false positives on commit SHAs and resource identifiers,
which strata's own log lines are full of. Runs as the last structlog
processor before the renderer, so no sink (console, file, Logstash, OTLP)
can receive a secret the pipeline saw.
"""

from typing import Any

from structlog.typing import EventDict, WrappedLogger

REDACTED = "***redacted***"

_MAX_DEPTH = 4

# Exact key names that are sensitive but too short or too common to match as
# substrings — "pat" would otherwise censor "path", "patch" and "pattern".
_SENSITIVE_EXACT = frozenset(
    {
        "auth",
        "authorization",
        "cookie",
        "pat",
        "pwd",
    }
)

# Fragments that are unambiguous wherever they appear, so "github_token" and
# "azure_client_secret" are caught without being enumerated.
_SENSITIVE_FRAGMENTS = (
    "api_key",
    "apikey",
    "connection_string",
    "credential",
    "passphrase",
    "password",
    "private_key",
    "secret",
    "token",
)

# A plural `tokens` is a **count** (e.g. an LLM/API usage metric), not a
# credential. `token` matched as a fragment is right for `github_token` and
# wrong for `input_tokens` — the discriminator is grammatical rather than an
# enumerated list, so `max_tokens`/`total_tokens` are covered by the same
# rule without being enumerated, and anything singular stays redacted (the
# failure direction stays "censor too much", not the reverse).
_COUNT_SUFFIX = "tokens"


def is_sensitive(key: str) -> bool:
    """Return whether a key name should have its value redacted."""
    lowered = key.lower()
    if lowered in _SENSITIVE_EXACT:
        return True
    if lowered == _COUNT_SUFFIX or lowered.endswith(f"_{_COUNT_SUFFIX}"):
        return False
    return any(fragment in lowered for fragment in _SENSITIVE_FRAGMENTS)


def _redact(value: Any, depth: int) -> Any:
    if depth >= _MAX_DEPTH:
        return value
    if isinstance(value, dict):
        return {key: REDACTED if is_sensitive(str(key)) else _redact(item, depth + 1) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        rebuilt = [_redact(item, depth + 1) for item in value]
        return tuple(rebuilt) if isinstance(value, tuple) else rebuilt
    return value


def censored(value: Any) -> Any:
    """Apply the same redaction the logger applies, for anything else that leaves the process.

    Exported so any other output path (e.g. a future debug/support bundle)
    doesn't grow a second, driftable list of sensitive key names.
    """
    return _redact(value, 0)


def censor_secrets(_logger: WrappedLogger, _method_name: str, event_dict: EventDict) -> EventDict:
    """structlog processor that replaces sensitive values with `REDACTED`.

    Nested dictionaries and sequences are walked to a bounded depth, because
    the realistic leak is a header map or provider payload nested inside a
    top-level field, not a top-level key literally named `password`.
    """
    for key in list(event_dict.keys()):
        if is_sensitive(str(key)):
            event_dict[key] = REDACTED
        else:
            event_dict[key] = _redact(event_dict[key], 1)
    return event_dict
