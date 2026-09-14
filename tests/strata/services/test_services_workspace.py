#!/usr/bin/env python3
"""
===============================================================================
Script Name   : test_services_workspace.py
Author        : Vincent Huybrechts
Created       : 2026-02-09
Last Updated  : 2026-02-09
Version       : 1.0.0
Python Version: 3.12+
Description   : WorkspaceService test fixtures and utilities for strata CLI tests.
===============================================================================
"""

from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, Optional
from unittest.mock import MagicMock, patch

import pytest

from strata.models.workspace_model import WorkspaceModel
from strata.services.workspace_service import WorkspaceService

_MINIMAL_SINGLE_REPO_WORKSPACE: Dict[str, Any] = {
    "apiVersion": "strata.huybrechts.xyz/v1",
    "kind": "workspace",
    "meta": {"name": "single_repo_ws"},
    "spec": {
        "providers": [
            {"name": "azure", "file": "tests/data/providers/provider-standard.yaml"},
        ],
        "provisioners": [
            {
                "name": "platform_iac",
                "provisioner": "terraform",
                "source": {"source_path": "terraform"},
            }
        ],
        "resources": [
            {"name": "node", "file": "tests/data/resources/resource-standard.yaml"},
        ],
        "topology": [
            {
                "name": "platform_cluster",
                "provider": "azure",
                "provisioner": "platform_iac",
                "type": "kubernetes",
                "components": [{"resource": "node"}],
            }
        ],
    },
}


def _data(relative_path: str) -> str:
    return str(Path(__file__).parent.parent.parent / "data" / relative_path)


class TestWorkspaceService:
    @pytest.fixture
    def get_workspace_service(self):
        return WorkspaceService(_data("workspaces/workspace-standard.yaml"))

    def test_get_model_class(self, get_workspace_service):
        service = get_workspace_service
        model_class = service._get_model_class()
        assert model_class == WorkspaceModel

    def test_validate_standard(self, get_workspace_service):
        service = get_workspace_service
        is_valid, errors = service.validate()
        assert is_valid, f"Validation failed: {errors}"
        assert service.is_validated()

    def test_get_kind_after_validate(self, get_workspace_service):
        service = get_workspace_service
        service.validate()
        assert service.get_kind() == "workspace"


class TestWorkspaceServiceSingleRepo:
    """Verify that a provisioner with source_path only (no repository) passes Phase 2."""

    def _make_service(self):
        return WorkspaceService(data=_MINIMAL_SINGLE_REPO_WORKSPACE)

    def test_phase1_passes_without_repository(self):
        """Phase 1 validation must succeed when repository is absent."""
        service = self._make_service()
        is_valid, errors = service.validate()
        assert is_valid, f"Phase 1 failed: {errors}"

    def test_dynamic_validate_no_repository_no_errors(self):
        """Phase 2 must not raise InvalidReferenceError when repository is absent."""
        service = self._make_service()
        # Prime the model via Phase 1 first.
        service.validate()
        service._repo_map = {}  # empty repo map — simulates single-repo workspace
        config_model = MagicMock()
        config_model.get_remote_map.return_value = {}
        # ADR-0079: the fixture's sole provisioner is `provisioner: terraform` — declare a
        # matching integration so this test still exercises only what it's meant to (that
        # source.repository is optional), not the separate integration-binding check.
        config_model.spec.integrations = [SimpleNamespace(name="tf", type="terraform", enabled=True)]
        is_valid, errors = service._validate_dynamic(configuration_model=config_model)
        assert is_valid, f"Phase 2 errors: {errors}"
        assert errors == []


