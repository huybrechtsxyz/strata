#!/usr/bin/env python3
"""Name/list uniqueness helper.

Lives in `strata.utils` (below `strata.models` in the layered architecture,
ADR-0003): pure list logic, no Pydantic dependency, reused across nearly
every model file's `check_unique_*` validators.
"""


def check_unique_names(items: list[str], label: str) -> None:
    """Raise ``ValueError`` if `items` contains duplicate values.

    Uses O(n) set-based detection instead of the O(n^2) `.count()` pattern.
    The error message lists duplicates in sorted order for deterministic output.
    """
    seen: set[str] = set()
    dupes: set[str] = set()
    for item in items:
        if item in seen:
            dupes.add(item)
        seen.add(item)
    if dupes:
        raise ValueError(f"Duplicate {label}: {', '.join(sorted(dupes))}")
