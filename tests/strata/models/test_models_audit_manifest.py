#!/usr/bin/env python3
"""Tests for the deployment manifest model — `_manifest.json` (docs/design/audit-trail.md)."""

import pytest
from pydantic import ValidationError

from strata.models.audit_manifest_model import (
    ChangeReferenceModel,
    DeploymentManifestModel,
    ManifestArtifactsModel,
    ManifestPipelineModel,
    ManifestPlatformReferenceModel,
    ManifestStageModel,
    ManifestStepModel,
)


def _minimal_manifest(**overrides) -> dict:
    data = {
        "execution_id": "11111111-1111-1111-1111-111111111111",
        "action": "deploy",
        "deployment": "fabrikam-prod",
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


def test_manifest_stage_with_its_phases():
    data = _minimal_manifest(
        stages=[
            {
                "name": "infrastructure",
                "provisioner": "tf_main",
                "status": "success",
                "steps": [
                    {"step": "setup", "success": True, "duration_seconds": 1.5},
                    {"step": "apply", "success": True, "duration_seconds": 12.0},
                ],
            }
        ]
    )
    model = DeploymentManifestModel.model_validate(data)
    assert [step.step for step in model.stages[0].steps] == ["setup", "apply"]


@pytest.mark.parametrize("field", ["outputs", "outputs_artifact", "topology"])
def test_manifest_stage_carries_no_output_values_and_no_topology(field):
    # docs/design/audit-trail.md, "Stages": output values stay in the output store.
    stage = {"name": "infrastructure", "status": "success", field: {"x": "y"}}
    with pytest.raises(ValidationError, match=field):
        DeploymentManifestModel.model_validate(_minimal_manifest(stages=[stage]))


def test_manifest_stage_skipped_requires_no_skip_reason_but_accepts_one():
    data = _minimal_manifest(stages=[{"name": "optional-stage", "status": "skipped", "skip_reason": "enabled: false"}])
    model = DeploymentManifestModel.model_validate(data)
    assert model.stages[0].skip_reason == "enabled: false"


def test_manifest_change_reference_is_optional():
    assert DeploymentManifestModel.model_validate(_minimal_manifest()).change_reference is None
    data = _minimal_manifest(
        change_reference={
            "system": "jira",
            "id": "OPS-1234",
            "reason": "scheduled maintenance",
            "supplied_by": "octocat",
            "supplied_at": "2026-09-30T14:00:00Z",
        },
    )
    model = DeploymentManifestModel.model_validate(data)
    assert model.change_reference.id == "OPS-1234"


def test_manifest_pipeline_needs_a_system_and_a_run_id_only():
    model = DeploymentManifestModel.model_validate(
        _minimal_manifest(pipeline={"system": "azure_devops", "run_id": "4711"})
    )
    assert model.pipeline == ManifestPipelineModel(system="azure_devops", run_id="4711")
    with pytest.raises(ValidationError, match="system"):
        DeploymentManifestModel.model_validate(_minimal_manifest(pipeline={"system": "jenkins", "run_id": "1"}))


def test_manifest_records_who_ran_the_deploy():
    model = DeploymentManifestModel.model_validate(_minimal_manifest(deployed_by="ci-runner"))
    assert model.deployed_by == "ci-runner"


@pytest.mark.parametrize(
    "field", ["commit_author", "commit_message", "force", "pull_request", "outputs", "signatures", "metadata"]
)
def test_manifest_drops_the_fields_nothing_could_fill(field):
    # docs/design/audit-trail.md, "The audit files". None was ever written, so no existing manifest holds one.
    with pytest.raises(ValidationError, match=field):
        DeploymentManifestModel.model_validate(_minimal_manifest(**{field: "x"}))


def test_manifest_providers_carry_the_tool_version_and_path_and_both_are_optional():
    artifacts = {"platform": {"hash": "sha256:abc", "path": "platform.json"}}
    old = {**artifacts, "providers": [{"name": "p1", "type": "helm"}]}
    new = {
        **artifacts,
        "providers": [{"name": "p1", "type": "helm", "tool_version": "4.3.1", "tool_path": "/bin/helm"}],
    }

    [provider] = DeploymentManifestModel.model_validate(_minimal_manifest(artifacts=old)).artifacts.providers
    assert provider.tool_version is None and provider.tool_path is None
    [provider] = DeploymentManifestModel.model_validate(_minimal_manifest(artifacts=new)).artifacts.providers
    assert provider.tool_version == "4.3.1" and provider.tool_path == "/bin/helm"


def test_manifest_commit_dirty_and_repository_dirty_are_optional():
    """docs/design/remote-commit-provenance.md, "The audit manifest copies the record": a manifest written before the
    build record still loads."""
    platform = {"platform": {"hash": "sha256:abc", "path": "resolved.yaml"}}
    old = _minimal_manifest(
        commit_sha="5d0e", artifacts={**platform, "repositories": {"infra": {"url": "u", "commit": "3f9c"}}}
    )
    new = _minimal_manifest(
        commit_sha="5d0e",
        commit_dirty=True,
        artifacts={**platform, "repositories": {"infra": {"url": "u", "commit": "3f9c", "dirty": False}}},
    )

    model = DeploymentManifestModel.model_validate(old)
    assert model.commit_dirty is None and model.artifacts.repositories["infra"].dirty is None
    model = DeploymentManifestModel.model_validate(new)
    assert model.commit_dirty is True and model.artifacts.repositories["infra"].dirty is False


def test_manifest_rejects_unknown_field():
    data = _minimal_manifest(unexpected_field="nope")
    with pytest.raises(ValidationError):
        DeploymentManifestModel.model_validate(data)


def test_manifest_sub_models_importable_directly():
    """Sanity check the module's public sub-model surface is stable and importable."""
    assert ManifestArtifactsModel is not None
    assert ManifestPlatformReferenceModel is not None
    assert ManifestPipelineModel is not None
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
