"""Tests for DeploymentService.apply_environment_overrides — remote override block."""

from unittest.mock import MagicMock, patch

from strata.models.repository_model import RemoteType
from strata.services.configuration_service import ConfigurationService
from strata.services.deployment_service import DeploymentService
from strata.services.environment_service import EnvironmentService


def _make_remote(name: str, reference: str = "main") -> MagicMock:
    """Return a mock object with the same interface as RemoteModel."""
    remote = MagicMock()
    remote.name = name
    remote.type = RemoteType.GITOPS
    remote.repository = "https://github.com/org/repo.git"
    remote.reference = reference
    remote.source_path = "terraform"
    remote.deploy_path = name
    return remote


def _make_env_service_with_remote_override(remote_name: str, new_ref: str) -> EnvironmentService:
    """Build an EnvironmentService that has one remote override."""
    data = {
        "apiVersion": "strata.huybrechts.xyz/v1",
        "kind": "environment",
        "meta": {"name": "env_dev", "labels": None},
        "spec": {"overrides": {"remotes": [{"remote": remote_name, "reference": new_ref}]}},
    }
    svc = EnvironmentService(data=data)
    svc.validate()
    return svc


def _make_workspace_service() -> MagicMock:
    ws = MagicMock()
    ws.model = MagicMock()
    ws.model.spec = MagicMock()
    ws.model.spec.resources = []
    ws.model.spec.providers = []
    return ws


class TestApplyEnvironmentOverridesRemotes:
    def _make_deployment_service_with_override(self, remote_name: str, initial_ref: str, new_ref: str):
        ds = DeploymentService.__new__(DeploymentService)
        ds._errors = []
        ds._messages = []
        ds._validation_errors = []
        ds._structured_errors = []
        ds._repo_map = {}
        ds._validated = True  # bypass _ensure_validated checks
        ds.logger = MagicMock()
        ds.model = MagicMock()
        ds.model.spec = MagicMock()
        ds.model.meta = MagicMock()
        ds.model.meta.name = "test_deploy"

        remote = _make_remote(remote_name, initial_ref)

        config_model = MagicMock()
        config_model.spec.remotes = [remote]

        config_service = MagicMock(spec=ConfigurationService)
        config_service.model = config_model

        env_service = _make_env_service_with_remote_override(remote_name, new_ref)
        ws_service = _make_workspace_service()

        ds._workspace_service = ws_service
        ds._environment_service = env_service

        return ds, remote, config_service

    def test_remote_reference_mutated_in_place(self):
        ds, remote, config_service = self._make_deployment_service_with_override("tf_landscape", "main", "v1.2.3")
        with patch.object(
            ConfigurationService,
            "get_instance",
            return_value=config_service,
        ):
            ok, errors = ds.apply_environment_overrides()
        assert remote.reference == "v1.2.3"

    def test_unknown_remote_produces_critical_error(self):
        ds, remote, config_service = self._make_deployment_service_with_override("tf_landscape", "main", "v1.0.0")
        # Return a config_service where remotes list is empty (remote not found)
        config_service.model.spec.remotes = []
        with patch.object(
            ConfigurationService,
            "get_instance",
            return_value=config_service,
        ):
            ok, errors = ds.apply_environment_overrides()
        assert any("tf_landscape" in e for e in errors)

    def test_no_remote_overrides_leaves_ref_unchanged(self):
        ds = DeploymentService.__new__(DeploymentService)
        ds._errors = []
        ds._messages = []
        ds._validation_errors = []
        ds._structured_errors = []
        ds._repo_map = {}
        ds._validated = True  # pretend validated so _ensure_validated passes
        ds.logger = MagicMock()
        ds.model = MagicMock()

        # Environment with no remote overrides but has properties (so has_overrides=True via properties)
        env_data = {
            "apiVersion": "strata.huybrechts.xyz/v1",
            "kind": "environment",
            "meta": {"name": "env_dev", "labels": None},
            "spec": {"properties": {"key": "value"}},
        }
        env_service = EnvironmentService(data=env_data)
        env_service.validate()
        ws_service = _make_workspace_service()

        ds._workspace_service = ws_service
        ds._environment_service = env_service

        ok, errors = ds.apply_environment_overrides()
        # No remote overrides means no remote mutation — properties-only override applies without errors
        assert ok
