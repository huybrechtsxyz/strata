#!/usr/bin/env python3
"""Tests for rendering the audit CloudEvents+ECS envelope (docs/design/audit-trail.md's
"The audit files — ... the Event is rendered, not written").

Phase 2 of that doc's Layer 2 Implementation Plan.
"""

import uuid

from strata.controllers.audit_event_rendering import render_manifest_event, render_metrics_event
from strata.models.audit_manifest_model import DeploymentManifestModel
from strata.models.audit_metrics_model import DeploymentMetricsModel
from strata.models.common_models import PlatformVersion


def _manifest(**overrides) -> DeploymentManifestModel:
    data = {
        "execution_id": "11111111-1111-1111-1111-111111111111",
        "action": "deploy",
        "deployment": "c0224-prod",
        "workspace": "main",
        "environment": "prod",
        "started_at": "2026-09-30T14:30:00Z",
        "completed_at": "2026-09-30T14:32:03Z",
        "duration_seconds": 123.0,
        "status": "success",
        "deployed_by": "ci-runner",
        "artifacts": {"platform": {"hash": "sha256:abc", "path": "platform.json"}},
    }
    data.update(overrides)
    return DeploymentManifestModel.model_validate(data)


def _metrics(**overrides) -> DeploymentMetricsModel:
    data = {
        "execution_id": "11111111-1111-1111-1111-111111111111",
        "deployment": "c0224-prod",
        "workspace": "main",
        "timestamp": "2026-09-30T14:32:03Z",
        "dimensions": {"outcome": "success", "action": "deploy", "environment": "prod", "actor": "ci-runner"},
        "measures": {"duration_seconds": 123.0, "stages_total": 3, "stages_failed": 0},
    }
    data.update(overrides)
    return DeploymentMetricsModel.model_validate(data)


def test_render_manifest_event_shape():
    event = render_manifest_event(
        _manifest(), relative_path="z00/s01/c0224/prod/20260930-143000/_manifest.json", file_sha256="sha256:manifest"
    )
    assert event["specversion"] == "1.0"
    assert event["type"] == "xyz.huybrechts.strata.deployment.completed"
    assert event["source"] == "/strata/main/c0224-prod"
    assert event["subject"] == "c0224-prod"
    assert event["time"] == "2026-09-30T14:32:03Z"
    assert event["data"]["event"]["outcome"] == "success"
    assert event["data"]["event"]["action"] == "deployment-completed"
    assert event["data"]["event"]["duration"] == 123_000_000_000
    assert event["data"]["user"]["name"] == "ci-runner"
    assert event["data"]["labels"] == {
        "execution_id": "11111111-1111-1111-1111-111111111111",
        "workspace": "main",
        "environment": "prod",
        "deployment": "c0224-prod",
    }
    assert event["data"]["manifest"] == {
        "path": "z00/s01/c0224/prod/20260930-143000/_manifest.json",
        "sha256": "sha256:manifest",
    }


def test_render_manifest_event_destroy_action_uses_destroyed_type():
    event = render_manifest_event(_manifest(action="destroy"), relative_path="x", file_sha256="y")
    assert event["type"] == "xyz.huybrechts.strata.deployment.destroyed"
    assert event["data"]["event"]["action"] == "deployment-destroyed"


def test_render_manifest_event_omp_api_version_uses_omp_type_prefix():
    """A deployment document declared with `strata.omp.com/v2` must emit
    `com.omp.strata.*` event types, never the huybrechts.xyz-branded default."""
    event = render_manifest_event(_manifest(), relative_path="p", file_sha256="h", api_version=PlatformVersion.v2_omp)
    assert event["type"] == "com.omp.strata.deployment.completed"


def test_render_manifest_event_id_is_fresh_uuid4_each_call():
    """The same manifest rendered twice (e.g. initial send + a later resend) gets a
    different id both times — CloudEvents' own id/delivery-attempt uniqueness rule."""
    manifest = _manifest()
    first = render_manifest_event(manifest, relative_path="p", file_sha256="h")
    second = render_manifest_event(manifest, relative_path="p", file_sha256="h")
    assert first["id"] != second["id"]
    uuid.UUID(first["id"])  # does not raise — a real UUID4


def test_render_manifest_event_no_wholesale_payload_dump():
    """The envelope references the manifest by path+hash — it never embeds the
    manifest's own fields (v1's actual defect: data.strata was the full dict)."""
    event = render_manifest_event(_manifest(), relative_path="p", file_sha256="h")
    assert "artifacts" not in event["data"]
    assert "stages" not in event["data"]
    assert set(event["data"].keys()) == {"event", "user", "labels", "manifest"}


def test_render_metrics_event_shape():
    event = render_metrics_event(_metrics(), relative_path="p", file_sha256="h")
    assert event["type"] == "xyz.huybrechts.strata.deployment.measured"
    assert event["data"]["event"]["action"] == "deployment-measured"
    assert event["data"]["event"]["outcome"] == "success"
    assert event["data"]["user"]["name"] == "ci-runner"


def test_render_metrics_event_omp_api_version_uses_omp_type_prefix():
    event = render_metrics_event(_metrics(), relative_path="p", file_sha256="h", api_version=PlatformVersion.v2_omp)
    assert event["type"] == "com.omp.strata.deployment.measured"
