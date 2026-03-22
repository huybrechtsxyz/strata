#!/usr/bin/env python3
"""
===============================================================================
Script Name   : test_services_workspace.py
Author        : Vincent Huybrechts
Version       : 1.0.0
Python Version: 3.12+
Description   : Tests for WorkspaceService validation
===============================================================================
"""

import pytest
from pathlib import Path

from xyz_platform.services.workspace_service import WorkspaceService
from xyz_platform.services.configuration_service import ConfigurationService


@pytest.fixture(autouse=True)
def set_pythonpath_env(monkeypatch):
    monkeypatch.setenv("PYTHONPATH", "src")


# Test data paths
DATA_DIR = Path(__file__).parent.parent.parent / "data"
WORKSPACE_STANDARD = DATA_DIR / "workspaces" / "workspace-standard.yaml"
CONFIG_STANDARD = DATA_DIR / "configurations" / "configuration-standard.yaml"


class TestWorkspaceServiceValidation:
    """Test workspace service validation including repository references."""

    @pytest.fixture
    def get_configuration_service(self):
        """Fixture for configuration service with test data."""
        ConfigurationService.reset()
        config_svc = ConfigurationService.get_instance()
        success, errors = config_svc.load_from_paths([str(CONFIG_STANDARD)])
        assert success, f"Configuration loading failed: {errors}"
        is_valid, val_errors = config_svc.validate()
        assert is_valid, f"Configuration validation failed: {val_errors}"
        return config_svc

    def test_workspace_service_load_valid(self):
        """Test loading a valid workspace."""
        service = WorkspaceService(path=str(WORKSPACE_STANDARD))
        # Validate to populate the model
        is_valid, errors = service.validate()
        assert is_valid, f"Validation failed: {errors}"
        assert service.model is not None
        assert service.model.meta.name == "valid_workspace"

    def test_workspace_dynamic_validation_with_config(self, get_configuration_service):
        """Test workspace dynamic validation against configuration."""
        # Load workspace
        workspace_service = WorkspaceService(path=str(WORKSPACE_STANDARD))

        # Get configuration
        config_service = get_configuration_service

        # Validate workspace against configuration
        is_valid, errors = workspace_service.validate(
            configuration_model=config_service.model
        )

        # Should be valid - workspace-standard.yaml references repositories that exist in configuration-standard.yaml
        assert is_valid, f"Validation failed with errors: {errors}"
        assert len(errors) == 0

    def test_provisioner_repository_validation_valid(self, get_configuration_service):
        """Test that provisioner repository references are validated correctly."""
        # Load workspace
        workspace_service = WorkspaceService(path=str(WORKSPACE_STANDARD))

        # Get configuration
        config_service = get_configuration_service

        # Validate workspace first to populate model
        is_valid, errors = workspace_service.validate(
            configuration_model=config_service.model
        )
        assert is_valid, f"Validation failed: {errors}"

        # Get provisioners from workspace
        provisioners = workspace_service.model.spec.provisioners
        assert len(provisioners) > 0

        # Get repositories from configuration
        repositories = config_service.model.spec.repositories
        repo_names = {repo.name for repo in repositories}

        # Verify workspace provisioners reference valid repositories
        for provisioner in provisioners:
            assert (
                provisioner.source.repository in repo_names
            ), f"Provisioner '{provisioner.name}' references non-existent repository '{provisioner.source.repository}'"

    def test_provisioner_repository_validation_invalid(self, get_configuration_service):
        """Test validation fails when provisioner references non-existent repository."""
        # Create workspace with invalid repository reference
        workspace_data = {
            "apiVersion": "platform.huybrechts.xyz/v1",
            "kind": "workspace",
            "meta": {"name": "test_invalid_repo"},
            "spec": {
                "providers": [
                    {
                        "name": "test_provider",
                        "file": str(DATA_DIR / "providers" / "provider-standard.yaml"),
                    }
                ],
                "provisioners": [
                    {
                        "name": "test_provisioner",
                        "provisioner": "terraform",
                        "source": {
                            "repository": "non-existent-repo",  # Invalid reference
                            "source_path": "deploy/terraform",
                            "target_path": "iac/terraform",
                        },
                    }
                ],
                "resources": [
                    {
                        "name": "test_resource",
                        "role": "worker",
                        "file": str(DATA_DIR / "resources" / "resource-standard.yaml"),
                        "count": 1,
                    }
                ],
                "topology": [
                    {
                        "name": "test_topology",
                        "provider": "test_provider",
                        "provisioner": "terraform",
                        "type": "dockerswarm",
                        "components": [{"resource": "test_resource"}],
                    }
                ],
            },
        }

        # Get configuration
        config_service = get_configuration_service

        # Create workspace service from data
        workspace_service = WorkspaceService(data=workspace_data)

        # Validate - should fail
        is_valid, errors = workspace_service.validate(
            configuration_model=config_service.model
        )

        assert not is_valid, "Validation should fail for non-existent repository"
        assert len(errors) > 0
        assert any(
            "non-existent-repo" in error for error in errors
        ), f"Error should mention the invalid repository name. Got: {errors}"
        assert any(
            "Provisioner" in error or "test_provisioner" in error for error in errors
        ), f"Error should mention provisioner. Got: {errors}"

    def test_provisioner_type_from_enum(self):
        """Test that provisioner types are validated via ProvisionerType enum."""
        # Load workspace
        workspace_service = WorkspaceService(path=str(WORKSPACE_STANDARD))

        # Validate to populate model
        is_valid, errors = workspace_service.validate()
        assert is_valid, f"Validation failed: {errors}"

        # Get provisioners
        provisioners = workspace_service.model.spec.provisioners

        # Verify provisioner types are valid enum values
        valid_types = ["terraform", "script"]
        for provisioner in provisioners:
            assert (
                provisioner.provisioner.value in valid_types
            ), f"Invalid provisioner type: {provisioner.provisioner}"

    def test_workspace_repositories_in_config(self, get_configuration_service):
        """Test that all workspace repository references exist in configuration."""
        # Load workspace
        workspace_service = WorkspaceService(path=str(WORKSPACE_STANDARD))

        # Validate to populate model
        is_valid, errors = workspace_service.validate()
        assert is_valid, f"Validation failed: {errors}"

        # Get configuration
        config_service = get_configuration_service

        # Get all repository references from workspace
        workspace_repos = set()
        for provisioner in workspace_service.model.spec.provisioners:
            if provisioner.source and provisioner.source.repository:
                workspace_repos.add(provisioner.source.repository)

        # Get configuration repositories
        config_repos = {repo.name for repo in config_service.model.spec.repositories}

        # Verify all workspace repositories exist in configuration
        missing_repos = workspace_repos - config_repos
        assert (
            len(missing_repos) == 0
        ), f"Workspace references repositories not in configuration: {missing_repos}"

    def test_validation_error_structure(self, get_configuration_service):
        """Test that validation errors have proper structure."""
        # Create workspace with invalid repository
        workspace_data = {
            "apiVersion": "platform.huybrechts.xyz/v1",
            "kind": "workspace",
            "meta": {"name": "test_errors"},
            "spec": {
                "providers": [
                    {
                        "name": "test_provider",
                        "file": str(DATA_DIR / "providers" / "provider-standard.yaml"),
                    }
                ],
                "provisioners": [
                    {
                        "name": "bad_provisioner",
                        "provisioner": "terraform",
                        "source": {
                            "repository": "missing-repo",
                            "source_path": "deploy",
                        },
                    }
                ],
                "resources": [
                    {
                        "name": "test_resource",
                        "role": "worker",
                        "file": str(DATA_DIR / "resources" / "resource-standard.yaml"),
                        "count": 1,
                    }
                ],
                "topology": [
                    {
                        "name": "test_topology",
                        "provider": "test_provider",
                        "provisioner": "terraform",
                        "type": "dockerswarm",
                        "components": [{"resource": "test_resource"}],
                    }
                ],
            },
        }

        workspace_service = WorkspaceService(data=workspace_data)
        config_service = get_configuration_service

        # Run validation
        is_valid, errors = workspace_service.validate(
            configuration_model=config_service.model
        )

        # Check error structure
        assert not is_valid
        assert len(errors) > 0

        # Check that structured errors were created
        assert hasattr(workspace_service, "_structured_errors")
        assert len(workspace_service._structured_errors) > 0

        # Verify error contains key information
        error_str = errors[0]
        assert "Provisioner" in error_str
        assert "bad_provisioner" in error_str
        assert "repository" in error_str
        assert "missing-repo" in error_str
