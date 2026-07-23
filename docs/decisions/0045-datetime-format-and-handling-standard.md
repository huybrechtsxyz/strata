# Date / Time Format and Handling Standard

- Status: implemented
- Date: 2026-07-20
- Implemented: 2026-07-23

## Context and Problem Statement

strata emits timestamps in CLI output, accepts them as CLI arguments (`--since`), stores them in build artifacts, and logs them in audit records. Without a single standard, different parts of the codebase use:

- `datetime.now()` — naive datetime (no timezone, breaks UTC comparisons)
- `datetime.now(timezone.utc).isoformat()` — UTC ISO 8601 with `+00:00` suffix
- `datetime.utcnow()` — deprecated in Python 3.12+, still naive
- Various display formats in console output (no consistent shape)

Operators and integrations (CI/CD, SIEM, AI agents) need to parse, filter, and sort timestamps reliably. Ambiguous or missing timezone information causes silent bugs in time-range queries (`--since`), audit log filtering, and cross-timezone reporting.

## Considered Options

### Option A: Python's `datetime.isoformat()` freeform
- Use whatever Python produces; no enforcement
- **Rejected:** produces `+00:00` vs. `Z` vs. no timezone depending on how the object was created; unpredictable for consumers

### Option B: Unix epoch integers
- Store and transmit timestamps as epoch seconds
- **Rejected:** not human-readable in CLI output; requires post-processing for every display; conflicts with ISO 8601-based standards (CycloneDX, SARIF, OpenTelemetry)

### Option C: ISO 8601 with UTC-always rule (CHOSEN)
- Always produce and accept ISO 8601 with an explicit timezone
- UTC is the canonical internal representation; display may humanise for console output
- Consistent across CLI input, JSON output, artifact files, and audit logs

## Decision Outcome

Chosen: **Option C — ISO 8601, UTC-always**.

---

## Standard

### Internal representation

All timestamps MUST be **timezone-aware UTC** `datetime` objects:

```python
# ✅ correct
from datetime import datetime, timezone
now = datetime.now(timezone.utc)

# ❌ wrong — naive (no timezone)
now = datetime.now()

# ❌ wrong — deprecated in Python 3.12+, still naive
now = datetime.utcnow()
```

### Wire format (JSON output, artifact files, audit log)

All serialised timestamps MUST use ISO 8601 **with explicit UTC offset**:

```
2026-07-20T14:30:00+00:00
```

Use `datetime.isoformat()` on a UTC-aware object — this always produces `+00:00`.  
Do **not** manually format with `strftime` or substitute `Z` for `+00:00`; the `Z` suffix requires Python 3.11+ `datetime.fromisoformat()` to parse reliably, so `+00:00` is preferred for maximum compatibility.

```python
# ✅ correct wire format
ts = datetime.now(timezone.utc).isoformat()
# → "2026-07-20T14:30:00.123456+00:00"
```

### CLI input (`--since TIMESTAMP`, future `--until TIMESTAMP`)

- Accept **ISO 8601 strings with timezone**, e.g.:
  - `2026-07-20T14:30:00+00:00`
  - `2026-07-20T14:30:00Z`  *(Z accepted on input only — normalise to `+00:00` internally)*
  - `2026-07-20` *(date-only: interpreted as `2026-07-20T00:00:00+00:00`)*
- **Reject** bare datetimes without timezone — raise `click.BadParameter` (exit 2) with an actionable message:
  > `"'2026-07-20T14:30:00' has no timezone. Use ISO 8601 with offset, e.g. 2026-07-20T14:30:00+00:00 or 2026-07-20T14:30:00Z."`
- Parse with the shared helper `parse_iso_timestamp(value: str) -> datetime` (see Implementation section)
- Type annotation in help text: `TIMESTAMP`

### CLI console display

For human-readable (`--output console`) output, timestamps SHOULD be displayed in a concise local-friendly form. The recommended format is:

```
2026-07-20 14:30:00 UTC
```

Use the shared helper `format_display_timestamp(dt: datetime) -> str`.

For `--output text` and `--output json`, always use the full ISO 8601 wire format.

### Type annotation in help strings

Use the metavar `TIMESTAMP` consistently:

```python
@click.option("--since", metavar="TIMESTAMP", help="Show entries since ISO 8601 timestamp (e.g. 2026-07-20T00:00:00+00:00).")
```

