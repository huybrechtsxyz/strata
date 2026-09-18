#!/usr/bin/env python3
"""
===============================================================================
Script Name   : test_models_workspace.py
Author        : Vincent Huybrechts
Version       : 1.0.0
Python Version: 3.12+
Description   : Workspace model using Pydantic for data validation and YAML parsing.
===============================================================================
"""

import os

import pytest
import yaml
from pydantic import ValidationError

from strata.models.common_models import ProvisionerType, SourceModel
from strata.models.workspace_model import (
    OutputProfileModel,
    ProvisionerReferencesModel,
    WorkspaceIacBackendModel,
    WorkspaceIacModel,
    WorkspaceModel,
    WorkspaceResourceModel,
)


@pytest.fixture(autouse=True)
def set_pythonpath_env(monkeypatch):
    monkeypatch.setenv("PYTHONPATH", "src")


WORKSPACE_FOLDER = os.path.join(os.path.dirname(__file__), "..", "..", "data", "workspaces")

# List of YAML files to test (extensible)
WORKSPACE_VALID_FILES = [
    os.path.join(WORKSPACE_FOLDER, "workspace-standard.yaml"),
    os.path.join(
        os.path.dirname(__file__), "..", "..", "..", "config", "kamatera-swarm", "stack", "kamatera-ws-platform.yaml"
    ),
]

# List of invalid YAML files to test (extensible)
WORKSPACE_INVALID_FILES = [os.path.join(WORKSPACE_FOLDER, "workspace-invalid.yaml")]


