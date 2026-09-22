#!/usr/bin/env python3
"""Tests for ModuleModel YAML validation."""

import pytest
from pydantic import ValidationError

from strata.models.module_model import ModuleModel


def _minimal_module() -> dict:
    return {
        "meta": {"name": "authentik"},
        "spec": {
            "source": {"chart_name": "authentik", "remote": "goauthentik"},
            "default_labels": {"app.kubernetes.io/name": "authentik"},
        },
    }


def test_module_minimal_is_valid():
    """A minimal module document (only required fields) validates successfully."""
    model = ModuleModel.model_validate(_minimal_module())
    assert model.meta.name == "authentik"
    assert model.spec.source.chart_name == "authentik"
    assert model.apiVersion.value == "strata.huybrechts.xyz/v2"
    assert model.kind.value == "module"


def test_module_accepts_workload_deployer_type():
    """type accepts any workload deployer tool (helm/compose/argocd/script)."""
    data = _minimal_module()
    data["spec"]["type"] = "helm"
    model = ModuleModel.model_validate(data)
    assert model.spec.type == "helm"


def test_module_rejects_non_workload_deployer_type():
    """type rejects infra-provisioning tools (terraform/ansible/bicep) — not workload deployers."""
    data = _minimal_module()
    data["spec"]["type"] = "terraform"
    with pytest.raises(ValidationError):
        ModuleModel.model_validate(data)


def test_module_rejects_opentofu_type():
    """type rejects opentofu too — a recognized, terraform-compatible infra tool, not a workload deployer."""
    data = _minimal_module()
    data["spec"]["type"] = "opentofu"
    with pytest.raises(ValidationError):
        ModuleModel.model_validate(data)


def test_module_accepts_unknown_custom_provisioner_type():
    """An unrecognized type (e.g. a custom provisioner plugin name) is allowed through.

    v1's real DeployerFactory supports user-registered provisioner plugins
    beyond the built-in tools — validating whether a custom name is actually
    a workload deployer requires the plugin registry (deferred to Phase 2,
    not built in v2 yet), so unknown values pass Phase 1 unchecked.
    """
    data = _minimal_module()
    data["spec"]["type"] = "pulumi"
    model = ModuleModel.model_validate(data)
    assert model.spec.type == "pulumi"


def test_module_source_rejects_mixed_git_and_chart():
    """A source mixing git-based and chart-based selection is rejected.

    `remote` is shared by both modes, so the mix is detected via the
    selection fields: source_path (git) alongside chart_name (chart).
    """
    data = _minimal_module()
    data["spec"]["source"]["source_path"] = "helm/authentik"
    with pytest.raises(ValidationError):
        ModuleModel.model_validate(data)


def test_module_source_git_based_is_valid():
    """A git-based source (remote + source_path) validates successfully."""
    data = _minimal_module()
    data["spec"]["source"] = {"remote": "my-infra-repo", "source_path": "helm/authentik"}
    model = ModuleModel.model_validate(data)
    assert model.spec.source.remote == "my-infra-repo"


def test_module_source_rejects_path_traversal():
    """A source_path containing '..' is rejected."""
    data = _minimal_module()
    data["spec"]["source"] = {"remote": "my-infra-repo", "source_path": "../etc/passwd"}
    with pytest.raises(ValidationError):
        ModuleModel.model_validate(data)


def test_module_source_rejects_absolute_path():
    """A source_path that is absolute is rejected."""
    data = _minimal_module()
    data["spec"]["source"] = {"remote": "my-infra-repo", "source_path": "/etc/passwd"}
    with pytest.raises(ValidationError):
        ModuleModel.model_validate(data)


def test_module_source_chart_requires_remote():
    """A chart-based source must name a remote — a chart always comes from a registry."""
    data = _minimal_module()
    data["spec"]["source"] = {"chart_name": "authentik"}
    with pytest.raises(ValidationError, match="remote is required for chart-based"):
        ModuleModel.model_validate(data)


def test_module_source_git_may_omit_remote():
    """A git-based source may omit remote, meaning this solution's own repository."""
    data = _minimal_module()
    data["spec"]["source"] = {"source_path": "helm/authentik"}
    model = ModuleModel.model_validate(data)
    assert model.spec.source.remote is None
    assert model.spec.source.source_path == "helm/authentik"


def test_module_source_rejects_chart_version_on_git_source():
    """chart_version is meaningless for a git-based source."""
    data = _minimal_module()
    data["spec"]["source"] = {"source_path": "helm/authentik", "chart_version": "1.0.0"}
    with pytest.raises(ValidationError, match="chart_version is only valid"):
        ModuleModel.model_validate(data)


def test_module_accepts_services_with_value_token_env():
    """A service environment variable may embed '${secret:}' tokens instead of a literal."""
    data = _minimal_module()
    data["spec"]["services"] = [
        {
            "name": "server",
            "image": "ghcr.io/goauthentik/server:2024.12",
            "environment": [{"key": "POSTGRES_PASSWORD", "value": "${secret:DB_PASSWORD}"}],
        }
    ]
    model = ModuleModel.model_validate(data)
    assert model.spec.services[0].environment[0].value == "${secret:DB_PASSWORD}"


