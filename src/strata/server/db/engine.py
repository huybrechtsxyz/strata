"""Engine creation and liveness check for the strata state-service server.

Deliberately framework-free at module scope — no top-level ``sqlalchemy`` import —
so this module can itself be imported without the optional ``server`` dependency
installed; the actual import happens lazily inside each function.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Tuple

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

# The three backends ADR-0065 Step 2.2 supports. sqlite is the zero-config
# default (Python stdlib sqlite3, no driver needed); postgresql/mssql are
# opt-in production backends requiring their own extra
# (server-postgres / server-mssql).
_SUPPORTED_DIALECTS = ("sqlite", "postgresql", "mssql")


def create_engine_from_url(url: str) -> "Engine":
    """Build a SQLAlchemy engine, rejecting any backend outside the three supported ones."""
    from sqlalchemy import create_engine
    from sqlalchemy.engine import make_url

    backend = make_url(url).get_backend_name()
    if backend not in _SUPPORTED_DIALECTS:
        raise ValueError(f"Unsupported database backend '{backend}'. Supported: {', '.join(_SUPPORTED_DIALECTS)}.")
    return create_engine(url)


def check_connection(engine: "Engine") -> Tuple[bool, str]:
    """Run a trivial `SELECT 1` to verify the database is reachable.

    Best-effort — never raises past this boundary; used by `/healthz` (step 2.2
    onward) to report a database outage the same way a process-down outage is
    already visible.
    """
    try:
        from sqlalchemy import text

        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True, ""
    except Exception as exc:  # noqa: BLE001 - liveness check, any failure means "not ok"
        return False, str(exc)
