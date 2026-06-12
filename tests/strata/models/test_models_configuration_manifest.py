"""Unit tests for ConfigurationManifestModel and ManifestStoreType."""

import pytest
from pydantic import ValidationError

from strata.models.configuration_model import (
    ConfigurationDeploymentModel,
    ConfigurationManifestModel,
    ManifestStoreType,
)


class TestManifestStoreType:
    def test_local_value(self):
        assert ManifestStoreType.LOCAL == "local"

    def test_gitops_value(self):
        assert ManifestStoreType.GITOPS == "gitops"


class TestConfigurationManifestModelLocal:
    def test_minimal_local(self):
        m = ConfigurationManifestModel(type="local")
        assert m.type == ManifestStoreType.LOCAL
        assert m.path == ".strata/deployments"
        assert m.repository is None
        assert m.branch is None
        assert m.tag is True

    def test_local_custom_path(self):
        m = ConfigurationManifestModel(type="local", path="output/manifests")
        assert m.path == "output/manifests"

    def test_local_tag_false(self):
        m = ConfigurationManifestModel(type="local", tag=False)
        assert m.tag is False

    def test_local_ignores_repository_and_branch(self):
        """Local type doesn't require repository/branch — they are ignored if present."""
        m = ConfigurationManifestModel(type="local", repository="some-repo", branch="main")
        assert m.repository == "some-repo"
        assert m.branch == "main"


class TestConfigurationManifestModelGitops:
    def test_valid_gitops(self):
        m = ConfigurationManifestModel(type="gitops", repository="state-repo", branch="manifests")
        assert m.type == ManifestStoreType.GITOPS
        assert m.repository == "state-repo"
        assert m.branch == "manifests"
        assert m.tag is True

    def test_gitops_custom_path_and_tag(self):
        m = ConfigurationManifestModel(
            type="gitops", repository="ops", branch="deploy", path="state/manifests", tag=False
        )
        assert m.path == "state/manifests"
        assert m.tag is False

    def test_gitops_missing_repository_rejected(self):
        with pytest.raises(ValidationError, match="repository is required"):
            ConfigurationManifestModel(type="gitops", branch="main")

    def test_gitops_missing_branch_rejected(self):
        with pytest.raises(ValidationError, match="branch is required"):
            ConfigurationManifestModel(type="gitops", repository="my-repo")

    def test_gitops_missing_both_rejected(self):
        with pytest.raises(ValidationError, match="repository is required"):
            ConfigurationManifestModel(type="gitops")


class TestConfigurationManifestModelInvalid:
    def test_invalid_type_rejected(self):
        with pytest.raises(ValidationError):
            ConfigurationManifestModel(type="s3")

    def test_missing_type_rejected(self):
        with pytest.raises(ValidationError):
            ConfigurationManifestModel()  # type: ignore[call-arg]


class TestConfigurationDeploymentModelWithManifest:
    def test_deployment_model_without_manifest(self):
        m = ConfigurationDeploymentModel(additional_properties=False)
        assert m.manifest is None

    def test_deployment_model_with_local_manifest(self):
        m = ConfigurationDeploymentModel(
            additional_properties=False,
            manifest={"type": "local", "path": "output/deploy"},
        )
        assert m.manifest is not None
        assert m.manifest.type == ManifestStoreType.LOCAL
        assert m.manifest.path == "output/deploy"

    def test_deployment_model_with_gitops_manifest(self):
        m = ConfigurationDeploymentModel(
            additional_properties=False,
            manifest={"type": "gitops", "repository": "state", "branch": "main", "path": "manifests"},
        )
        assert m.manifest is not None
        assert m.manifest.type == ManifestStoreType.GITOPS
        assert m.manifest.repository == "state"
        assert m.manifest.branch == "main"
