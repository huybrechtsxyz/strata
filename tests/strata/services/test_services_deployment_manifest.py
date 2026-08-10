"""Unit tests for DeploymentManifestService."""

import json
from pathlib import Path

from strata.models.common_models import PlatformKind
from strata.models.configuration_model import ConfigurationManifestModel
from strata.models.deployment_manifest_model import (
    DeploymentManifestMetaModel,
    DeploymentManifestModel,
    DeploymentManifestSpecModel,
    ManifestArtifactsModel,
    ManifestPlatformModel,
    ManifestStageModel,
)
from strata.services.deployment_manifest_service import DeploymentManifestService


def _make_manifest(
    deployment_name: str = "my_deploy",
    started_at: str = "2025-06-11T14:00:00+00:00",
    status: str = "success",
) -> DeploymentManifestModel:
    return DeploymentManifestModel(
        meta=DeploymentManifestMetaModel(name=deployment_name),
        spec=DeploymentManifestSpecModel(
            deployment_name=deployment_name,
            workspace_name="test_ws",
            action="deploy",
            started_at=started_at,
            status=status,
            artifacts=ManifestArtifactsModel(
                platform=ManifestPlatformModel(hash="abc123"),
            ),
            stages=[
                ManifestStageModel(name="infra", status=status),
            ],
        ),
    )


class TestDeploymentManifestServiceInit:
    def test_create_without_path(self):
        svc = DeploymentManifestService()
        assert svc.model is None

    def test_get_model_class(self):
        svc = DeploymentManifestService()
        assert svc._get_model_class() == DeploymentManifestModel


class TestDeploymentManifestServiceSave:
    def test_save_creates_file(self, tmp_path):
        svc = DeploymentManifestService()
        manifest = _make_manifest()
        result = svc.save(manifest, tmp_path)
        assert result.exists()
        assert result.suffix == ".json"
        assert "my_deploy" in result.stem

    def test_save_content_valid_json(self, tmp_path):
        svc = DeploymentManifestService()
        manifest = _make_manifest()
        result = svc.save(manifest, tmp_path)
        data = json.loads(result.read_text(encoding="utf-8"))
        assert data["kind"] == PlatformKind.DEPLOYMENT_MANIFEST.value
        assert data["spec"]["deployment_name"] == "my_deploy"
        assert data["spec"]["status"] == "success"

    def test_save_creates_directory(self, tmp_path):
        svc = DeploymentManifestService()
        manifest = _make_manifest()
        nested = tmp_path / "sub" / "dir"
        result = svc.save(manifest, nested)
        assert result.exists()
        assert nested.exists()

    def test_save_filename_contains_timestamp(self, tmp_path):
        svc = DeploymentManifestService()
        manifest = _make_manifest(started_at="2025-06-11T14:30:00+00:00")
        result = svc.save(manifest, tmp_path)
        assert "20250611T1430" in result.stem

    def test_save_excludes_none_fields(self, tmp_path):
        svc = DeploymentManifestService()
        manifest = _make_manifest()
        result = svc.save(manifest, tmp_path)
        data = json.loads(result.read_text(encoding="utf-8"))
        assert "sbom" not in data["spec"]
        assert "signatures" not in data["spec"]


class TestDeploymentManifestServiceQuery:
    def test_list_manifests_empty(self, tmp_path):
        result = DeploymentManifestService.list_manifests(tmp_path)
        assert result == []

    def test_list_manifests_nonexistent_dir(self, tmp_path):
        result = DeploymentManifestService.list_manifests(tmp_path / "nope")
        assert result == []

    def test_list_manifests_returns_sorted(self, tmp_path):
        svc = DeploymentManifestService()
        svc.save(_make_manifest(started_at="2025-01-01T00:00:00+00:00"), tmp_path)
        svc.save(_make_manifest(started_at="2025-06-01T00:00:00+00:00"), tmp_path)
        svc.save(_make_manifest(started_at="2025-03-01T00:00:00+00:00"), tmp_path)
        result = DeploymentManifestService.list_manifests(tmp_path)
        assert len(result) == 3
        # Newest first
        assert "20250601" in result[0].stem
        assert "20250301" in result[1].stem
        assert "20250101" in result[2].stem

    def test_get_latest_returns_newest(self, tmp_path):
        svc = DeploymentManifestService()
        svc.save(_make_manifest(started_at="2025-01-01T00:00:00+00:00"), tmp_path)
        svc.save(_make_manifest(started_at="2025-06-01T00:00:00+00:00"), tmp_path)
        latest = DeploymentManifestService.get_latest(tmp_path)
        assert latest is not None
        assert "20250601" in latest.stem

    def test_get_latest_filtered_by_name(self, tmp_path):
        svc = DeploymentManifestService()
        svc.save(
            _make_manifest(deployment_name="alpha", started_at="2025-01-01T00:00:00+00:00"),
            tmp_path,
        )
        svc.save(
            _make_manifest(deployment_name="beta", started_at="2025-06-01T00:00:00+00:00"),
            tmp_path,
        )
        latest = DeploymentManifestService.get_latest(tmp_path, deployment_name="alpha")
        assert latest is not None
        assert "alpha" in latest.stem

    def test_get_latest_returns_none_when_empty(self, tmp_path):
        result = DeploymentManifestService.get_latest(tmp_path)
        assert result is None


