#!/usr/bin/env python3
"""Failure types for the integration layer (ADR-0021)."""


class IntegrationError(Exception):
    """An integration is misconfigured or fails a precondition check.

    Distinct from `strata.utils.transport.TransportResult` failures, which
    are *outcomes* a caller decides about (a command exit code, an HTTP
    status) — this is for assertions about the integration itself before any
    of that runs: an unsupported transport was requested, or the installed
    version doesn't satisfy what a Provisioner expects (ADR-0021 D4).
    """


class ValueResolutionError(Exception):
    """A store backend could not resolve a key: auth failure, network error,
    missing secret, or an unimplemented store type.

    Always carries a message safe to show a user — never the raw exception
    from an SDK/HTTP call, which may embed request details.
    """
