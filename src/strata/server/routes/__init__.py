"""Route modules for the strata state-service server (ADR-0065/0067).

Split out of a single `app.py` once the route count grew past "a few closures
in one factory function" — FastAPI's own recommended shape for this
("Bigger Applications - Multiple Files"): one `APIRouter` per concern,
assembled by `create_app()` via `app.include_router(...)`.

Shared per-app-instance config (`engine`, `admin_token`, etc.) lives on
`app.state`, set once by `create_app()` — see `state.py` for typed accessors.
This is FastAPI's own recommended home for exactly this kind of singleton
config, so route modules never need to close over `create_app()`'s locals.
"""
