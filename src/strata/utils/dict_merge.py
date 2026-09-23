#!/usr/bin/env python3
"""Recursive dictionary merging.

Self-contained — imports nothing from other `strata.utils` modules.
"""

from typing import Any


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge `override` into `base`; `override` wins per leaf key.

    Keys present in both and mapping to dicts merge recursively. Any other
    conflict — scalar, list, or mismatched types — is replaced wholesale by
    `override`. Keys present on only one side carry through unchanged.

    Lists are replaced rather than concatenated: a caller wanting
    element-level semantics (merge by name, append, dedupe) has to state
    which, and that choice belongs with the caller who knows what the list
    means.

    Merging per leaf key rather than per top-level key matters for nested
    settings blocks. Replacing wholesale means a child overriding one field
    silently discards its siblings, which then quietly fall back to schema
    defaults — the same value changes to something nobody wrote, with no
    error. Helm values and Kustomize both merge per leaf key for this reason.

    Args:
        base: The lower-precedence dictionary.
        override: The higher-precedence dictionary.

    Returns:
        A new merged dictionary. Neither input is mutated.
    """
    result = dict(base)
    for key, value in override.items():
        existing = result.get(key)
        if isinstance(existing, dict) and isinstance(value, dict):
            result[key] = deep_merge(existing, value)
        else:
            result[key] = value
    return result
