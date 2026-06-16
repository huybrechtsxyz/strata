"""Unit tests for deployment manifest models."""

import pytest
from pydantic import ValidationError

from strata.models.common_models import PlatformKind, PlatformVersion
from strata.models.deployment_manifest_model import (
    DeploymentManifestMetaModel,
    DeploymentManifestModel,
    DeploymentManifestSpecModel,
    ManifestArtifactImageModel,
    ManifestArtifactProviderModel,
    ManifestArtifactsModel,
    ManifestLockReferenceModel,
    ManifestOutputsReferenceModel,
    ManifestPlatformModel,
    ManifestRepositoryModel,
    ManifestStageModel,
)


def _make_artifacts(**kwargs) -> ManifestArtifactsModel:
    defaults = dict(platform=ManifestPlatformModel(hash="sha256:abc123"))
    defaults.update(kwargs)
    return ManifestArtifactsModel(**defaults)


def _make_spec(**kwargs) -> DeploymentManifestSpecModel:
    defaults = dict(
        deployment_name="my_deploy",
        workspace_name="my_ws",
        action="deploy",
        started_at="2025-01-01T00:00:00+00:00",
        status="success",
        artifacts=_make_artifacts(),
    )
    defaults.update(kwargs)
    return DeploymentManifestSpecModel(**defaults)


class TestManifestPlatformModel:
    def test_minimal(self):
        m = ManifestPlatformModel(hash="abc123")
        assert m.hash == "abc123"
        assert m.path is None
        assert m.content is None

    def test_with_path(self):
        m = ManifestPlatformModel(hash="abc123", path="build/platform.json")
        assert m.path == "build/platform.json"

    def test_with_content(self):
        m = ManifestPlatformModel(hash="abc123", content={"kind": "platform_model", "spec": {}})
        assert m.content["kind"] == "platform_model"

    def test_full(self):
        m = ManifestPlatformModel(
            hash="sha256:deadbeef",
            path="build/prod/platform.json",
            content={"spec": {"deployment_name": "prod"}},
        )
        assert m.hash == "sha256:deadbeef"
        assert m.content["spec"]["deployment_name"] == "prod"


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


class TestManifestArtifactImageModel:
    def test_minimal(self):
        m = ManifestArtifactImageModel(name="traefik", image="docker.io/traefik:v3.0.1")
        assert m.name == "traefik"
        assert m.image == "docker.io/traefik:v3.0.1"
        assert m.digest is None

    def test_with_digest(self):
        m = ManifestArtifactImageModel(
            name="app",
            image="registry.example.com/app:v2.0",
            digest="sha256:cafebabe",
        )
        assert m.digest == "sha256:cafebabe"

    def test_missing_image_rejected(self):
        with pytest.raises(ValidationError):
            ManifestArtifactImageModel(name="traefik")  # type: ignore[call-arg]


class TestManifestArtifactProviderModel:
    def test_minimal(self):
        m = ManifestArtifactProviderModel(name="tf_hetzner", type="terraform")
        assert m.name == "tf_hetzner"
        assert m.type == "terraform"
        assert m.backend is None
        assert m.details is None

    def test_terraform_with_backend(self):
        m = ManifestArtifactProviderModel(
            name="tf_hetzner",
            type="terraform",
            backend={"type": "azurerm", "configuration": {"container_name": "state"}},
        )
        assert m.backend["type"] == "azurerm"

    def test_ansible_with_details(self):
        m = ManifestArtifactProviderModel(
            name="ansible_configure",
            type="ansible",
            details={"playbook": "site.yml"},
        )
        assert m.details["playbook"] == "site.yml"

    def test_missing_type_rejected(self):
        with pytest.raises(ValidationError):
            ManifestArtifactProviderModel(name="x")  # type: ignore[call-arg]


class TestManifestArtifactsModel:
    def test_minimal(self):
        m = ManifestArtifactsModel(platform=ManifestPlatformModel(hash="abc"))
        assert m.platform.hash == "abc"
        assert m.repositories is None
        assert m.images is None
        assert m.providers is None

    def test_full(self):
        m = ManifestArtifactsModel(
            platform=ManifestPlatformModel(hash="sha256:abc", content={"kind": "platform_model"}),
            repositories={"xyz_infra": ManifestRepositoryModel(url="git@github.com:org/infra.git", commit="abc123")},
            images=[ManifestArtifactImageModel(name="traefik", image="traefik:v3")],
            providers=[ManifestArtifactProviderModel(name="tf_hetzner", type="terraform")],
        )
        assert m.platform.content["kind"] == "platform_model"
        assert "xyz_infra" in m.repositories
        assert len(m.images) == 1
        assert len(m.providers) == 1


