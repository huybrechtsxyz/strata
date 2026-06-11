"""Unit tests for deployment manifest models."""

import pytest
from pydantic import ValidationError

from strata.models.common_models import PlatformKind, PlatformVersion
from strata.models.deployment_manifest_model import (
    DeploymentManifestMetaModel,
    DeploymentManifestModel,
    DeploymentManifestSpecModel,
    ManifestPlatformModel,
    ManifestRepositoryModel,
    ManifestStageModel,
)


class TestManifestPlatformModel:
    def test_minimal(self):
        m = ManifestPlatformModel(hash="abc123")
        assert m.hash == "abc123"
        assert m.path is None

    def test_full(self):
        m = ManifestPlatformModel(hash="abc123", path=".strata/platform.json")
        assert m.hash == "abc123"
        assert m.path == ".strata/platform.json"


class TestManifestRepositoryModel:
    def test_all_optional(self):
        m = ManifestRepositoryModel()
        assert m.url is None
        assert m.ref is None
        assert m.commit is None

    def test_full(self):
        m = ManifestRepositoryModel(
            url="https://github.com/org/repo.git",
            ref="main",
            commit="abc123def456",
        )
        assert m.url == "https://github.com/org/repo.git"
        assert m.ref == "main"
        assert m.commit == "abc123def456"


class TestManifestStageModel:
    def test_minimal(self):
        m = ManifestStageModel(name="infra", status="success")
        assert str(m.name) == "infra"
        assert m.status == "success"
        assert m.provisioner is None
        assert m.steps is None
        assert m.error is None

    def test_full(self):
        m = ManifestStageModel(
            name="infra",
            provisioner="tf_hetzner",
            topology="primary",
            status="failed",
            started_at="2025-01-01T00:00:00+00:00",
            completed_at="2025-01-01T00:05:00+00:00",
            duration_seconds=300,
            steps=["setup", "check", "plan", "apply"],
            outputs={"ip": "1.2.3.4"},
            error="Apply timed out",
        )
        assert m.provisioner == "tf_hetzner"
        assert m.steps == ["setup", "check", "plan", "apply"]
        assert m.outputs == {"ip": "1.2.3.4"}
        assert m.error == "Apply timed out"

    def test_invalid_name_rejected(self):
        with pytest.raises(ValidationError):
            ManifestStageModel(name="HAS SPACES!", status="success")


class TestDeploymentManifestMetaModel:
    def test_minimal(self):
        m = DeploymentManifestMetaModel(name="my_deployment")
        assert str(m.name) == "my_deployment"
        assert m.annotations is None
        assert m.labels is None
        assert m.tags is None

    def test_full(self):
        m = DeploymentManifestMetaModel(
            name="prod",
            annotations={"description": "Production deploy"},
            labels={"env": "prod"},
            tags=["release-v1"],
        )
        assert m.labels == {"env": "prod"}


class TestDeploymentManifestSpecModel:
    @pytest.fixture
    def minimal_spec(self):
        return DeploymentManifestSpecModel(
            deployment_name="my_deploy",
            workspace_name="my_workspace",
            action="deploy",
            started_at="2025-01-01T00:00:00+00:00",
            status="success",
            platform=ManifestPlatformModel(hash="abc123"),
        )

    def test_minimal(self, minimal_spec):
        assert str(minimal_spec.deployment_name) == "my_deploy"
        assert str(minimal_spec.workspace_name) == "my_workspace"
        assert minimal_spec.action == "deploy"
        assert minimal_spec.status == "success"
        assert minimal_spec.dry_run is False
        assert minimal_spec.stages is None
        assert minimal_spec.sbom is None

    def test_full(self):
        spec = DeploymentManifestSpecModel(
            deployment_name="prod_deploy",
            workspace_name="prod_ws",
            environment="production",
            action="deploy",
            started_at="2025-01-01T00:00:00+00:00",
            completed_at="2025-01-01T00:10:00+00:00",
            duration_seconds=600,
            status="success",
            dry_run=False,
            deployed_by="ci-bot",
            platform=ManifestPlatformModel(hash="abc123", path="platform.json"),
            repositories={
                "infra": ManifestRepositoryModel(url="https://github.com/org/infra.git", commit="abc123"),
            },
            stages=[
                ManifestStageModel(name="infra", status="success", steps=["setup", "apply"]),
            ],
        )
        assert spec.environment == "production"
        assert spec.deployed_by == "ci-bot"
        assert len(spec.repositories) == 1
        assert len(spec.stages) == 1

    def test_missing_required_fields_rejected(self):
        with pytest.raises(ValidationError):
            DeploymentManifestSpecModel(
                deployment_name="x",
                # missing workspace_name, action, started_at, status, platform
            )


class TestDeploymentManifestModel:
    @pytest.fixture
    def manifest(self):
        return DeploymentManifestModel(
            meta=DeploymentManifestMetaModel(name="prod"),
            spec=DeploymentManifestSpecModel(
                deployment_name="prod_deploy",
                workspace_name="prod_ws",
                action="deploy",
                started_at="2025-01-01T00:00:00+00:00",
                status="success",
                platform=ManifestPlatformModel(hash="abc123"),
            ),
        )

    def test_defaults(self, manifest):
        assert manifest.apiVersion == PlatformVersion.v1
        assert manifest.kind == PlatformKind.DEPLOYMENT_MANIFEST

    def test_meta_accessible(self, manifest):
        assert str(manifest.meta.name) == "prod"

    def test_spec_accessible(self, manifest):
        assert manifest.spec.action == "deploy"
        assert manifest.spec.status == "success"

    def test_json_round_trip(self, manifest):
        json_str = manifest.model_dump_json(indent=2, exclude_none=True)
        loaded = DeploymentManifestModel.model_validate_json(json_str)
        assert loaded.spec.deployment_name == manifest.spec.deployment_name
        assert loaded.kind == PlatformKind.DEPLOYMENT_MANIFEST

    def test_destroy_action(self):
        m = DeploymentManifestModel(
            meta=DeploymentManifestMetaModel(name="destroy_run"),
            spec=DeploymentManifestSpecModel(
                deployment_name="prod_deploy",
                workspace_name="prod_ws",
                action="destroy",
                started_at="2025-01-01T00:00:00+00:00",
                status="failed",
                platform=ManifestPlatformModel(hash="abc123"),
                stages=[
                    ManifestStageModel(
                        name="infra",
                        status="failed",
                        error="Destroy timed out",
                    ),
                ],
            ),
        )
        assert m.spec.action == "destroy"
        assert m.spec.stages[0].error == "Destroy timed out"
