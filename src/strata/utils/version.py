from importlib.metadata import version
from typing import Optional

import requests


def get_version() -> str:
    """This function gets the version as defined in the `VERSION.txt` file.

    Returns:
        str: The version as a string.
    """
    return version("xyz-strata")


def check_for_updates() -> tuple[Optional[str], bool]:
    """Check if a newer version is available on PyPI.

    Queries PyPI API for the latest version of xyz-strata and compares
    it with the currently installed version.

    Returns:
        tuple: (latest_version, update_available)
               - latest_version is None if the check fails
               - update_available is True only if a newer version exists and was successfully retrieved

    Raises:
        No exceptions — all failures are caught and handled gracefully.
    """
    try:
        response = requests.get("https://pypi.org/pypi/xyz-strata/json", timeout=3)
        response.raise_for_status()
        latest = response.json()["info"]["version"]
        current = get_version()

        # Simple version comparison: split by dots and compare numerically
        # e.g., "1.2.3" > "1.2.1" or "1.2.3" < "2.0.0"
        current_parts = [int(x) for x in current.split(".") if x.isdigit()]
        latest_parts = [int(x) for x in latest.split(".") if x.isdigit()]

        # Pad shorter version with zeros for comparison
        max_len = max(len(current_parts), len(latest_parts))
        current_parts.extend([0] * (max_len - len(current_parts)))
        latest_parts.extend([0] * (max_len - len(latest_parts)))

        update_available = latest_parts > current_parts
        return latest, update_available
    except Exception:
        # Silently fail on network errors, invalid JSON, or version parsing issues
        return None, False
