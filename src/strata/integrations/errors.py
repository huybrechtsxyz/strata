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


class CheckPhaseUnsupportedError(IntegrationError):
    """An integration has no namespace-scoped check phase (`InfraIntegration.check_namespace()`).

    A subclass, so `deploy_run()` can treat exactly this as "nothing to check" while any other
    `IntegrationError` raised during a check stays a real failure.
    """


class RegistryAuthError(IntegrationError):
    """Authenticating to a chart registry with the configured credentials failed (ADR-0042).

    Never carries the password: callers scrub it before raising.
    """


class ValueResolutionError(Exception):
    """A store backend could not resolve a key: auth failure, network error,
    missing secret, or an unimplemented store type.

    Always carries a message safe to show a user — never the raw exception
    from an SDK/HTTP call, which may embed request details.
    """


class KeyValueConflictError(IntegrationError):
    """`KeyValueStoreIntegration.put(if_version=...)` lost a compare-and-
    swap race — another writer updated the key first (or, for the
    `NEW_KEY_VERSION` sentinel, the key already existed). The caller should
    re-read the current value and retry its own merge, never retry the
    same write unconditionally (that would silently discard the other
    writer's update).

    A subclass of `IntegrationError`, not a sibling — every existing
    `except IntegrationError` call site still catches this unchanged; a
    caller that specifically wants to retry on conflict catches this by
    name instead.
    """
