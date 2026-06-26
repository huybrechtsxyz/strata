"""Unit tests for deploy-log models."""

import pytest
from pydantic import ValidationError

from strata.models.deploy_log_model import (
    DeployLogModel,
    DeployLogPullRequestModel,
    DeployLogStageFileModel,
    DeployLogStageModel,
    DeployLogStepModel,
)


class TestDeployLogStepModel:
    def test_valid(self):
        m = DeployLogStepModel(step="plan", success=True, duration_seconds=12.5)
        assert m.step == "plan"
        assert m.success is True
        assert m.duration_seconds == 12.5

    def test_missing_required(self):
        with pytest.raises(ValidationError):
            DeployLogStepModel(step="plan", success=True)  # type: ignore[call-arg]

    def test_extra_fields_forbidden(self):
        with pytest.raises(ValidationError):
            DeployLogStepModel(step="plan", success=True, duration_seconds=1.0, extra="bad")


class TestDeployLogStageModel:
    def _make_stage(self, **kwargs) -> DeployLogStageModel:
        defaults = dict(
            name="infrastructure",
            success=True,
            started_at="2026-06-24T14:00:00Z",
            completed_at="2026-06-24T14:02:00Z",
            duration_seconds=120.0,
        )
        defaults.update(kwargs)
        return DeployLogStageModel(**defaults)

    def test_minimal(self):
        m = self._make_stage()
        assert m.name == "infrastructure"
        assert m.provisioner is None
        assert m.topology is None
        assert m.steps == []
        assert m.errors == []

    def test_with_provisioner(self):
        m = self._make_stage(provisioner="terraform")
        assert m.provisioner == "terraform"

    def test_with_topology(self):
        m = self._make_stage(topology="core_services")
        assert m.topology == "core_services"

    def test_with_steps(self):
        steps = [
            DeployLogStepModel(step="setup", success=True, duration_seconds=2.0),
            DeployLogStepModel(step="plan", success=True, duration_seconds=45.0),
            DeployLogStepModel(step="apply", success=True, duration_seconds=73.0),
        ]
        m = self._make_stage(steps=steps)
        assert len(m.steps) == 3
        assert m.steps[1].step == "plan"

    def test_with_errors(self):
        m = self._make_stage(success=False, errors=["terraform apply failed"])
        assert m.success is False
        assert "terraform apply failed" in m.errors

    def test_invalid_name(self):
        with pytest.raises(ValidationError):
            self._make_stage(name="Invalid Name!")

    def test_extra_fields_forbidden(self):
        with pytest.raises(ValidationError):
            self._make_stage(unexpected="field")


class TestDeployLogPullRequestModel:
    def test_minimal(self):
        m = DeployLogPullRequestModel(number=42, title="feat: add replicas", url="https://github.com/org/repo/pull/42")
        assert m.number == 42
        assert m.author is None
        assert m.approvers == []
        assert m.files_changed == []

    def test_full(self):
        m = DeployLogPullRequestModel(
            number=42,
            title="feat: add replicas",
            url="https://github.com/org/repo/pull/42",
            author="jane",
            merged_by="lead",
            merged_at="2026-06-24T14:30:00Z",
            approvers=["lead", "security"],
            labels=["risk:low", "env:production"],
            linked_issues=["ORG-1234"],
            files_changed=["deploy/deploy-prd.yaml"],
        )
        assert m.merged_by == "lead"
        assert len(m.approvers) == 2
        assert "ORG-1234" in m.linked_issues

    def test_missing_required(self):
        with pytest.raises(ValidationError):
            DeployLogPullRequestModel(number=42, title="feat")  # type: ignore[call-arg]


