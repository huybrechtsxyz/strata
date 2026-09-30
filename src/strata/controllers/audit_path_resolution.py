#!/usr/bin/env python3
"""Resolve where a run's audit files land — the shared per-execution
directory, reused by the local write and every push destination
(docs/design/audit-trail.md's "Path resolution").

Phase 3 of that doc's Layer 2 Implementation Plan. Lives in `controllers/`
(not `utils/`) because it needs a deployment's resolved `PathConventionModel`
alongside the `DeploymentModel` itself — a cross-document lookup, same tier
as `semantic_checks.py`.

No Jinja2 templating, no config field — a plain function of already-resolved
model data. When a deployment declares `layers.follows`/`layers.segments`
(already validated against `spec.paths` by `semantic_checks.py`'s
`_check_deployment_layers()`), the directory is built by joining those
resolved segment values, in the order that convention's own
`PathConventionModel.segments[]` list declares them. Otherwise falls back to
`workspace/deployment`. The `yyyymmdd-hhmmss` leaf is unconditional either
way — a deliberate fix for a real v1 bug where some structures had no
timestamp component at all and silently overwrote the prior run's record.
"""

from datetime import datetime
from pathlib import Path

from strata.models.configuration_model import ConfigurationModel
from strata.models.deployment_model import DeploymentModel


def resolve_audit_relative_path(
    deployment: DeploymentModel,
    configuration: ConfigurationModel | None,
    started_at: str,
) -> Path:
    """Return the per-execution relative path for `deployment`'s audit files.

    Args:
        deployment: The deployment being run.
        configuration: The single effective Configuration document, or
            `None` if absent/ambiguous — falls back the same way an absent
            `layers` block would.
        started_at: The run's ISO-8601 start timestamp, fixed once at the
            top of the run — never `completed_at`, so the resolved path is
            stable even if finalizing/writing needs to be retried.

    Returns:
        A relative path, never absolute — the caller joins it under
        `layout.audit_dir(root)` for the local write, or under a git sink's
        own `path` for a push destination.
    """
    return _resolve_base_path(deployment, configuration) / _format_timestamp(started_at)


def _resolve_base_path(deployment: DeploymentModel, configuration: ConfigurationModel | None) -> Path:
    """The directory shape before the timestamp leaf — layers-derived, or workspace/deployment."""
    ordered_values = _layers_segment_values(deployment, configuration)
    if ordered_values is not None:
        return Path(*ordered_values)

    workspace = deployment.spec.workspace or "unknown-workspace"
    return Path(workspace, deployment.meta.name)


def _layers_segment_values(deployment: DeploymentModel, configuration: ConfigurationModel | None) -> list[str] | None:
    """The deployment's layers.segments values, in its convention's declared order.

    Returns `None` (triggering the workspace/deployment fallback) whenever
    anything needed to derive an ordered list is missing or inconsistent —
    never raises, since a missing/mismatched declaration is `semantic_checks.
    py`'s job to report, not this function's.
    """
    layers = deployment.spec.layers
    if layers is None or layers.follows is None or not layers.segments or configuration is None:
        return None

    conventions = {c.name: c for c in (configuration.spec.paths or [])}
    convention = conventions.get(layers.follows)
    if convention is None or not convention.segments:
        return None

    try:
        return [layers.segments[segment.name] for segment in convention.segments]
    except KeyError:
        return None


def _format_timestamp(started_at: str) -> str:
    """`yyyymmdd-hhmmss` — Windows paths reject ':', so this necessarily differs
    from the full ISO-8601 timestamp recorded inside the JSON files themselves.
    """
    return datetime.fromisoformat(started_at).strftime("%Y%m%d-%H%M%S")