---

## Implementation

Add to `src/strata/utils/datetime_utils.py`:

```python
"""Shared date/time utilities for the strata CLI.

All timestamps are UTC-aware. Parsing accepts ISO 8601 with explicit
timezone; display formats are separated by output mode.
"""

from __future__ import annotations

from datetime import datetime, timezone

import click


def now_utc() -> datetime:
    """Return current time as a timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)


def parse_iso_timestamp(value: str) -> datetime:
    """Parse an ISO 8601 timestamp string, requiring an explicit timezone.

    Accepts:
        2026-07-20T14:30:00+00:00
        2026-07-20T14:30:00Z          (normalised to +00:00)
        2026-07-20                    (interpreted as 00:00:00+00:00)

    Raises:
        click.BadParameter: if the string is missing a timezone or is not parseable.
    """
    # Normalise Z → +00:00 for Python < 3.11 compatibility
    normalised = value.rstrip("Z") + "+00:00" if value.endswith("Z") else value

    # Date-only shorthand
    if len(normalised) == 10:
        normalised = normalised + "T00:00:00+00:00"

    try:
        dt = datetime.fromisoformat(normalised)
    except ValueError:
        raise click.BadParameter(
            f"'{value}' is not a valid ISO 8601 timestamp. "
            "Use e.g. 2026-07-20T14:30:00+00:00 or 2026-07-20T14:30:00Z."
        )

    if dt.tzinfo is None:
        raise click.BadParameter(
            f"'{value}' has no timezone. "
            "Use ISO 8601 with offset, e.g. 2026-07-20T14:30:00+00:00 or 2026-07-20T14:30:00Z."
        )

    return dt.astimezone(timezone.utc)


def to_wire_timestamp(dt: datetime) -> str:
    """Serialise a datetime to the canonical wire format (ISO 8601, +00:00 suffix)."""
    return dt.astimezone(timezone.utc).isoformat()


def format_display_timestamp(dt: datetime) -> str:
    """Format a datetime for human-readable console output."""
    utc = dt.astimezone(timezone.utc)
    return utc.strftime("%Y-%m-%d %H:%M:%S UTC")
```

### Migration checklist

| Location                                   | Current                                  | Required action                             |
| ------------------------------------------ | ---------------------------------------- | ------------------------------------------- |
| `cli_audit.py` `--since`                   | raw `str`, no validation                 | wrap with `parse_iso_timestamp` callback    |
| `sbom_build_command.py`                    | `datetime.now()` (naive)                 | replace with `now_utc()`                    |
| `sbom_build_command.py` `"ts"` key         | `datetime.now(timezone.utc).isoformat()` | replace with `to_wire_timestamp(now_utc())` |
| `run_build_command.py` `_build_started_at` | `datetime.now(timezone.utc).isoformat()` | replace with `to_wire_timestamp(now_utc())` |
| `scan_deployments_command.py`              | `datetime.now(timezone.utc).isoformat()` | replace with `to_wire_timestamp(now_utc())` |
| Future `--until`, `--from-date` flags      | —                                        | must use `parse_iso_timestamp`              |

---

## Consequences

### Positive

- Operators and CI/CD scripts can reliably parse all timestamps from JSON output
- `--since` rejects ambiguous inputs immediately at the CLI boundary with a clear fix message
- Python `datetime.now(timezone.utc)` produces correct, comparable objects everywhere
- Compatible with ISO 8601-native formats: CycloneDX, SARIF, OpenTelemetry, SIEM ingestion

### Negative

- `sbom_build_command.py` currently uses naive `datetime.now()` internally — a one-time migration is required
- `Z` suffix (acceptable on input) is not produced on output; some tools expect `Z`; document the difference explicitly in the help text examples

## More Information

- [ISO 8601 Wikipedia](https://en.wikipedia.org/wiki/ISO_8601)
- [Python `datetime` docs](https://docs.python.org/3/library/datetime.html)
- [Python 3.11 `fromisoformat` `Z` support](https://docs.python.org/3/library/datetime.html#datetime.datetime.fromisoformat)
- Related ADRs: ADR-0020 (CLI parameter consistency — `TIMESTAMP` metavar), ADR-0018 (deployment audit traceability — audit log timestamps)
