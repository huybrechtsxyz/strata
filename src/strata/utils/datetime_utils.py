"""Shared date/time utilities for the strata CLI.

All timestamps are UTC-aware. Parsing accepts ISO 8601 with explicit
timezone; display formats are separated by output mode.

Usage::

    from strata.utils.datetime_utils import now_utc, to_wire_timestamp, format_display_timestamp, parse_iso_timestamp

    # Create
    ts = now_utc()

    # Serialize to JSON / artifact files
    wire = to_wire_timestamp(ts)         # "2026-07-20T14:30:00.123456+00:00"

    # Console display
    display = format_display_timestamp(ts)  # "2026-07-20 14:30:00 UTC"

    # Parse CLI input (--since flag etc.)
    dt = parse_iso_timestamp("2026-07-20T00:00:00Z")
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

import click


def now_utc() -> datetime:
    """Return the current time as a timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)


def to_wire_timestamp(dt: datetime) -> str:
    """Serialise a datetime to the canonical wire format (ISO 8601, +00:00 suffix).

    Always produces ``2026-07-20T14:30:00.123456+00:00``.
    """
    return dt.astimezone(timezone.utc).isoformat()


def format_display_timestamp(dt: datetime) -> str:
    """Format a datetime for human-readable console output.

    Produces ``2026-07-20 14:30:00 UTC``.
    """
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def parse_iso_timestamp(value: str) -> datetime:
    """Parse an ISO 8601 timestamp string, requiring an explicit timezone.

    Accepts:
        - ``2026-07-20T14:30:00+00:00``
        - ``2026-07-20T14:30:00Z``  (normalised internally to ``+00:00``)
        - ``2026-07-20``            (interpreted as ``2026-07-20T00:00:00+00:00``)

    Raises:
        click.BadParameter: if the string lacks a timezone or is not parseable.
    """
    normalised = value

    # Date-only shorthand
    if len(normalised) == 10:
        normalised = normalised + "T00:00:00+00:00"

    # Normalise Z → +00:00 for Python < 3.11 compatibility
    if normalised.endswith("Z"):
        normalised = normalised[:-1] + "+00:00"

    try:
        dt = datetime.fromisoformat(normalised)
    except ValueError as exc:
        raise click.BadParameter(
            f"'{value}' is not a valid ISO 8601 timestamp. Use e.g. 2026-07-20T14:30:00+00:00 or 2026-07-20T14:30:00Z."
        ) from exc

    if dt.tzinfo is None:
        raise click.BadParameter(
            f"'{value}' has no timezone. "
            "Use ISO 8601 with offset, e.g. 2026-07-20T14:30:00+00:00 or 2026-07-20T14:30:00Z."
        )

    return dt.astimezone(timezone.utc)


def coerce_to_utc(dt: Optional[datetime]) -> Optional[datetime]:
    """Ensure a datetime is UTC-aware; returns None if input is None.

    Naive datetimes are assumed to be UTC and tagged accordingly.
    Aware datetimes are converted to UTC.
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)
