"""Minimal read-tail query for the strata state-service server (ADR-0065 Step 2.6).

Deliberately narrow — the last N rows, optionally filtered by workspace, no
date ranges, no aggregation, no arbitrary filtering. This is not Phase 3's
(still deferred) read API; it exists solely to back the VS Code extension's
tail view (Step 2.7) with a `tail -f`-shaped answer to "what happened most
recently."
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine


def list_workspaces(engine: "Engine") -> List[str]:
    """Return the distinct, non-null workspace names seen in `events`, sorted alphabetically.

    A read-only projection, not a workspace registry — a workspace shows up here once it has
    sent at least one event, regardless of whether it has (or ever had) an ingest token. Backs
    the read-only dashboard's "Workspaces" view (no auth required for this query, unlike
    `/v1/tokens`, which lists *registered* tokens instead).
    """
    from sqlalchemy import select

    from strata.server.db.schema import events

    query = select(events.c.workspace).distinct().where(events.c.workspace.isnot(None)).order_by(events.c.workspace)

    with engine.connect() as conn:
        rows = conn.execute(query).scalars().all()

    return list(rows)


def list_recent_events(engine: "Engine", workspace: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
    """Return the most recent `limit` events, oldest-of-the-batch first, optionally scoped to one workspace.

    A lean projection, not the full stored `payload` — enough for a one-line-per-event tail
    view. The underlying query orders by `received_at` descending (to actually get the most
    *recent* rows under `LIMIT`), then the result is reversed to ascending order — the order a
    real `tail` shows lines in, and the order a UI can append to the bottom of a scrolling list
    without re-sorting.
    """
    from sqlalchemy import select

    from strata.server.db.schema import events

    columns = (
        events.c.execution_id,
        events.c.record_type,
        events.c.recorded_at,
        events.c.received_at,
        events.c.workspace,
        events.c.deployment,
        events.c.environment,
        events.c.action,
        events.c.outcome,
    )
    query = select(*columns).order_by(events.c.received_at.desc()).limit(limit)
    if workspace:
        query = query.where(events.c.workspace == workspace)

    with engine.connect() as conn:
        rows = conn.execute(query).mappings().all()

    return [
        {
            "execution_id": row["execution_id"],
            "record_type": row["record_type"],
            "recorded_at": row["recorded_at"].isoformat() if row["recorded_at"] else None,
            "received_at": row["received_at"].isoformat() if row["received_at"] else None,
            "workspace": row["workspace"],
            "deployment": row["deployment"],
            "environment": row["environment"],
            "action": row["action"],
            "outcome": row["outcome"],
        }
        for row in reversed(rows)
    ]