class TestWorkspaceServiceTerraformIntegrationBinding:
    """ADR-0079: `strata validate --deep` resolves every `provisioner: terraform`
    entry's integration binding ahead of deploy time."""

    def _service(self, provisioner_extra: Optional[Dict[str, Any]] = None) -> WorkspaceService:
        spec = {**_MINIMAL_SINGLE_REPO_WORKSPACE["spec"]}
        provisioner = {**spec["provisioners"][0], **(provisioner_extra or {})}
        spec = {**spec, "provisioners": [provisioner]}
        service = WorkspaceService(data={**_MINIMAL_SINGLE_REPO_WORKSPACE, "spec": spec})
        service.validate()
        service._repo_map = {}
        return service

    def _config_model(self, integrations):
        config_model = MagicMock()
        config_model.get_remote_map.return_value = {}
        config_model.spec.integrations = integrations
        return config_model

    def test_auto_bind_sole_candidate_no_errors(self):
        service = self._service()
        config_model = self._config_model([SimpleNamespace(name="tf", type="terraform", enabled=True)])
        is_valid, errors = service._validate_dynamic(configuration_model=config_model)
        assert is_valid, f"Unexpected errors: {errors}"

    def test_auto_bind_opentofu_counts_as_compatible(self):
        service = self._service()
        config_model = self._config_model([SimpleNamespace(name="tofu", type="opentofu", enabled=True)])
        is_valid, errors = service._validate_dynamic(configuration_model=config_model)
        assert is_valid, f"Unexpected errors: {errors}"

    def test_auto_bind_zero_candidates_fails(self):
        service = self._service()
        config_model = self._config_model([])
        is_valid, errors = service._validate_dynamic(configuration_model=config_model)
        assert is_valid is False
        assert any("no Terraform-compatible integration registered" in e for e in errors)

    def test_auto_bind_ambiguous_candidates_fails(self):
        service = self._service()
        config_model = self._config_model(
            [
                SimpleNamespace(name="legacy", type="terraform", enabled=True),
                SimpleNamespace(name="current", type="terraform", enabled=True),
            ]
        )
        is_valid, errors = service._validate_dynamic(configuration_model=config_model)
        assert is_valid is False
        assert any("ambiguous" in e for e in errors)

    def test_disabled_integration_excluded_from_candidates(self):
        """A disabled integration doesn't count — auto-bind still fails with zero candidates."""
        service = self._service()
        config_model = self._config_model([SimpleNamespace(name="tf", type="terraform", enabled=False)])
        is_valid, errors = service._validate_dynamic(configuration_model=config_model)
        assert is_valid is False
        assert any("no Terraform-compatible integration registered" in e for e in errors)

    def test_explicit_integration_name_wins_over_ambiguity(self):
        service = self._service(provisioner_extra={"integration": "current"})
        config_model = self._config_model(
            [
                SimpleNamespace(name="legacy", type="terraform", enabled=True),
                SimpleNamespace(name="current", type="terraform", enabled=True),
            ]
        )
        is_valid, errors = service._validate_dynamic(configuration_model=config_model)
        assert is_valid, f"Unexpected errors: {errors}"

    def test_explicit_integration_name_missing_fails(self):
        service = self._service(provisioner_extra={"integration": "does_not_exist"})
        config_model = self._config_model([SimpleNamespace(name="tf", type="terraform", enabled=True)])
        is_valid, errors = service._validate_dynamic(configuration_model=config_model)
        assert is_valid is False
        assert any("is not registered" in e for e in errors)

    def test_explicit_integration_wrong_type_fails(self):
        service = self._service(provisioner_extra={"integration": "git_main"})
        config_model = self._config_model([SimpleNamespace(name="git_main", type="git", enabled=True)])
        is_valid, errors = service._validate_dynamic(configuration_model=config_model)
        assert is_valid is False
        assert any("is not compatible" in e for e in errors)

    def test_non_terraform_provisioner_skips_binding_check(self):
        """An ansible provisioner is never checked against Terraform integrations."""
        service = self._service(provisioner_extra={"provisioner": "ansible"})
        config_model = self._config_model([])
        is_valid, errors = service._validate_dynamic(configuration_model=config_model)
        assert is_valid, f"Unexpected errors: {errors}"


class TestWorkspaceServiceNetworks:
    """ADR-0076: kind:network file references must load and merge like DNS/firewalls."""

    def _repo_root(self) -> Path:
        return Path(__file__).parent.parent.parent.parent

    def _workspace_with_network(self, network_file: str) -> dict:
        spec: dict = {**_MINIMAL_SINGLE_REPO_WORKSPACE["spec"]}
        spec["networks"] = [{"name": "net1", "file": network_file}]
        return {**_MINIMAL_SINGLE_REPO_WORKSPACE, "spec": spec}

    def test_get_network_services_after_load(self):
        service = WorkspaceService(data=self._workspace_with_network("tests/data/network/network-haven.yaml"))
        is_valid, errors = service.validate()
        assert is_valid, f"Validation failed: {errors}"

        mock_config_service = MagicMock()
        mock_config_service.get_remote_map.return_value = {}
        with patch(
            "strata.services.configuration_service.ConfigurationService.get_instance",
            return_value=mock_config_service,
        ):
            related_services, success = service.load_workspace_services(objects_path=str(self._repo_root()))
        assert success, "load_workspace_services should succeed"
        assert "networks" in related_services
        assert "net1" in related_services["networks"]

        network_services = service.get_network_services()
        assert network_services is not None
        assert set(network_services.keys()) == {"net1"}

        net_service = network_services["net1"]
        assert net_service.model is not None
        assert len(net_service.model.spec.networks) >= 1

        # Named-lookup accessor mirrors get_dns_service()/get_firewall_service()
        assert service.get_network_service("net1") is net_service
        assert service.get_network_service("does_not_exist") is None

    def test_missing_network_file_fails_dynamic_validation(self):
        service = WorkspaceService(data=self._workspace_with_network("tests/data/network/does-not-exist.yaml"))
        service.validate()
        config_model = MagicMock()
        config_model.get_remote_map.return_value = {}
        # ADR-0079: declare a matching integration so the only failure is the network file
        # this test is actually about, not an incidental integration-binding error too.
        config_model.spec.integrations = [SimpleNamespace(name="tf", type="terraform", enabled=True)]
        is_valid, errors = service._validate_dynamic(configuration_model=config_model, work_path=str(self._repo_root()))
        assert is_valid is False
        assert any("net1" in e for e in errors)
