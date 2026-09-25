#!/usr/bin/env python3
"""Small, dependency-free `.env` file parser (docs/design/
build-time-value-categories.md, Q9) — `strata build run --env-file` lets a
local dev machine supply `ENVIRONMENT`-store values a CI pipeline would
already have exported for real. No third-party dependency added: the repo
has no existing `python-dotenv` usage, and the format is small enough not
to warrant one.
"""

from pathlib import Path


def load_env_file(path: Path) -> dict[str, str]:
    """Parse `path` as `KEY=VALUE` lines.

    `#`-prefixed and blank lines are skipped. A value wrapped in matching
    single or double quotes has them stripped. Does not evaluate shell
    expansions, multi-line values, or `export` prefixes — deliberately a
    minimal reader, not a full dotenv implementation.
    """
    result: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if key:
            result[key] = value
    return result
