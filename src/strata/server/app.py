"""ASGI application factory for the strata state-service server (ADR-0065 Step 2.1).

Only ``GET /healthz`` exists at this step — no ``/v1/events``, no database. Import
of ``fastapi`` happens inside :func:`create_app`, never at module scope, so this
module can itself be imported (e.g. by tests) without the optional ``server``
dependency installed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict

if TYPE_CHECKING:
    from fastapi import FastAPI


def create_app() -> "FastAPI":
    """Build the FastAPI app. Requires ``pip install xyz-strata[server]``."""
    from fastapi import FastAPI

    app = FastAPI(title="strata state service")

    @app.get("/healthz")
    def healthz() -> Dict[str, Any]:
        """Liveness check. Step 2.2 will extend this to also verify DB connectivity."""
        return {"status": "ok"}

    return app