class TestManifestOutputsReferenceModel:
    def test_all_fields(self):
        m = ManifestOutputsReferenceModel(
            path=".strata/outputs/prod/1.0.0/infra.json",
            stage="infra",
            version="1.0.0",
            written_at="2026-01-01T00:00:00+00:00",
        )
        assert m.path == ".strata/outputs/prod/1.0.0/infra.json"
        assert m.stage == "infra"
        assert m.version == "1.0.0"
        assert m.written_at == "2026-01-01T00:00:00+00:00"

    def test_missing_path_rejected(self):
        with pytest.raises(ValidationError):
            ManifestOutputsReferenceModel(stage="infra", version="1.0.0", written_at="ts")  # type: ignore[call-arg]

    def test_missing_stage_rejected(self):
        with pytest.raises(ValidationError):
            ManifestOutputsReferenceModel(path="x", version="1.0.0", written_at="ts")  # type: ignore[call-arg]

    def test_serialised_round_trip(self):
        m = ManifestOutputsReferenceModel(
            path=".strata/outputs/prod/1.0.0/infra.json",
            stage="infra",
            version="1.0.0",
            written_at="2026-01-01T00:00:00+00:00",
        )
        data = m.model_dump()
        restored = ManifestOutputsReferenceModel(**data)
        assert restored.path == m.path


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

    def test_outputs_artifact_field_is_none_by_default(self):
        m = ManifestStageModel(name="infra", status="success")
        assert m.outputs_artifact is None

    def test_outputs_artifact_field_accepted(self):
        ref = ManifestOutputsReferenceModel(
            path=".strata/outputs/prod/1.0.0/infra.json",
            stage="infra",
            version="1.0.0",
            written_at="2026-01-01T00:00:00+00:00",
        )
        m = ManifestStageModel(name="infra", status="success", outputs_artifact=ref)
        assert m.outputs_artifact is not None
        assert m.outputs_artifact.path == ".strata/outputs/prod/1.0.0/infra.json"

    def test_outputs_artifact_serialised(self):
        ref = ManifestOutputsReferenceModel(
            path=".strata/outputs/prod/1.0.0/infra.json",
            stage="infra",
            version="1.0.0",
            written_at="2026-01-01T00:00:00+00:00",
        )
        m = ManifestStageModel(name="infra", status="success", outputs_artifact=ref)
        data = m.model_dump()
        assert data["outputs_artifact"]["path"] == ".strata/outputs/prod/1.0.0/infra.json"

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
    def test_minimal(self):
        spec = _make_spec()
        assert str(spec.deployment_name) == "my_deploy"
        assert spec.action == "deploy"
        assert spec.status == "success"
        assert spec.dry_run is False
        assert spec.stages is None
        assert spec.sbom is None
        assert spec.artifacts.repositories is None

    def test_artifacts_accessible(self):
        spec = _make_spec(
            artifacts=ManifestArtifactsModel(
                platform=ManifestPlatformModel(hash="sha256:abc", content={"spec": {}}),
                repositories={"repo_a": ManifestRepositoryModel(commit="abc123")},
                providers=[ManifestArtifactProviderModel(name="tf_x", type="terraform")],
                images=[ManifestArtifactImageModel(name="svc", image="img:latest")],
            )
        )
        assert spec.artifacts.platform.hash == "sha256:abc"
        assert spec.artifacts.platform.content is not None
        assert "repo_a" in spec.artifacts.repositories
        assert len(spec.artifacts.providers) == 1
        assert len(spec.artifacts.images) == 1

    def test_missing_required_fields_rejected(self):
        with pytest.raises(ValidationError):
            DeploymentManifestSpecModel(deployment_name="x")  # type: ignore[call-arg]

    def test_full(self):
        spec = _make_spec(
            environment="production",
            completed_at="2025-01-01T00:10:00+00:00",
            duration_seconds=600,
            deployed_by="ci-bot",
            stages=[ManifestStageModel(name="infra", status="success", steps=["setup", "apply"])],
        )
        assert spec.environment == "production"
        assert spec.deployed_by == "ci-bot"
        assert len(spec.stages) == 1


