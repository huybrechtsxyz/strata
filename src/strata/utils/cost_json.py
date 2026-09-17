"""Shared parsing helpers for ``cost.json``-shaped data (ADR-0031 section 3a).

Before this module existed, three call sites each independently re-implemented
the same "walk an Infracost-shaped provisioner entry and find a monthly cost"
logic, with subtly different fallbacks:

- ``strata.validators.policies.cost_threshold_policy``
- ``strata.controllers.gate_context_builder``
- ``strata.utils.cost_history.CostHistoryStore``

This module is the single source of truth for "what does cost.json mean."
All three now delegate here instead of maintaining their own copy.

Expected shape::

    {"provisioners": {"<name>": <raw estimator breakdown/diff output>}}

Each provisioner entry may carry its cost under any of (checked in order):
``breakdown.totalMonthlyCost``, ``projects[].breakdown.totalMonthlyCost``, or
a top-level ``totalMonthlyCost`` key on the entry itself (this last form is
what a raw Infracost ``diff`` result looks like once wrapped under its
provisioner name). The same three shapes are checked for
``pastTotalMonthlyCost`` to support cost *delta* (diff) consumers.
"""

from typing import Any, Dict, Optional


def extract_provisioner_value(prov_data: Any, key: str) -> Optional[float]:
    """Extract a monthly-cost-shaped ``key`` (e.g. ``totalMonthlyCost`` or
    ``pastTotalMonthlyCost``) from a single provisioner's raw estimator output.

    Checks, in order: ``breakdown.<key>``, ``projects[].breakdown.<key>``
    (summed), then a top-level ``<key>`` on the entry itself.
    """
    if not isinstance(prov_data, dict):
        return None

    breakdown = prov_data.get("breakdown")
    if isinstance(breakdown, dict):
        value = breakdown.get(key)
        if value is not None:
            try:
                return float(value)
            except (ValueError, TypeError):
                pass

    projects = prov_data.get("projects")
    if isinstance(projects, list):
        total = 0.0
        found = False
        for project in projects:
            proj_breakdown = project.get("breakdown", {}) if isinstance(project, dict) else {}
            value = proj_breakdown.get(key)
            if value is not None:
                try:
                    total += float(value)
                    found = True
                except (ValueError, TypeError):
                    pass
        if found:
            return total

    value = prov_data.get(key)
    if value is not None:
        try:
            return float(value)
        except (ValueError, TypeError):
            pass

    return None


def extract_total_monthly(cost_data: Dict[str, Any], key: str = "totalMonthlyCost") -> Optional[float]:
    """Sum a monthly-cost-shaped ``key`` across all provisioners in a cost.json dict.

    Falls back to a top-level ``<key>`` when ``cost_data`` has no ``provisioners``
    dict (or none of its entries yield a value) — covers callers that pass a raw,
    unwrapped estimator result directly.

    Returns ``None`` when no cost could be found anywhere (distinct from a
    legitimate ``0.0`` total).
    """
    total = 0.0
    found_any = False

    provisioners = cost_data.get("provisioners", {})
    if isinstance(provisioners, dict):
        for prov_data in provisioners.values():
            value = extract_provisioner_value(prov_data, key)
            if value is not None:
                total += value
                found_any = True

    if not found_any:
        top_level = cost_data.get(key)
        if top_level is not None:
            try:
                total = float(top_level)
                found_any = True
            except (ValueError, TypeError):
                pass

    return total if found_any else None


def extract_cost_delta(cost_data: Dict[str, Any]) -> Optional[float]:
    """Return ``totalMonthlyCost - pastTotalMonthlyCost`` across all provisioners.

    ``None`` when either side can't be determined (e.g. a plain ``breakdown``
    snapshot with no "past" cost to compare against — there is no delta to
    report, as opposed to a delta of zero).
    """
    total = extract_total_monthly(cost_data, key="totalMonthlyCost")
    past_total = extract_total_monthly(cost_data, key="pastTotalMonthlyCost")
    if total is None or past_total is None:
        return None
    return round(total - past_total, 2)
