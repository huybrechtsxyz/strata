"""Envelope-to-row extraction for the ingest endpoint (ADR-0065 Step 2.3).

The body arriving at ``POST /v1/events`` is always a CloudEvents 1.0 + ECS
envelope — ``AuditController._build_envelope()``'s output, which every event
passes through before reaching any sink, including the webhook sink step 2.5
points at this endpoint. It is never a raw artifact dump.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional


def _parse_time(raw: Optional[str]) -> datetime:
    """Parse the envelope's `time` field, falling back to now on any parse failure.

    Validation here is deliberately shallow (ADR-0065 step 2.3) — a timestamp
    format quirk should not reject an otherwise valid record.
    """
    if raw:
        try:
            return datetime.fromisoformat(raw)
        except ValueError:
            pass
    return datetime.now(timezone.utc)


def extract_row(envelope: Dict[str, Any]) -> Dict[str, Any]:
    """Map a CloudEvents+ECS envelope to an `events` table row.

    Raises ValueError if a required identity field (`type` / `data.labels.execution_id`)
    is missing — callers should translate that into a 400 response.
    """
    record_type = envelope.get("type")
    if not record_type or not isinstance(record_type, str):
        raise ValueError("Missing or invalid 'type' (record_type)")

    data = envelope.get("data") or {}
    labels = data.get("labels") or {}
    execution_id = labels.get("execution_id")
    if not execution_id or not isinstance(execution_id, str):
        raise ValueError("Missing or invalid 'data.labels.execution_id'")

    event = data.get("event") or {}

    return {
        "execution_id": execution_id,
        "record_type": record_type,
        "recorded_at": _parse_time(envelope.get("time")),
        "deployment": labels.get("deployment"),
        "workspace": labels.get("workspace"),
        "environment": labels.get("environment"),
        "tenant": labels.get("tenant"),
        # ring/strata_version: not populated by _build_envelope() yet — a future
        # producer-side change, not something this endpoint can backfill.
        "ring": labels.get("ring"),
        "action": event.get("action"),
        "outcome": event.get("outcome"),
        "strata_version": envelope.get("strata_version"),
        "payload": envelope,  # the complete envelope, verbatim
    }
