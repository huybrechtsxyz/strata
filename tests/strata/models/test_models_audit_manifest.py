#!/usr/bin/env python3
"""Tests for the deployment manifest model — `_manifest.json` (docs/design/audit-trail.md).

Phase 2 of that doc's Layer 2 Implementation Plan.
"""

import pytest
from pydantic import ValidationError

from strata.models.audit_manifest_model import (
    ChangeReferenceModel,
    DeploymentManifestModel,
    ManifestArtifactsModel,
    ManifestPlatformReferenceModel,
    ManifestPullRequestModel,
    ManifestStageModel,
    ManifestStepModel,
)


def _minimal_manifest(**overrides) -> dict:
    data = {
        "execution_id": "11111111-1111-1111-1111-111111111111",
        "action": "deploy",
        "deployment": "c0224-prod",
        "workspace": "main",
        "started_at": "2026-09-30T14:30:00Z",
        "status": "success",
        "artifacts": {"platform": {"hash": "sha256:abc", "path": "platform.json"}},
    }
    data.update(overrides)
    return data


def test_minimal_manifest_is_valid():
    model = DeploymentManifestModel.model_validate(_minimal_manifest())
    assert model.execution_id == "11111111-1111-1111-1111-111111111111"
    assert model.status == "success"
    assert model.force is False
    assert model.dry_run is False


def test_manifest_rejects_invalid_status():
    with pytest.raises(ValidationError):
        DeploymentManifestModel.model_validate(_minimal_manifest(status="ok"))


def test_manifest_accepts_tri_state_partial_status():
    model = DeploymentManifestModel.model_validate(_minimal_manifest(status="partial"))
    assert model.status == "partial"


def test_manifest_artifacts_platform_is_reference_only():
    """artifacts.platform is hash+path only — no `content` field exists at all
    (PlatformBaseModel's extra='forbid' rejects it if supplied)."""
    data = _minimal_manifest()
    data["artifacts"]["platform"]["content"] = {"embedded": "should not be accepted"}
    with pytest.raises(ValidationError, match="content"):
        DeploymentManifestModel.model_validate(data)


def test_manifest_stage_with_steps_and_outputs_artifact():
    data = _minimal_manifest(
        stages=[
            {
                "name": "infrastructure",
                "status": "success",
                "steps": [
                    {"step": "init", "success": True, "duration_seconds": 1.5},
                    {"step": "apply", "success": True, "duration_seconds": 12.0},
                ],
                "outputs_artifact": {"path": "outputs.json", "sha256": "sha256:def"},
            }
        ]
    )
    model = DeploymentManifestModel.model_validate(data)
    assert model.stages[0].steps[0].step == "init"
    assert model.stages[0].outputs_artifact.sha256 == "sha256:def"


def test_manifest_stage_skipped_requires_no_skip_reason_but_accepts_one():
    data = _minimal_manifest(stages=[{"name": "optional-stage", "status": "skipped", "skip_reason": "enabled: false"}])
    model = DeploymentManifestModel.model_validate(data)
    assert model.stages[0].skip_reason == "enabled: false"


def test_manifest_pull_request_and_change_reference_are_independent_optional_fields():
    data = _minimal_manifest(
        pull_request={"number": 42, "title": "Fix thing", "author": "octocat"},
        change_reference={
            "system": "jira",
            "id": "OPS-1234",
            "reason": "scheduled maintenance",
            "supplied_by": "octocat",
            "supplied_at": "2026-09-30T14:00:00Z",
        },
    )
    model = DeploymentManifestModel.model_validate(data)
    assert model.pull_request.number == 42
    assert model.change_reference.id == "OPS-1234"


def test_manifest_actor_kept_distinct_from_commit_author():
    data = _minimal_manifest(deployed_by="ci-runner", commit_author="jane.doe")
    model = DeploymentManifestModel.model_validate(data)
    assert model.deployed_by == "ci-runner"
    assert model.commit_author == "jane.doe"


def test_manifest_rejects_unknown_field():
    data = _minimal_manifest(unexpected_field="nope")
    with pytest.raises(ValidationError):
        DeploymentManifestModel.model_validate(data)


def test_manifest_sub_models_importable_directly():
    """Sanity check the module's public sub-model surface is stable and importable."""
    assert ManifestArtifactsModel is not None
    assert ManifestPlatformReferenceModel is not None
    assert ManifestPullRequestModel is not None
    assert ManifestStageModel is not None
    assert ManifestStepModel is not None
    assert ChangeReferenceModel is not None


# ---------------------------------------------------------------------------
# ChangeReferenceModel — approved_by/approved_at (who approved, distinct
# from supplied_by/deployed_by)
# ---------------------------------------------------------------------------


def _minimal_change_reference(**overrides) -> dict:
    data = {
        "system": "azure_devops",
        "id": "OPS-1234",
        "reason": "scheduled maintenance window",
        "supplied_by": "ci-runner",
        "supplied_at": "2026-10-05T14:00:00+00:00",
    }
    data.update(overrides)
    return data


def test_change_reference_approval_fields_default_to_none():
    model = ChangeReferenceModel.model_validate(_minimal_change_reference())
    assert model.approved_by is None
    assert model.approved_at is None


def test_change_reference_accepts_both_approval_fields():
    model = ChangeReferenceModel.model_validate(
        _minimal_change_reference(approved_by="jsmith", approved_at="2026-10-04T09:00:00+00:00")
    )
    assert model.approved_by == "jsmith"
    assert model.approved_at == "2026-10-04T09:00:00+00:00"


def test_change_reference_rejects_approved_by_without_approved_at():
    with pytest.raises(ValidationError, match="approved_by and approved_at must be supplied together"):
        ChangeReferenceModel.model_validate(_minimal_change_reference(approved_by="jsmith"))


def test_change_reference_rejects_approved_at_without_approved_by():
    with pytest.raises(ValidationError, match="approved_by and approved_at must be supplied together"):
        ChangeReferenceModel.model_validate(_minimal_change_reference(approved_at="2026-10-04T09:00:00+00:00"))


def test_change_reference_approved_by_is_distinct_from_supplied_by():
    """The whole point: an independent approver, not whoever typed the reference."""
    model = ChangeReferenceModel.model_validate(
        _minimal_change_reference(
            supplied_by="ci-runner", approved_by="jsmith", approved_at="2026-10-04T09:00:00+00:00"
        )
    )
    assert model.supplied_by == "ci-runner"
    assert model.approved_by == "jsmith"
    assert model.supplied_by != model.approved_by
