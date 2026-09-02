"""Tests for DeploymentService.apply_environment_overrides — configuration/custom deep merge.

Regression coverage for the bug where resource/module override `configuration`/`custom`
dicts were merged with a shallow `dict.update()` despite comments claiming "deep merge",
silently dropping nested sibling keys not repeated in the override.
"""

from unittest.mock import MagicMock

from strata.services.configuration_service import ConfigurationService
from strata.services.deployment_service import DeploymentService
from strata.services.environment_service import EnvironmentService


def _make_env_service_with_resource_override(
    resource_name: str, configuration: dict, custom: dict
) -> EnvironmentService:
    data = {
        "apiVersion": "strata.huybrechts.xyz/v1",
        "kind": "environment",
        "meta": {"name": "env_dev", "labels": None},
        "spec": {
            "overrides": {
                "resources": [
                    {
                        "resource": resource_name,
                        "configuration": configuration,
                        "custom": custom,
                    }
                ]
            }
        },
    }
    svc = EnvironmentService(data=data)
    svc.validate()
    return svc


def _make_env_service_with_module_override(
    module_name: str, resource_name: str, configuration: dict
) -> EnvironmentService:
    data = {
        "apiVersion": "strata.huybrechts.xyz/v1",
        "kind": "environment",
        "meta": {"name": "env_dev", "labels": None},
        "spec": {
            "overrides": {
                "modules": [
                    {
                        "module": module_name,
                        "resource": resource_name,
                        "configuration": configuration,
                    }
                ]
            }
        },
    }
    svc = EnvironmentService(data=data)
    svc.validate()
    return svc


def _make_deployment_service() -> DeploymentService:
    ds = DeploymentService.__new__(DeploymentService)
    ds._errors = []
    ds._validation_errors = []
    ds._structured_errors = []
    ds._repo_map = {}
    ds._validated = True  # bypass _ensure_validated checks
    ds.logger = MagicMock()
    ds.model = MagicMock()
    ds.model.spec = MagicMock()
    ds.model.meta = MagicMock()
    ds.model.meta.name = "test_deploy"
    return ds


def _make_workspace_resource(name: str, configuration: dict, custom: dict) -> MagicMock:
    resource = MagicMock()
    resource.name = name
    resource.description = None
    resource.enabled = None
    resource.condition = None
    resource.role = None
    resource.count = None
    resource.depends_on = None
    resource.references = None
    resource.firewalls = None
    resource.configuration = configuration
    resource.custom = custom
    resource.labels = None
    resource.tags = None
    resource.modules = None
    return resource


class TestResourceOverrideConfigurationDeepMerge:
    """Regression: resource override configuration/custom must deep-merge, not replace wholesale."""

    def test_nested_configuration_key_survives_partial_override(self):
        ws_resource = _make_workspace_resource(
            "manager",
            configuration={"vm_size": "Standard_D2s_v3", "network": {"subnet": "a", "nsg": "b"}},
            custom={"team": "platform", "costcenter": "base"},
        )
        ws_service = MagicMock()
        ws_service.model = MagicMock()
        ws_service.model.spec = MagicMock()
        ws_service.model.spec.resources = [ws_resource]
        ws_service.model.spec.providers = []

        env_service = _make_env_service_with_resource_override(
            "manager",
            configuration={"network": {"nsg": "override-nsg"}},
            custom={"costcenter": "prd"},
        )

        ds = _make_deployment_service()
        ds._workspace_service = ws_service
        ds._environment_service = env_service

        config_service = MagicMock(spec=ConfigurationService)
        config_service.model = MagicMock()
        config_service.model.spec.remotes = []

        from unittest.mock import patch

        with patch.object(ConfigurationService, "get_instance", return_value=config_service):
            ok, errors = ds.apply_environment_overrides()

        assert ok, errors
        # Sibling key at top level untouched by override — must survive
        assert ws_resource.configuration["vm_size"] == "Standard_D2s_v3"
        # Nested sibling key ('subnet') not repeated in override — must survive
        assert ws_resource.configuration["network"]["subnet"] == "a"
        # Nested key overridden
        assert ws_resource.configuration["network"]["nsg"] == "override-nsg"
        # custom: team only in base — must survive; costcenter overridden
        assert ws_resource.custom["team"] == "platform"
        assert ws_resource.custom["costcenter"] == "prd"


class TestModuleOverrideConfigurationDeepMerge:
    """Regression: module override configuration must deep-merge, not replace wholesale."""

    def test_nested_configuration_key_survives_partial_override(self):
        target_module = MagicMock()
        target_module.name = "traefik"
        target_module.slot_type = "main"
        target_module.enabled = None
        target_module.configuration = {"chart_version": "28.0.0", "values": {"replicas": 2, "ingressClass": "nginx"}}

        ws_resource = MagicMock()
        ws_resource.name = "cluster"
        ws_resource.modules = [target_module]

        ws_service = MagicMock()
        ws_service.model = MagicMock()
        ws_service.model.spec = MagicMock()
        ws_service.model.spec.resources = [ws_resource]
        ws_service.model.spec.providers = []

        env_service = _make_env_service_with_module_override(
            "traefik",
            "cluster",
            configuration={"values": {"replicas": 5}},
        )

        ds = _make_deployment_service()
        ds._workspace_service = ws_service
        ds._environment_service = env_service

        config_service = MagicMock(spec=ConfigurationService)
        config_service.model = MagicMock()
        config_service.model.spec.remotes = []

        from unittest.mock import patch

        with patch.object(ConfigurationService, "get_instance", return_value=config_service):
            ok, errors = ds.apply_environment_overrides()

        assert ok, errors
        # Sibling top-level key untouched by override — must survive
        assert target_module.configuration["chart_version"] == "28.0.0"
        # Nested sibling key ('ingressClass') not repeated in override — must survive
        assert target_module.configuration["values"]["ingressClass"] == "nginx"
        # Nested key overridden
        assert target_module.configuration["values"]["replicas"] == 5