@pytest.mark.parametrize("yaml_path", WORKSPACE_VALID_FILES)
def test_workspace_yaml_valid(yaml_path):
    """Test that a workspace YAML file is a valid WorkspaceModel."""
    with open(yaml_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    model = WorkspaceModel.model_validate(data)
    assert model is not None


@pytest.mark.parametrize("yaml_path", WORKSPACE_INVALID_FILES)
def test_workspace_yaml_invalid(yaml_path):
    """Test that a workspace YAML file is NOT a valid WorkspaceModel."""
    with open(yaml_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    with pytest.raises(ValidationError):
        WorkspaceModel.model_validate(data)
    model = None
    assert model is None


class TestRemovedReferencesField:
    """ADR-0078: the inert cross-resource 'references' field was removed."""

    def test_references_is_not_a_field(self):
        assert "references" not in WorkspaceResourceModel.model_fields

    def test_legacy_references_key_is_now_rejected(self):
        """The deprecation shim served its cycle in v1.10.0 and has been removed.

        A user who upgraded through 1.10.0 was warned; `extra="forbid"` now rejects
        the key outright, which is the point of a deprecation period ending.
        """
        with pytest.raises(ValidationError):
            WorkspaceResourceModel.model_validate(
                {"name": "app_tier", "file": "config/app.yaml", "references": {"db": "storage.conn"}}
            )

    def test_unknown_keys_still_rejected(self):
        with pytest.raises(ValidationError):
            WorkspaceResourceModel.model_validate({"name": "app_tier", "file": "config/app.yaml", "bogus": 1})


class TestSourceModelSingleRepo:
    def test_source_path_only_is_valid(self):
        """Phase 1: source_path without repository must pass SourceModel validation."""
        model = SourceModel(source_path="terraform")
        assert model.source_path == "terraform"
        assert model.repository is None

    def test_repository_plus_source_path_is_valid(self):
        """Phase 1: explicit repository + source_path must still pass."""
        model = SourceModel(repository="my_repo", source_path="terraform")
        assert str(model.repository) == "my_repo"
        assert model.source_path == "terraform"

    def test_neither_git_nor_chart_raises(self):
        """Phase 1: completely empty SourceModel must fail validation."""
        with pytest.raises(ValidationError):
            SourceModel()

    def test_repository_without_source_path_raises(self):
        """Phase 1: repository alone (no source_path) must fail — source_path is required."""
        with pytest.raises(ValidationError):
            SourceModel(repository="my_repo")


class TestProvisionerReferencesModel:
    """ADR-0078 Gap 1: WorkspaceIacModel.references opts a provisioner (e.g. script,
    which has no component document of its own) into scoped injection."""

    def test_all_fields_optional(self):
        model = ProvisionerReferencesModel.model_validate({})
        assert model.variables is None
        assert model.secrets is None
        assert model.features is None

    def test_fields_validate_independently(self):
        model = ProvisionerReferencesModel.model_validate(
            {"variables": ["a", "b"], "secrets": ["c"], "features": ["d"]}
        )
        assert model.variables == ["a", "b"]
        assert model.secrets == ["c"]
        assert model.features == ["d"]

    def test_workspace_iac_model_references_round_trips(self):
        model = WorkspaceIacModel(
            name="bootstrap",
            provisioner=ProvisionerType.SCRIPT,
            source=SourceModel(repository="haven", source_path="scripts"),
            references=ProvisionerReferencesModel(variables=["service_connection_id"]),
        )
        assert model.references is not None
        assert model.references.variables == ["service_connection_id"]

    def test_workspace_iac_model_references_defaults_to_none(self):
        model = WorkspaceIacModel(
            name="infra",
            provisioner=ProvisionerType.TERRAFORM,
            source=SourceModel(repository="haven", source_path="terraform"),
        )
        assert model.references is None


class TestWorkspaceIacModelProvisionerFieldValidation:
    """Tests for WorkspaceIacModel.validate_provisioner_fields() (ADR-0071) — previously
    had zero direct test coverage. Covers the pre-existing source/properties checks and
    the new backend/output restrictions added alongside ADR-0071's finding that neither
    was validator-restricted to terraform the way properties already is to ansible."""

    def _source(self) -> SourceModel:
        return SourceModel(repository="my_repo", source_path="terraform")

    def test_source_required_for_non_sync_provisioner(self):
        with pytest.raises(ValidationError, match="'source' is required"):
            WorkspaceIacModel(name="infra", provisioner=ProvisionerType.TERRAFORM, source=None)

    def test_source_not_required_for_sync_provisioner(self):
        model = WorkspaceIacModel(name="sync", provisioner=ProvisionerType.ARGOCD, source=None)
        assert model.source is None

    def test_properties_allowed_on_ansible(self):
        model = WorkspaceIacModel(
            name="config",
            provisioner=ProvisionerType.ANSIBLE,
            source=self._source(),
            properties={"playbook": "site.yml"},
        )
        assert model.properties is not None

    def test_properties_rejected_on_terraform(self):
        with pytest.raises(ValidationError, match="'properties' is only supported for ansible"):
            WorkspaceIacModel(
                name="infra",
                provisioner=ProvisionerType.TERRAFORM,
                source=self._source(),
                properties={"playbook": "site.yml"},
            )

    def test_backend_allowed_on_terraform(self):
        model = WorkspaceIacModel(
            name="infra",
            provisioner=ProvisionerType.TERRAFORM,
            source=self._source(),
            backend=WorkspaceIacBackendModel(type="local", configuration={}),
        )
        assert model.backend is not None

    def test_backend_rejected_on_bicep(self):
        with pytest.raises(ValidationError, match="'backend' is only supported for terraform"):
            WorkspaceIacModel(
                name="infra",
                provisioner=ProvisionerType.BICEP,
                source=self._source(),
                backend=WorkspaceIacBackendModel(type="local", configuration={}),
            )

    def test_backend_rejected_on_ansible(self):
        with pytest.raises(ValidationError, match="'backend' is only supported for terraform"):
            WorkspaceIacModel(
                name="config",
                provisioner=ProvisionerType.ANSIBLE,
                source=self._source(),
                backend=WorkspaceIacBackendModel(type="local", configuration={}),
            )

    def test_output_allowed_on_terraform(self):
        model = WorkspaceIacModel(
            name="infra",
            provisioner=ProvisionerType.TERRAFORM,
            source=self._source(),
            output=OutputProfileModel(),
        )
        assert model.output is not None

    def test_output_rejected_on_compose(self):
        with pytest.raises(ValidationError, match="'output' is only supported for terraform"):
            WorkspaceIacModel(
                name="app",
                provisioner=ProvisionerType.COMPOSE,
                source=self._source(),
                output=OutputProfileModel(),
            )

    def test_output_rejected_on_helm(self):
        with pytest.raises(ValidationError, match="'output' is only supported for terraform"):
            WorkspaceIacModel(
                name="app",
                provisioner=ProvisionerType.HELM,
                source=self._source(),
                output=OutputProfileModel(),
            )

    def test_integration_allowed_on_terraform(self):
        model = WorkspaceIacModel(
            name="infra",
            provisioner=ProvisionerType.TERRAFORM,
            source=self._source(),
            integration="terraform_current",
        )
        assert model.integration == "terraform_current"

    def test_integration_unset_by_default(self):
        model = WorkspaceIacModel(
            name="infra",
            provisioner=ProvisionerType.TERRAFORM,
            source=self._source(),
        )
        assert model.integration is None

    def test_integration_allowed_on_ansible(self):
        """ADR-0080: ansible is provisioner-scoped (has _iac_model), wired into
        IntegrationService.resolve_for_provisioner() same as terraform."""
        model = WorkspaceIacModel(
            name="config",
            provisioner=ProvisionerType.ANSIBLE,
            source=self._source(),
            integration="config_mgmt",
        )
        assert model.integration == "config_mgmt"

    def test_integration_allowed_on_bicep(self):
        """ADR-0080: bicep is provisioner-scoped (has _iac_model), wired into
        IntegrationService.resolve_for_provisioner() (matching AzureCLIIntegration)."""
        model = WorkspaceIacModel(
            name="infra",
            provisioner=ProvisionerType.BICEP,
            source=self._source(),
            integration="azure_main",
        )
        assert model.integration == "azure_main"

    def test_integration_rejected_on_helm(self):
        """ADR-0080: helm IS wired into an integration lookup (resolve_by_class), but it isn't
        provisioner-scoped (no _iac_model — a stage can deploy many charts), so there's no
        addressable provisioner entry for an explicit 'integration:' override to target."""
        with pytest.raises(ValidationError, match="'integration' is not supported for provisioner type"):
            WorkspaceIacModel(
                name="app",
                provisioner=ProvisionerType.HELM,
                source=self._source(),
                integration="helm_registry",
            )

    def test_integration_rejected_on_compose(self):
        """ADR-0080: same reasoning as helm above — compose isn't provisioner-scoped either."""
        with pytest.raises(ValidationError, match="'integration' is not supported for provisioner type"):
            WorkspaceIacModel(
                name="app",
                provisioner=ProvisionerType.COMPOSE,
                source=self._source(),
                integration="docker_main",
            )

    def test_integration_rejected_on_script(self):
        with pytest.raises(ValidationError, match="'integration' is not supported for provisioner type"):
            WorkspaceIacModel(
                name="app",
                provisioner=ProvisionerType.SCRIPT,
                source=self._source(),
                integration="something",
            )

    def test_integration_rejected_on_argocd(self):
        with pytest.raises(ValidationError, match="'integration' is not supported for provisioner type"):
            WorkspaceIacModel(
                name="app",
                provisioner=ProvisionerType.ARGOCD,
                integration="something",
            )