class TestDeploymentManifestModel:
    @pytest.fixture
    def manifest(self):
        return DeploymentManifestModel(
            meta=DeploymentManifestMetaModel(name="prod"),
            spec=_make_spec(deployment_name="prod_deploy", workspace_name="prod_ws"),
        )

    def test_defaults(self, manifest):
        assert manifest.apiVersion == PlatformVersion.v1
        assert manifest.kind == PlatformKind.DEPLOYMENT_MANIFEST

    def test_meta_accessible(self, manifest):
        assert str(manifest.meta.name) == "prod"

    def test_spec_accessible(self, manifest):
        assert manifest.spec.action == "deploy"
        assert manifest.spec.status == "success"
        assert manifest.spec.artifacts is not None

    def test_json_round_trip(self, manifest):
        json_str = manifest.model_dump_json(indent=2, exclude_none=True)
        loaded = DeploymentManifestModel.model_validate_json(json_str)
        assert loaded.spec.deployment_name == manifest.spec.deployment_name
        assert loaded.kind == PlatformKind.DEPLOYMENT_MANIFEST
        assert loaded.spec.artifacts.platform.hash == "sha256:abc123"

    def test_destroy_action(self):
        m = DeploymentManifestModel(
            meta=DeploymentManifestMetaModel(name="destroy_run"),
            spec=_make_spec(
                action="destroy",
                status="failed",
                stages=[ManifestStageModel(name="infra", status="failed", error="Destroy timed out")],
            ),
        )
        assert m.spec.action == "destroy"
        assert m.spec.stages[0].error == "Destroy timed out"

    def test_embedded_platform_content_round_trip(self):
        m = DeploymentManifestModel(
            meta=DeploymentManifestMetaModel(name="prod"),
            spec=_make_spec(
                artifacts=ManifestArtifactsModel(
                    platform=ManifestPlatformModel(
                        hash="sha256:abc",
                        path="build/platform.json",
                        content={"kind": "platform_model", "spec": {"deployment_name": "prod"}},
                    ),
                    providers=[ManifestArtifactProviderModel(name="tf_hetzner", type="terraform")],
                    images=[ManifestArtifactImageModel(name="traefik", image="traefik:v3.0.1")],
                )
            ),
        )
        json_str = m.model_dump_json(indent=2, exclude_none=True)
        loaded = DeploymentManifestModel.model_validate_json(json_str)
        assert loaded.spec.artifacts.platform.content["kind"] == "platform_model"
        assert loaded.spec.artifacts.providers[0].name == "tf_hetzner"
        assert loaded.spec.artifacts.images[0].image == "traefik:v3.0.1"

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


class TestManifestLockReferenceModel:
    """Tests for ManifestLockReferenceModel and its wiring into DeploymentManifestSpecModel."""

    def _make_lock(self, **kwargs) -> ManifestLockReferenceModel:
        defaults = dict(
            lock_id="a1b2c3d4-e5f6-7890-abcd-ef1234567890",
            backend="azurerm",
            acquired_at="2026-06-16T14:02:01Z",
            holder="alice@company.com",
            hostname="WORKSTATION-A",
        )
        defaults.update(kwargs)
        return ManifestLockReferenceModel(**defaults)

    def test_minimal_required_fields(self):
        lock = self._make_lock()
        assert lock.lock_id == "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
        assert lock.backend == "azurerm"
        assert lock.acquired_at == "2026-06-16T14:02:01Z"
        assert lock.holder == "alice@company.com"
        assert lock.hostname == "WORKSTATION-A"
        assert lock.released_at is None

    def test_released_at_optional(self):
        lock = self._make_lock(released_at="2026-06-16T14:32:01Z")
        assert lock.released_at == "2026-06-16T14:32:01Z"

    def test_all_backend_types_accepted(self):
        for backend in ("azurerm", "terraform_cloud", "s3", "consul", "gcs", "local"):
            lock = self._make_lock(backend=backend)
            assert lock.backend == backend

    def test_roundtrip_serialization(self):
        lock = self._make_lock(released_at="2026-06-16T14:32:01Z")
        data = lock.model_dump()
        restored = ManifestLockReferenceModel(**data)
        assert restored.lock_id == lock.lock_id
        assert restored.released_at == lock.released_at

    def test_missing_required_field_rejected(self):
        with pytest.raises(ValidationError):
            ManifestLockReferenceModel(  # type: ignore[call-arg]
                backend="azurerm",
                acquired_at="2026-06-16T14:02:01Z",
                holder="alice@company.com",
                # hostname missing
            )

    def test_wired_into_spec_absent_by_default(self):
        spec = _make_spec()
        assert spec.lock is None

    def test_wired_into_spec_accepts_lock(self):
        lock = self._make_lock()
        spec = _make_spec(lock=lock)
        assert spec.lock is not None
        assert spec.lock.backend == "azurerm"
        assert spec.lock.holder == "alice@company.com"
