"""Recursive dictionary merging.

Self-contained utility — no cross-imports from other ``strata.utils`` modules.
"""

from typing import Any, Dict


def deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """Recursively merge ``override`` into ``base``.

    Keys present in both that map to dicts are merged recursively. Any other
    conflicting key (scalar, list, or mismatched types) is replaced wholesale
    by the value from ``override``. Keys only present in one side are carried
    through unchanged.

    Args:
        base: Base dictionary.
        override: Dictionary whose values take precedence on conflict.

    Returns:
        A new merged dictionary; neither ``base`` nor ``override`` is mutated.
    """
    result = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result