class TestDeploymentManifestServiceValidate:
    def test_validate_dynamic_always_passes(self):
        svc = DeploymentManifestService()
        ok, errors = svc._validate_dynamic()
        assert ok is True
        assert errors == []


class TestResolveOutputDir:
    def test_local_relative_path(self, tmp_path):
        config = ConfigurationManifestModel(path=".strata/deployments")
        result = DeploymentManifestService.resolve_output_dir(config, tmp_path, "prod_deploy")
        assert result == tmp_path / ".strata" / "deployments" / "prod_deploy"

    def test_local_relative_path_with_version(self, tmp_path):
        config = ConfigurationManifestModel(path=".strata/deployments")
        result = DeploymentManifestService.resolve_output_dir(config, tmp_path, "prod_deploy", version="2.3.0")
        assert result == tmp_path / ".strata" / "deployments" / "prod_deploy" / "2.3.0"

    def test_local_absolute_path(self, tmp_path):
        abs_path = str(tmp_path / "custom" / "output")
        config = ConfigurationManifestModel(path=abs_path)
        result = DeploymentManifestService.resolve_output_dir(config, tmp_path, "my_deploy")
        assert result == Path(abs_path) / "my_deploy"

    def test_repository_field_does_not_affect_path(self, tmp_path):
        """repository (RepositoryPushModel) governs the durable push destination, not the local path."""
        config = ConfigurationManifestModel(
            path="deployments", push_manifest=True, repository={"push": True, "name": "state-repo"}
        )
        result = DeploymentManifestService.resolve_output_dir(config, tmp_path, "staging_deploy", version="1.0.0")
        assert result == tmp_path / "deployments" / "staging_deploy" / "1.0.0"

    def test_no_version_omits_version_segment(self, tmp_path):
        config = ConfigurationManifestModel(path="out")
        result = DeploymentManifestService.resolve_output_dir(config, tmp_path, "app")
        assert result == tmp_path / "out" / "app"


class TestSaveWithConfig:
    def test_save_with_config_creates_structured_path(self, tmp_path):
        config = ConfigurationManifestModel(path=".strata/deployments")
        svc = DeploymentManifestService()
        manifest = _make_manifest(deployment_name="web_app")
        result = svc.save_with_config(manifest, config, tmp_path, version="1.2.0")
        assert result.exists()
        assert "web_app" in str(result)
        assert "1.2.0" in str(result)
        # File is inside: tmp_path/.strata/deployments/web_app/1.2.0/
        assert result.parent == tmp_path / ".strata" / "deployments" / "web_app" / "1.2.0"

    def test_save_with_config_no_version(self, tmp_path):
        config = ConfigurationManifestModel(path="manifests")
        svc = DeploymentManifestService()
        manifest = _make_manifest(deployment_name="api_svc")
        result = svc.save_with_config(manifest, config, tmp_path)
        assert result.exists()
        assert result.parent == tmp_path / "manifests" / "api_svc"

    def test_save_with_config_and_repository_still_writes_locally(self, tmp_path):
        """repository (durable push) does not change where the file is written locally."""
        config = ConfigurationManifestModel(
            path="state", push_manifest=True, repository={"push": True, "name": "ops-repo"}
        )
        svc = DeploymentManifestService()
        manifest = _make_manifest(deployment_name="prod")
        result = svc.save_with_config(manifest, config, tmp_path, version="3.0.0")
        assert result.exists()
        data = json.loads(result.read_text(encoding="utf-8"))
        assert data["spec"]["deployment_name"] == "prod"