class TestDeployLogModel:
    def _make_log(self, **kwargs) -> DeployLogModel:
        defaults = dict(
            execution_id="550e8400-e29b-41d4-a716-446655440000",
            timestamp="2026-06-24T14:32:00Z",
            version="0.13.0",
            deployment="xyz_platform_prd",
            file="deploy/deploy-prd.yaml",
            success=True,
            duration_seconds=164.0,
        )
        defaults.update(kwargs)
        return DeployLogModel(**defaults)

    def test_minimal(self):
        m = self._make_log()
        assert m.execution_id == "550e8400-e29b-41d4-a716-446655440000"
        assert m.command == "deploy_run"
        assert m.commit_sha is None
        assert m.pull_request is None
        assert m.stages == []
        assert m.errors == []
        assert m.metadata == {}
        assert m.force is False
        assert m.dry_run is False

    def test_with_git_context(self):
        m = self._make_log(
            commit_sha="abc123def456",
            commit_message="feat: increase replica count",
            commit_author="jane@example.com",
        )
        assert m.commit_sha == "abc123def456"
        assert m.commit_author == "jane@example.com"

    def test_with_stages(self):
        stages = [
            DeployLogStageModel(
                name="infrastructure",
                provisioner="terraform",
                success=True,
                started_at="2026-06-24T14:32:00Z",
                completed_at="2026-06-24T14:34:00Z",
                duration_seconds=120.0,
            ),
            DeployLogStageModel(
                name="platform",
                provisioner="helm",
                success=True,
                started_at="2026-06-24T14:34:00Z",
                completed_at="2026-06-24T14:35:00Z",
                duration_seconds=60.0,
            ),
        ]
        m = self._make_log(stages=stages)
        assert len(m.stages) == 2
        assert m.stages[0].provisioner == "terraform"

    def test_with_pull_request(self):
        pr = DeployLogPullRequestModel(
            number=42,
            title="feat: replicas",
            url="https://github.com/org/repo/pull/42",
            author="jane",
        )
        m = self._make_log(pull_request=pr)
        assert m.pull_request is not None
        assert m.pull_request.number == 42

    def test_with_metadata(self):
        m = self._make_log(metadata={"ci_run_id": "12345", "triggered_by": "schedule"})
        assert m.metadata["ci_run_id"] == "12345"

    def test_failed_deployment(self):
        m = self._make_log(
            success=False,
            errors=["Stage infrastructure failed: terraform apply error"],
        )
        assert m.success is False
        assert len(m.errors) == 1

    def test_invalid_deployment_name(self):
        with pytest.raises(ValidationError):
            self._make_log(deployment="Invalid Name!")

    def test_extra_fields_forbidden(self):
        with pytest.raises(ValidationError):
            self._make_log(unexpected_field="value")

    def test_serialization_round_trip(self):
        m = self._make_log(
            workspace="xyz_platform",
            environment="prd",
            commit_sha="abc123",
        )
        data = m.model_dump(exclude_none=True)
        restored = DeployLogModel(**data)
        assert restored.execution_id == m.execution_id
        assert restored.workspace == "xyz_platform"

    def test_json_serialization(self):
        m = self._make_log()
        json_str = m.model_dump_json(exclude_none=True)
        assert "550e8400-e29b-41d4-a716-446655440000" in json_str
        assert "deploy_run" in json_str


class TestDeployLogStageFileModel:
    def test_valid(self):
        stage = DeployLogStageModel(
            name="infrastructure",
            success=True,
            started_at="2026-06-24T14:32:00Z",
            completed_at="2026-06-24T14:34:00Z",
            duration_seconds=120.0,
        )
        m = DeployLogStageFileModel(
            execution_id="550e8400-e29b-41d4-a716-446655440000",
            timestamp="2026-06-24T14:32:00Z",
            version="0.13.0",
            deployment="xyz_platform_prd",
            stage=stage,
        )
        assert m.stage.name == "infrastructure"
        assert m.deployment == "xyz_platform_prd"

    def test_missing_stage(self):
        with pytest.raises(ValidationError):
            DeployLogStageFileModel(
                execution_id="550e8400-e29b-41d4-a716-446655440000",
                timestamp="2026-06-24T14:32:00Z",
                version="0.13.0",
                deployment="xyz_platform_prd",
            )  # type: ignore[call-arg]
