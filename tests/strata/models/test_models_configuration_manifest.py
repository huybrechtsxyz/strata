"""Unit tests for ConfigurationManifestModel (ADR-0065 Phase 1 — unified durable storage)."""

import pytest
from pydantic import ValidationError

from strata.models.audit_config_model import RepositoryPushModel
from strata.models.configuration_model import (
    ConfigurationDeploymentModel,
    ConfigurationManifestModel,
)


class TestConfigurationManifestModel:
    def test_minimal(self):
        m = ConfigurationManifestModel()
        assert m.path == ".strata/deployments"
        assert m.push_manifest is False
        assert m.repository is None

    def test_custom_path(self):
        m = ConfigurationManifestModel(path="output/manifests")
        assert m.path == "output/manifests"

    def test_push_manifest_true(self):
        m = ConfigurationManifestModel(push_manifest=True)
        assert m.push_manifest is True

    def test_repository_accepts_repository_push_model(self):
        m = ConfigurationManifestModel(
            push_manifest=True,
            repository=RepositoryPushModel(push=True, name="state-repo", path="history/manifest"),
        )
        assert m.repository is not None
        assert m.repository.push is True
        assert m.repository.name == "state-repo"
        assert m.repository.path == "history/manifest"

    def test_repository_accepts_dict(self):
        m = ConfigurationManifestModel(repository={"push": True, "name": "state-repo"})
        assert m.repository is not None
        assert m.repository.name == "state-repo"

    def test_no_type_field(self):
        """type/repository(str)/branch/tag were dead configuration and are removed (ADR-0065)."""
        with pytest.raises(ValidationError):
            ConfigurationManifestModel(type="gitops")  # type: ignore[call-arg]


class TestConfigurationDeploymentModelWithManifest:
    def test_deployment_model_without_manifest(self):
        m = ConfigurationDeploymentModel(additional_properties=False)
        assert m.manifest is None

    def test_deployment_model_with_manifest(self):
        m = ConfigurationDeploymentModel(
            additional_properties=False,
            manifest={"path": "output/deploy"},
        )
        assert m.manifest is not None
        assert m.manifest.path == "output/deploy"

    def test_deployment_model_with_manifest_repository(self):
        m = ConfigurationDeploymentModel(
            additional_properties=False,
            manifest={
                "path": "manifests",
                "push_manifest": True,
                "repository": {"push": True, "name": "state", "path": "history/manifest"},
            },
        )
        assert m.manifest is not None
        assert m.manifest.repository is not None
        assert m.manifest.repository.name == "state"
