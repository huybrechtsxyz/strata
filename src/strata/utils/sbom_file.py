"""Reading back the `sbom.json` that `build run` writes (`sbom_controller.write_sbom()`).

One loader for every reader (the image tag policy, the image gate, the audit manifest), so a missing, unreadable
or malformed file is handled the same way everywhere: `None` when there is no file, `ValueError` when there is one
that cannot be used. What each reader then takes from the components (purls, `strata:` properties) stays with it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

#: The file `build run` writes at the root of the build path.
SBOM_FILENAME = "sbom.json"


def read_sbom(build_path: Path) -> dict[str, Any] | None:
    """The build's `sbom.json`, or `None` when the build has none.

    Raises:
        ValueError: `sbom.json` exists but cannot be used: it cannot be read (locked, no permission), is not JSON,
            or has no `components` list.
    """
    path = build_path / SBOM_FILENAME
    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ValueError(f"'{path}' cannot be read ({exc})") from None
    return parse_sbom(text, path)


def parse_sbom(text: str | bytes, path: Path) -> dict[str, Any]:
    """`text` read as an SBOM; `path` only names the file in the error.

    An SBOM with no components has no `components` key at all (CycloneDX leaves it out), so a missing key is an
    empty list, and the returned SBOM always has one.

    Raises:
        ValueError: `text` is not JSON or not an object, or its `components` is not a list.
    """
    try:
        sbom = json.loads(text)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError(f"'{path}' is not valid JSON ({exc})") from None
    if not isinstance(sbom, dict) or not isinstance(sbom.setdefault("components", []), list):
        raise ValueError(f"'{path}' has no components list")
    return cast(dict[str, Any], sbom)


def component_properties(component: Any) -> dict[str, str]:
    """A CycloneDX component's `properties` as a mapping (the first value wins); empty for anything that is not a
    component or has no properties."""
    if not isinstance(component, dict):
        return {}
    found: dict[str, str] = {}
    for item in component.get("properties") or []:
        if isinstance(item, dict) and isinstance(item.get("name"), str) and isinstance(item.get("value"), str):
            found.setdefault(item["name"], item["value"])
    return found
