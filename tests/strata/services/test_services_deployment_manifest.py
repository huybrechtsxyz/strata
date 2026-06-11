"""Unit tests for DeploymentManifestService."""

import json

from strata.models.common_models import PlatformKind
from strata.models.deployment_manifest_model import (
    DeploymentManifestMetaModel,
    DeploymentManifestModel,
    DeploymentManifestSpecModel,
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
            platform=ManifestPlatformModel(hash="abc123"),
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
