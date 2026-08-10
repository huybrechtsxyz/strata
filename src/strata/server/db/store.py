"""Idempotent event insertion for the strata state-service server (ADR-0065 Step 2.2).

Idempotency is insert-then-catch-duplicate, not dialect-specific upsert SQL:
``ON CONFLICT DO NOTHING`` (postgresql/sqlite) has no equivalent on SQL Server
without a per-insert ``MERGE`` statement. A plain insert whose integrity error
is caught and treated as a no-op is simpler and fully portable across all three
supported backends, and idempotency here only needs to be correct on retry —
not optimised for hot-path throughput.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine


def insert_event(engine: "Engine", row: Dict[str, Any]) -> bool:
    """Insert one event row. Returns True if inserted, False if it was a duplicate (no-op).

    `row` must at least contain `execution_id` and `record_type` (the primary key).
    """
    from sqlalchemy.exc import IntegrityError

    from strata.server.db.schema import events

    try:
        with engine.begin() as conn:
            conn.execute(events.insert().values(**row))
        return True
    except IntegrityError:
        return False
