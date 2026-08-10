"""ASGI application factory for the strata state-service server.

``GET /healthz`` exists from Step 2.1 onward; from Step 2.2 onward it also
verifies database connectivity via the engine passed to :func:`create_app`.
No ``/v1/events`` route exists yet (that's Step 2.3).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict

if TYPE_CHECKING:
    from fastapi import FastAPI
    from sqlalchemy.engine import Engine


def create_app(engine: "Engine") -> "FastAPI":
    """Build the FastAPI app. Requires ``pip install xyz-strata[server]``."""
    from fastapi import FastAPI, HTTPException

    from strata.server.db.engine import check_connection

    app = FastAPI(title="strata state service")

    @app.get("/healthz")
    def healthz() -> Dict[str, Any]:
        """Liveness check — also verifies the database is reachable (Step 2.2)."""
        ok, detail = check_connection(engine)
        if not ok:
            raise HTTPException(status_code=503, detail=detail)
        return {"status": "ok"}

    return app
