"""Helm input validator — cross-checks configuration keys against chart values.yaml.

For local charts (repository + source_path), compares top-level keys from
module.spec.configuration against the chart's default values.yaml.
Catches typos in user-authored Helm value overrides before deploy.
"""

from difflib import get_close_matches
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import yaml

from strata.logger import get_logger

logger = get_logger(__name__)


def parse_chart_values(chart_dir: Path) -> Dict[str, Any]:
    """Parse the chart's default values.yaml and return its contents.

    Args:
        chart_dir: Directory containing the Helm chart (with values.yaml).

    Returns:
        Parsed values dict, or empty dict if not found.
    """
    values_path = chart_dir / "values.yaml"
    if not values_path.exists():
        return {}

    try:
        with open(values_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        logger.debug("Failed to parse chart values.yaml", file=str(values_path), error=str(exc))
        return {}


def collect_all_key_paths(d: Dict[str, Any], prefix: str = "") -> Set[str]:
    """Recursively collect all dotted key paths from a nested dict.

    Example: {"a": {"b": 1, "c": 2}} → {"a", "a.b", "a.c"}
    """
    paths: Set[str] = set()
    for key, value in d.items():
        full_key = f"{prefix}{key}" if not prefix else f"{prefix}.{key}"
        paths.add(full_key)
        if isinstance(value, dict):
            paths.update(collect_all_key_paths(value, full_key))
    return paths


def check_helm_values(
    override_keys: Dict[str, Any],
    chart_values: Dict[str, Any],
    module_label: str,
) -> tuple:
    """Cross-check override keys against chart default values.

    Walks the override dict and checks each key path against the chart's
    default values structure. Only checks paths that exist in the chart —
    charts may accept arbitrary keys under certain paths.

    Args:
        override_keys: The user-authored configuration dict from module.spec.configuration.
        chart_values: The chart's default values.yaml parsed as a dict.
        module_label: Label for error messages (e.g. "namespace/module").

    Returns:
        (errors, warnings) tuple of string lists.
    """
    errors: List[str] = []
    warnings: List[str] = []

    if not chart_values or not override_keys:
        return errors, warnings

    chart_top_keys = set(chart_values.keys())

    # Check top-level keys in the override against chart's top-level keys
    for key in sorted(override_keys.keys()):
        if key not in chart_top_keys:
            suggestion = _find_closest(key, chart_top_keys)
            msg = f"[{module_label}] Configuration key '{key}' is not in chart values.yaml"
            if suggestion:
                msg += f" (did you mean '{suggestion}'?)"
            errors.append(msg)
        elif isinstance(override_keys[key], dict) and isinstance(chart_values.get(key), dict):
            # Recurse one level for nested dicts
            _check_nested(
                override=override_keys[key],
                chart=chart_values[key],
                path=key,
                module_label=module_label,
                errors=errors,
            )

    return errors, warnings


def _check_nested(
    override: Dict[str, Any],
    chart: Dict[str, Any],
    path: str,
    module_label: str,
    errors: List[str],
) -> None:
    """Check nested keys one level deep."""
    chart_keys = set(chart.keys())
    for key in sorted(override.keys()):
        full_path = f"{path}.{key}"
        if key not in chart_keys:
            suggestion = _find_closest(key, chart_keys)
            msg = f"[{module_label}] Configuration key '{full_path}' is not in chart values.yaml"
            if suggestion:
                msg += f" (did you mean '{path}.{suggestion}'?)"
            errors.append(msg)


def _find_closest(key: str, candidates: Set[str], cutoff: float = 0.6) -> Optional[str]:
    """Find the closest matching key for typo suggestions."""
    matches = get_close_matches(key, sorted(candidates), n=1, cutoff=cutoff)
    return matches[0] if matches else None
