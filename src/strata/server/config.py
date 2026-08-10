"""Runtime configuration and bind-safety rule for the strata state-service server.

Deliberately framework-free — no ``fastapi``/``uvicorn`` import here — so the
loopback-or-TLS rule (ADR-0065 Step 2.1) can be unit tested without the optional
``server`` dependency installed.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# Hosts considered loopback-only — a bind to any of these never leaves the local
# machine, so TLS is not mandatory for it the way it is for any other bind address.
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


@dataclass(frozen=True)
class ServerRuntimeConfig:
    """Host/port/TLS configuration for one ``strata serve run`` invocation.

    Deliberately not a workspace YAML model (no ``spec.state_service`` block) —
    the state service is operational config for a standalone process shared
    across many workspaces, not configuration of any one deployment (ADR-0065).
    """

    host: str
    port: int
    tls_cert: Optional[Path] = None
    tls_key: Optional[Path] = None

    def is_loopback(self) -> bool:
        """Return True if `host` never leaves the local machine."""
        return self.host in _LOOPBACK_HOSTS

    def has_tls(self) -> bool:
        """Return True if both a cert and key are configured."""
        return self.tls_cert is not None and self.tls_key is not None

    def validate_bind(self) -> Optional[str]:
        """Return an error message if this bind is unsafe, else None.

        The service must refuse to start on a non-loopback bind without TLS
        (ADR-0065 Step 2.1) — this is a property of the process's bind, not of
        any one route, so it is enforced here, before the server ever starts.
        """
        if not self.is_loopback() and not self.has_tls():
            return (
                f"Refusing to bind to non-loopback host '{self.host}' without TLS. "
                "Provide --tls-cert and --tls-key, or bind to 127.0.0.1/::1/localhost."
            )
        return None
