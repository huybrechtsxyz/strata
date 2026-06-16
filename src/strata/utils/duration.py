"""Duration string parser — converts human-readable durations to seconds."""

import re

_PATTERN = re.compile(
    r"^\s*"
    r"(?:(\d+)h)?"  # hours
    r"(?:(\d+)m)?"  # minutes
    r"(?:(\d+)s)?"  # seconds
    r"\s*$"
)


def parse_duration(value: str) -> int:
    """Parse a human-readable duration string into a total number of seconds.

    Supports ``h`` (hours), ``m`` (minutes), and ``s`` (seconds) suffixes in
    any combination.  At least one component must be present.

    Examples::

        parse_duration("30m")    # 1800
        parse_duration("8h")     # 28800
        parse_duration("2h30m")  # 9000
        parse_duration("60s")    # 60
        parse_duration("1h5m30s")  # 3930

    Args:
        value: Duration string such as ``"30m"``, ``"8h"``, ``"2h30m"``.

    Returns:
        Total duration in seconds (always a non-negative integer).

    Raises:
        ValueError: If *value* is empty, contains no recognised components,
            or uses an unrecognised format.
    """
    if not value or not value.strip():
        raise ValueError(f"Invalid duration: {value!r} — must not be empty")

    match = _PATTERN.match(value)
    if not match:
        raise ValueError(
            f"Invalid duration: {value!r} — expected a combination of h/m/s components, e.g. '30m', '8h', '2h30m'"
        )

    hours, minutes, seconds = match.groups()
    if hours is None and minutes is None and seconds is None:
        raise ValueError(f"Invalid duration: {value!r} — no recognised components (h/m/s).  Example: '30m'")

    return int(hours or 0) * 3600 + int(minutes or 0) * 60 + int(seconds or 0)
