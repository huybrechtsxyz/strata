"""Utility for masking secret values for safe display."""

from __future__ import annotations


def mask_secret(value: str, show: int = 4, char: str = "*") -> str:
    """Return a masked copy of *value* safe for display in logs or output.

    The first *show* characters are kept in clear; the remainder are replaced
    with *char*.  If the value is too short to safely reveal *show* characters
    (i.e. ``len(value) <= show``), the entire value is masked so that short
    secrets are never accidentally exposed in full.

    Args:
        value: The secret string to mask.
        show:  Number of leading characters to keep visible.  Default 4.
        char:  Replacement character for the masked portion.  Default ``*``.

    Returns:
        The masked string, always the same length as the input.
    """
    if len(value) <= show:
        return char * len(value)
    return value[:show] + char * (len(value) - show)
