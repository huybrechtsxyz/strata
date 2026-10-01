#!/usr/bin/env python3
"""Render the audit CloudEvents+ECS envelope on demand — never written to disk
(docs/design/audit-trail.md's "The audit files — ... the Event is rendered,
not written").

Phase 2 of that doc's Layer 2 Implementation Plan. Lives in `controllers/`
(not `utils/`) because it depends on concrete model types
(`DeploymentManifestModel`/`DeploymentMetricsModel`), and `utils/` must
never depend on `models/` (ADR-0003) — same placement reasoning as
`semantic_checks.py`.

Nothing calls this yet in this pass — the `integration` sink arm's dispatch
(the only caller this would ever have) is deferred (see "Capability
gating"). Modeled and unit-tested now so the envelope shape is settled
ahead of that later phase, not designed under its time pressure.

`id` is a fresh UUID4 per call, never reused — the same rendering function
serves both an initial send and a later `audit resend`, and CloudEvents'
own `id` is meant to be unique per delivery attempt (v1 defect #7 is
satisfied by *code-path* identity, not *byte* identity — see the design
doc). `time` is the event's own occurrence time (`completed_at`/
`timestamp`), not the render call's wall-clock, so two renderings of the
same manifest describe the same moment.

The `type`'s reverse-domain prefix tracks the *deployment document's own*
`apiVersion` (`PlatformVersion`), not a single hardcoded constant — an OMP
deployment (`strata.omp.com/v2`) must never emit a `huybrechts.xyz`-branded
event type, and vice versa. Callers (the future `integration` sink
dispatch) pass the deployment's `apiVersion` straight through; it defaults
to `CANONICAL_API_VERSION` for callers that don't have one handy.
"""

import uuid
from typing import Any

from strata.models.audit_manifest_model import DeploymentManifestModel
from strata.models.audit_metrics_model import DeploymentMetricsModel
from strata.models.common_models import CANONICAL_API_VERSION, PlatformVersion

_EVENT_SOURCE_PREFIX = "/strata"

# Reverse-domain CloudEvents `type` prefix, keyed by the deployment document's
# own `apiVersion` — an OMP deployment must emit `com.omp.strata.*` types, a
# huybrechts.xyz one `xyz.huybrechts.strata.*`. Kept exhaustive over
# `PlatformVersion` on purpose: a future third api version would fail this
# lookup loudly (KeyError) rather than silently mislabeling its events.
_CLOUDEVENTS_TYPE_PREFIXES: dict[PlatformVersion, str] = {
    PlatformVersion.v2: "xyz.huybrechts.strata",
    PlatformVersion.v2_omp: "com.omp.strata",
}


def _render_event(
    *,
    api_version: PlatformVersion,
    event_type: str,
    ecs_action: str,
    outcome: str,
    time: str,
    workspace: str,
    deployment: str,
    environment: str | None,
    execution_id: str,
    actor: str | None,
    duration_seconds: float | None,
    relative_path: str,
    file_sha256: str,
) -> dict[str, Any]:
    """Build the shared CloudEvents 1.0 + ECS envelope both renderers use.

    Stays genuinely thin — no producer payload dumped in wholesale (v1's
    actual defect: `data.strata` was the *entire* deploy-log dict). The
    source record is referenced by path + hash (`data.manifest`), never
    embedded — the same reference discipline already applied to
    `artifacts.platform` inside the manifest itself.
    """
    duration_nanos = int(duration_seconds * 1_000_000_000) if duration_seconds is not None else None
    type_prefix = _CLOUDEVENTS_TYPE_PREFIXES[api_version]
    return {
        "specversion": "1.0",
        "type": f"{type_prefix}.{event_type}",
        "source": f"{_EVENT_SOURCE_PREFIX}/{workspace}/{deployment}",
        "id": str(uuid.uuid4()),
        "time": time,
        "subject": deployment,
        "datacontenttype": "application/json",
        "data": {
            "event": {
                "kind": "event",
                "category": ["configuration"],
                "action": ecs_action,
                "outcome": outcome,
                "duration": duration_nanos,
            },
            "user": {"name": actor},
            "labels": {
                "execution_id": execution_id,
                "workspace": workspace,
                "environment": environment,
                "deployment": deployment,
            },
            "manifest": {"path": relative_path, "sha256": file_sha256},
        },
    }


def render_manifest_event(
    manifest: DeploymentManifestModel,
    *,
    relative_path: str,
    file_sha256: str,
    api_version: PlatformVersion = CANONICAL_API_VERSION,
) -> dict[str, Any]:
    """Render `deployment.completed`/`deployment.destroyed` from `_manifest.json`'s content.

    `api_version` should be the originating deployment document's own
    `apiVersion` (`DeploymentModel.apiVersion`) — it decides the event
    `type`'s reverse-domain prefix, not just a cosmetic default.
    """
    suffix = "destroyed" if manifest.action == "destroy" else "completed"
    return _render_event(
        api_version=api_version,
        event_type=f"deployment.{suffix}",
        ecs_action=f"deployment-{suffix}",
        outcome=manifest.status,
        time=manifest.completed_at or manifest.started_at,
        workspace=manifest.workspace,
        deployment=manifest.deployment,
        environment=manifest.environment,
        execution_id=manifest.execution_id,
        actor=manifest.deployed_by,
        duration_seconds=manifest.duration_seconds,
        relative_path=relative_path,
        file_sha256=file_sha256,
    )


def render_metrics_event(
    metrics: DeploymentMetricsModel,
    *,
    relative_path: str,
    file_sha256: str,
    api_version: PlatformVersion = CANONICAL_API_VERSION,
) -> dict[str, Any]:
    """Render `deployment.measured` from `_metrics.json`'s content.

    `api_version` should be the originating deployment document's own
    `apiVersion` (`DeploymentModel.apiVersion`) — it decides the event
    `type`'s reverse-domain prefix, not just a cosmetic default.
    """
    return _render_event(
        api_version=api_version,
        event_type="deployment.measured",
        ecs_action="deployment-measured",
        outcome=metrics.dimensions.outcome,
        time=metrics.timestamp,
        workspace=metrics.workspace,
        deployment=metrics.deployment,
        environment=metrics.dimensions.environment,
        execution_id=metrics.execution_id,
        actor=metrics.dimensions.actor,
        duration_seconds=metrics.measures.duration_seconds,
        relative_path=relative_path,
        file_sha256=file_sha256,
    )