def test_module_rejects_malformed_value_token_in_env():
    """An unknown token kind in a service environment value is rejected at Phase 1."""
    data = _minimal_module()
    data["spec"]["services"] = [
        {"name": "server", "environment": [{"key": "APP_VERSION", "value": "${vars:APP_VERSION}"}]}
    ]
    with pytest.raises(ValidationError):
        ModuleModel.model_validate(data)


def test_module_rejects_duplicate_service_names():
    """Duplicate service names within a module are rejected."""
    data = _minimal_module()
    data["spec"]["services"] = [{"name": "server"}, {"name": "server"}]
    with pytest.raises(ValidationError):
        ModuleModel.model_validate(data)


def test_module_accepts_valid_intra_module_depends_on():
    """A depends_on referencing another real service in the same module is accepted."""
    data = _minimal_module()
    data["spec"]["services"] = [
        {"name": "server", "depends_on": ["redis"]},
        {"name": "redis"},
    ]
    model = ModuleModel.model_validate(data)
    assert model.spec.services[0].depends_on == ["redis"]


def test_module_rejects_unknown_intra_module_depends_on():
    """A depends_on referencing a service not defined in this module is rejected."""
    data = _minimal_module()
    data["spec"]["services"] = [{"name": "server", "depends_on": ["nonexistent"]}]
    with pytest.raises(ValidationError):
        ModuleModel.model_validate(data)


def test_module_accepts_cross_module_depends_on_syntax():
    """A syntactically valid '@module/service' cross-module depends_on is accepted."""
    data = _minimal_module()
    data["spec"]["services"] = [{"name": "server", "depends_on": ["@mod_auth/server"]}]
    model = ModuleModel.model_validate(data)
    assert model.spec.services[0].depends_on == ["@mod_auth/server"]


def test_module_rejects_malformed_cross_module_depends_on():
    """A malformed '@module/service' cross-module depends_on (empty service part) is rejected."""
    data = _minimal_module()
    data["spec"]["services"] = [{"name": "server", "depends_on": ["@mod_auth/"]}]
    with pytest.raises(ValidationError):
        ModuleModel.model_validate(data)


def test_module_rejects_compose_file_and_services_together():
    """compose_file and services are mutually exclusive."""
    data = _minimal_module()
    data["spec"]["compose_file"] = "@repo/docker-compose.yml"
    data["spec"]["services"] = [{"name": "server"}]
    with pytest.raises(ValidationError):
        ModuleModel.model_validate(data)


def test_module_mount_rejects_volume_ref_and_storage_class_together():
    """A mount cannot set both volume_ref and storage_class."""
    data = _minimal_module()
    data["spec"]["properties"] = {
        "mounts": [{"name": "data", "volume_ref": "data-vol", "storage_class": "standard", "storage_size": "10Gi"}]
    }
    with pytest.raises(ValidationError):
        ModuleModel.model_validate(data)


def test_module_mount_requires_storage_size_with_storage_class():
    """A mount with storage_class but no storage_size is rejected."""
    data = _minimal_module()
    data["spec"]["properties"] = {"mounts": [{"name": "data", "storage_class": "standard"}]}
    with pytest.raises(ValidationError):
        ModuleModel.model_validate(data)


def test_module_file_glob_requires_dir_target():
    """A glob source pattern requires a directory (trailing '/') target."""
    data = _minimal_module()
    data["spec"]["files"] = [{"source": "@infra/services/traefik/*", "target": "traefik.yaml"}]
    with pytest.raises(ValidationError):
        ModuleModel.model_validate(data)


def test_module_file_accepts_cross_repo_glob_with_dir_target():
    """A well-formed '@repo/' glob source with a directory target is accepted."""
    data = _minimal_module()
    data["spec"]["files"] = [{"source": "@infra/services/traefik/*", "target": "config/"}]
    model = ModuleModel.model_validate(data)
    assert model.spec.files[0].target == "config/"


def test_module_file_rejects_path_traversal_in_target():
    """A target escaping the build output directory via '..' is rejected."""
    data = _minimal_module()
    data["spec"]["files"] = [{"source": "traefik.yaml", "target": "../../etc/traefik.yaml"}]
    with pytest.raises(ValidationError):
        ModuleModel.model_validate(data)


def test_module_file_rejects_absolute_path_target():
    """An absolute target path is rejected."""
    data = _minimal_module()
    data["spec"]["files"] = [{"source": "traefik.yaml", "target": "/etc/traefik.yaml"}]
    with pytest.raises(ValidationError):
        ModuleModel.model_validate(data)


def test_module_file_rejects_path_traversal_in_cross_repo_source():
    """A '@repo/' source escaping the repo root via '..' is rejected."""
    data = _minimal_module()
    data["spec"]["files"] = [{"source": "@infra/../../../etc/passwd", "target": "config/"}]
    with pytest.raises(ValidationError):
        ModuleModel.model_validate(data)


def test_module_rejects_references_field():
    """spec.references is rejected (Requirement was removed as a schema concept — ADR-0002)."""
    data = _minimal_module()
    data["spec"]["references"] = {"variables": ["APP_VERSION"]}
    with pytest.raises(ValidationError):
        ModuleModel.model_validate(data)


def test_module_rejects_unknown_fields():
    """Extra/unknown fields are rejected (extra='forbid')."""
    data = _minimal_module()
    data["spec"]["unknown_field"] = "oops"
    with pytest.raises(ValidationError):
        ModuleModel.model_validate(data)
