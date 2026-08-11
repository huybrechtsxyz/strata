"""Tests for DeploymentService.load_environment_only (ADR-0026 Path B).

Verifies the lightweight environment-only loader used by ``deploy show`` and
``values list/get/resolve`` never touches the workspace, while still producing
a fully usable EnvironmentService (declared variables/secrets/features) and
merge provenance — exactly what ``load_deploy_services()`` would set, minus
the workspace/provider/resource/module resolution.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import yaml

from strata.services.configuration_service import ConfigurationService
from strata.services.deployment_service import DeploymentService


def _repo_root() -> Path:
    return Path(__file__).parent.parent.parent.parent


def _load_deployment() -> DeploymentService:
    # Deliberately bypasses DeploymentService.load()'s process-lifetime L1 cache
    # (strata.utils.service_cache) — each test needs its own fresh instance since
    # load_environment_only() mutates instance state (_environment_service).
    deployment_file = _repo_root() / "tests" / "data" / "deployments" / "deployment-standard.yaml"
    data = yaml.safe_load(deployment_file.read_text(encoding="utf-8"))
    ds = DeploymentService(path=str(deployment_file), data=data)
    ok, errors = ds.validate()
    assert ok, errors
    return ds


def _patched_config_service():
    fake_config = MagicMock()
    fake_config.get_remote_map.return_value = {}
    return patch.object(ConfigurationService, "get_instance", return_value=fake_config)


class TestLoadEnvironmentOnly:
    def test_loads_declared_variables_without_workspace(self) -> None:
        ds = _load_deployment()
        with _patched_config_service():
            ok = ds.load_environment_only(str(_repo_root()))
        assert ok is True
        assert ds._workspace_service is None

        env_service = ds.get_environment_service()
        assert env_service is not None
        keys = {v.key for v in env_service.get_variables()}
        assert keys == {"WORKSPACE", "DATACENTER", "KAMATERA_MANAGER_ID"}

    def test_declared_secrets_are_present_but_unresolved(self) -> None:
        ds = _load_deployment()
        with _patched_config_service():
            assert ds.load_environment_only(str(_repo_root())) is True

        env_service = ds.get_environment_service()
        secret_keys = {s.key for s in env_service.get_secrets()}
        assert "TERRAFORM_API_TOKEN" in secret_keys
        # declarations only — no store call happened here (that's ValueController's job)

    def test_second_call_is_a_cache_hit_noop(self) -> None:
        ds = _load_deployment()
        with _patched_config_service():
            assert ds.load_environment_only(str(_repo_root())) is True
            first_env_service = ds.get_environment_service()
            assert ds.load_environment_only(str(_repo_root())) is True
        assert ds.get_environment_service() is first_env_service

    def test_invalid_objects_path_returns_false(self) -> None:
        ds = _load_deployment()
        ok = ds.load_environment_only(str(_repo_root() / "does-not-exist-dir"))
        assert ok is False

    def test_apply_remote_overrides_works_without_workspace(self) -> None:
        """apply_remote_overrides() only needs the environment service — unlike
        apply_environment_overrides(), it must not require a workspace to be loaded."""
        ds = _load_deployment()
        with _patched_config_service():
            assert ds.load_environment_only(str(_repo_root())) is True

        # No remote overrides declared in the fixture environment — should succeed trivially.
        ok, errors = ds.apply_remote_overrides()
        assert ok is True
        assert errors == []
