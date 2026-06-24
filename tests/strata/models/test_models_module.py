#!/usr/bin/env python3
"""
===============================================================================
Script Name   : test_models_module.py
Author        : Vincent Huybrechts
Version       : 1.0.0
Python Version: 3.12+
Description   : Module model using Pydantic for data validation and YAML parsing.
===============================================================================
"""

import os

import pytest
import yaml
from pydantic import ValidationError

from strata.models.common_models import SourceModel
from strata.models.module_model import (
    ModuleModel,
    ModuleMountModel,
    ModuleServiceEnvironmentModel,
    ModuleServiceModel,
)


@pytest.fixture(autouse=True)
def set_pythonpath_env(monkeypatch):
    monkeypatch.setenv("PYTHONPATH", "src")


MODULE_FOLDER = os.path.join(os.path.dirname(__file__), "..", "..", "data", "modules")

# List of YAML files to test (extensible)
MODULE_VALID_FILES = [
    os.path.join(MODULE_FOLDER, "module-standard.yaml"),
    os.path.join(
        os.path.dirname(__file__), "..", "..", "..", "config", "kamatera-swarm", "stack", "kamatera-mod-traefik.yaml"
    ),
]

# List of invalid YAML files to test (extensible)
MODULE_INVALID_FILES = [os.path.join(MODULE_FOLDER, "module-invalid.yaml")]


