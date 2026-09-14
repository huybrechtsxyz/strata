#!/usr/bin/env python3
"""Integration-layer exceptions (secret/variable/feature store availability)."""

from typing import Optional

from strata.exceptions.base_exception import PlatformError


class SecretStoreUnavailableError(PlatformError):
    """Raised when a secret/variable/feature store cannot be reached or
    authenticated — as distinct from a key genuinely not existing in the store.

    Contract: ``get_secret()`` (and the analogous variable/feature methods)
    return ``None`` ONLY when the key does not exist. Any connectivity or
    authentication failure must raise this exception instead of returning
    ``None``. Callers (e.g. ``ValueController``) rely on this distinction to
    avoid unsafe fallback behaviour — in particular, never treat this the same
    as "missing" and trigger generate-on-missing secret creation.
    """

    def __init__(self, integration_name: str, reason: str, cause: Optional[Exception] = None):
        super().__init__(
            message=f"Store '{integration_name}' unavailable: {reason}",
            error_code="SECRET_STORE_UNAVAILABLE",
            details={"integration": integration_name, "reason": reason},
            cause=cause,
        )


class IntegrationResolutionError(PlatformError):
    """Raised when a workspace provisioner's ``integration:`` binding cannot be
    resolved to exactly one compatible, registered integration (ADR-0079).

    Covers three distinct cases, all surfaced with the same exception type so
    callers (``strata validate --deep`` and deploy-time validation) can treat
    them identically:

    - ``integration:`` is set but no integration is registered under that name.
    - ``integration:`` is set but the named integration is not an instance of
      the expected integration class (e.g. type mismatch).
    - ``integration:`` is unset and zero, or more than one, registered
      integration is compatible with the provisioner's type (ambiguous —
      never silently guessed).
    """

    def __init__(self, provisioner_name: str, reason: str):
        super().__init__(
            message=f"Provisioner '{provisioner_name}': {reason}",
            error_code="INTEGRATION_RESOLUTION_FAILED",
            details={"provisioner": provisioner_name, "reason": reason},
        )