@pytest.mark.parametrize("yaml_path", MODULE_VALID_FILES)
def test_module_yaml_valid(yaml_path):
    """Test that a module YAML file is a valid ModuleModel."""
    with open(yaml_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    model = ModuleModel.model_validate(data)
    assert model is not None


@pytest.mark.parametrize("yaml_path", MODULE_INVALID_FILES)
def test_module_yaml_invalid(yaml_path):
    """Test that a module YAML file is NOT a valid ModuleModel."""
    with open(yaml_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    with pytest.raises(ValidationError):
        ModuleModel.model_validate(data)
    model = None
    assert model is None


# ---------------------------------------------------------------------------
# SourceModel — chart fields
# ---------------------------------------------------------------------------


class TestSourceModelChartFields:
    def test_git_source_valid(self):
        s = SourceModel(repository="my-repo", source_path="services/app")
        assert s.repository == "my-repo"
        assert s.source_path == "services/app"
        assert s.chart_name is None

    def test_chart_source_valid(self):
        s = SourceModel(chart_name="authentik", chart_repository="https://charts.goauthentik.io")
        assert s.chart_name == "authentik"
        assert s.chart_repository == "https://charts.goauthentik.io"
        assert s.chart_version is None
        assert s.repository is None

    def test_chart_source_with_version(self):
        s = SourceModel(
            chart_name="authentik",
            chart_version="2024.12.0",
            chart_repository="https://charts.goauthentik.io",
        )
        assert s.chart_version == "2024.12.0"

    def test_chart_source_oci(self):
        s = SourceModel(chart_name="app", chart_repository="oci://ghcr.io/org/charts")
        assert s.chart_repository.startswith("oci://")

    def test_no_source_raises(self):
        with pytest.raises(ValidationError):
            SourceModel()

    def test_mixed_git_and_chart_raises(self):
        with pytest.raises(ValidationError):
            SourceModel(
                repository="my-repo",
                source_path="services/app",
                chart_name="authentik",
                chart_repository="https://charts.goauthentik.io",
            )

    def test_chart_repository_without_chart_name_raises(self):
        with pytest.raises(ValidationError):
            SourceModel(chart_repository="https://charts.goauthentik.io")

    def test_repository_without_source_path_raises(self):
        with pytest.raises(ValidationError):
            SourceModel(repository="my-repo")


# ---------------------------------------------------------------------------
# ModuleMountModel — volume_ref and PVC fields
# ---------------------------------------------------------------------------


class TestModuleMountModel:
    def test_basic_bind_mount(self):
        m = ModuleMountModel(name="config", type="bind", source_path="./config", target_path="/etc/app")
        assert m.volume_ref is None
        assert m.storage_class is None

    def test_volume_ref_valid(self):
        m = ModuleMountModel(name="data", volume_ref="data", target_path="/var/lib/app")
        assert m.volume_ref == "data"

    def test_pvc_valid(self):
        m = ModuleMountModel(name="data", storage_class="standard", storage_size="10Gi", target_path="/data")
        assert m.storage_class == "standard"
        assert m.storage_size == "10Gi"

    def test_volume_ref_and_storage_class_mutually_exclusive(self):
        with pytest.raises(ValidationError, match="mutually exclusive"):
            ModuleMountModel(
                name="data",
                volume_ref="data",
                storage_class="standard",
                storage_size="10Gi",
                target_path="/data",
            )

    def test_storage_class_without_size_raises(self):
        with pytest.raises(ValidationError, match="storage_size is required"):
            ModuleMountModel(name="data", storage_class="standard", target_path="/data")


# ---------------------------------------------------------------------------
# ModuleServiceEnvironmentModel
# ---------------------------------------------------------------------------


class TestModuleServiceEnvironmentModel:
    def test_literal_value(self):
        e = ModuleServiceEnvironmentModel(key="TZ", value="Europe/Brussels")
        assert e.value == "Europe/Brussels"

    def test_secret_ref(self):
        e = ModuleServiceEnvironmentModel(key="DB_PASSWORD", secret="DB_PASSWORD")
        assert e.secret == "DB_PASSWORD"
        assert e.value is None

    def test_var_ref(self):
        e = ModuleServiceEnvironmentModel(key="APP_VERSION", var="APP_VERSION")
        assert e.var == "APP_VERSION"

    def test_feature_ref(self):
        e = ModuleServiceEnvironmentModel(key="ENABLE_METRICS", feature="ENABLE_METRICS")
        assert e.feature == "ENABLE_METRICS"

    def test_no_source_raises(self):
        with pytest.raises(ValidationError, match="exactly one"):
            ModuleServiceEnvironmentModel(key="MY_VAR")

    def test_multiple_sources_raises(self):
        with pytest.raises(ValidationError, match="multiple sources"):
            ModuleServiceEnvironmentModel(key="MY_VAR", value="foo", secret="bar")


# ---------------------------------------------------------------------------
# ModuleServiceModel
# ---------------------------------------------------------------------------


class TestModuleServiceModel:
    def test_minimal_service(self):
        s = ModuleServiceModel(name="redis", image="redis:alpine")
        assert s.name == "redis"
        assert s.image == "redis:alpine"
        assert s.environment is None
        assert s.depends_on is None

    def test_full_service(self):
        s = ModuleServiceModel(
            name="server",
            image="ghcr.io/goauthentik/server:2024.12",
            command=["server"],
            restart="unless-stopped",
            ports=["9000:9000"],
            environment=[
                ModuleServiceEnvironmentModel(key="SECRET_KEY", secret="AUTHENTIK_SECRET_KEY"),
                ModuleServiceEnvironmentModel(key="TZ", value="UTC"),
            ],
            depends_on=["postgresql", "redis"],
        )
        assert len(s.environment) == 2
        assert s.depends_on == ["postgresql", "redis"]

    def test_service_with_mount(self):
        s = ModuleServiceModel(
            name="postgresql",
            image="postgres:16-alpine",
            mounts=[ModuleMountModel(name="pgdata", volume_ref="data", target_path="/var/lib/postgresql/data")],
        )
        assert s.mounts[0].volume_ref == "data"


# ---------------------------------------------------------------------------
# ModuleSpecModel — services list + uniqueness + k8s fields
# ---------------------------------------------------------------------------


class TestModuleSpecModelServices:
    def _base_data(self):
        return {
            "apiVersion": "strata.huybrechts.xyz/v1",
            "kind": "module",
            "meta": {"name": "authentik"},
            "spec": {
                "type": "compose",
                "source": {"repository": "haven-modules", "source_path": "services/authentik"},
            },
        }

    def test_module_without_services_backward_compat(self):
        data = self._base_data()
        model = ModuleModel.model_validate(data)
        assert model.spec.services is None

    def test_module_with_services(self):
        data = self._base_data()
        data["spec"]["services"] = [
            {"name": "server", "image": "ghcr.io/goauthentik/server:2024.12"},
            {"name": "worker", "image": "ghcr.io/goauthentik/server:2024.12", "command": ["worker"]},
            {"name": "redis", "image": "redis:alpine"},
        ]
        model = ModuleModel.model_validate(data)
        assert len(model.spec.services) == 3
        assert model.spec.services[1].command == ["worker"]

    def test_duplicate_service_names_raises(self):
        data = self._base_data()
        data["spec"]["services"] = [
            {"name": "redis", "image": "redis:alpine"},
            {"name": "redis", "image": "redis:7-alpine"},
        ]
        with pytest.raises(ValidationError, match="Duplicate service names"):
            ModuleModel.model_validate(data)

    def test_release_name_and_kubernetes_namespace(self):
        data = self._base_data()
        data["spec"]["type"] = "helm"
        data["spec"]["source"] = {
            "chart_name": "authentik",
            "chart_version": "2024.12.0",
            "chart_repository": "https://charts.goauthentik.io",
        }
        data["spec"]["release_name"] = "authentik-prod"
        data["spec"]["kubernetes_namespace"] = "identity"
        model = ModuleModel.model_validate(data)
        assert model.spec.release_name == "authentik-prod"
        assert model.spec.kubernetes_namespace == "identity"

    def test_multi_service_with_env_refs(self):
        data = self._base_data()
        data["spec"]["references"] = {"secrets": ["DB_PASSWORD"]}
        data["spec"]["services"] = [
            {
                "name": "postgresql",
                "image": "postgres:16-alpine",
                "environment": [{"key": "POSTGRES_PASSWORD", "secret": "DB_PASSWORD"}],
                "mounts": [{"name": "pgdata", "volume_ref": "data", "target_path": "/var/lib/postgresql/data"}],
            }
        ]
        model = ModuleModel.model_validate(data)
        env = model.spec.services[0].environment[0]
        assert env.secret == "DB_PASSWORD"
        assert model.spec.services[0].mounts[0].volume_ref == "data"

    def test_helm_module_with_chart_source(self):
        data = self._base_data()
        data["spec"]["type"] = "helm"
        data["spec"]["source"] = {
            "chart_name": "traefik",
            "chart_version": "26.0.0",
            "chart_repository": "https://traefik.github.io/charts",
        }
        model = ModuleModel.model_validate(data)
        assert model.spec.source.chart_name == "traefik"
        assert model.spec.source.chart_version == "26.0.0"


# ---------------------------------------------------------------------------
# ModuleSpecModel — compose_file field
# ---------------------------------------------------------------------------


class TestModuleSpecModelComposeFile:
    def _base_data(self):
        return {
            "apiVersion": "strata.huybrechts.xyz/v1",
            "kind": "module",
            "meta": {"name": "traefik"},
            "spec": {
                "type": "compose",
                "source": {"repository": "infra-repo", "source_path": "services/traefik"},
            },
        }

    def test_compose_file_alone_is_valid(self):
        data = self._base_data()
        data["spec"]["compose_file"] = "@infra-repo/services/traefik/docker-compose.yml"
        model = ModuleModel.model_validate(data)
        assert model.spec.compose_file == "@infra-repo/services/traefik/docker-compose.yml"
        assert model.spec.services is None

    def test_compose_file_defaults_to_none(self):
        data = self._base_data()
        model = ModuleModel.model_validate(data)
        assert model.spec.compose_file is None

    def test_compose_file_and_services_mutually_exclusive(self):
        data = self._base_data()
        data["spec"]["compose_file"] = "@infra-repo/services/traefik/docker-compose.yml"
        data["spec"]["services"] = [
            {"name": "proxy", "image": "traefik:v3"},
        ]
        with pytest.raises(ValidationError, match="mutually exclusive"):
            ModuleModel.model_validate(data)

    def test_services_without_compose_file_still_valid(self):
        data = self._base_data()
        data["spec"]["services"] = [
            {"name": "proxy", "image": "traefik:v3"},
        ]
        model = ModuleModel.model_validate(data)
        assert model.spec.compose_file is None
        assert len(model.spec.services) == 1
