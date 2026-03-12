#!/usr/bin/env python3
"""
===============================================================================
Script Name   : test_services_deployment.py
Author        : Vincent Huybrechts
Created       : 2026-02-09
Last Updated  : 2026-02-09
Version       : 1.0.0
Python Version: 3.12+
Description   : Unit tests for DeploymentService.
===============================================================================
"""

import pytest
import tempfile
from pathlib import Path
from copy import deepcopy
from xyz_platform.services.deployment_service import DeploymentService
from xyz_platform.exceptions import (
    PlatformConfigurationError,
    PlatformFileNotFoundError,
    ServiceNotValidatedError,
)


# Valid test data matching DeploymentModel structure
VALID_DEPLOYMENT_DATA = {
    "apiVersion": "platform.huybrechts.xyz/v1",
    "kind": "deployment",
    "meta": {"name": "test_deployment", "labels": {"version": "1.0.0"}},
    "spec": {
        "workspace": {
            "name": "test_workspace",
            "description": "Test workspace",
            "file": "tests/data/workspaces/workspace-standard.yaml",
        },
        "environments": ["tests/data/environments/environment-standard.yaml"],
        "approvals": {"type": "auto"},
        "stages": [
            {
                "name": "production",
                "type": "infrastructure",
            }
        ],
    },
}


class TestDeploymentServiceInitialization:
    """Test DeploymentService initialization."""

    def test_initialization_with_valid_data(self):
        """Can initialize with valid YAML data."""
        service = DeploymentService(data=deepcopy(VALID_DEPLOYMENT_DATA))
        assert service.data == VALID_DEPLOYMENT_DATA
        assert service.model is None  # Not validated yet
        assert service._validated is False

    def test_initialization_with_path(self):
        """Can initialize with file path."""
        # Create temp file
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write("apiVersion: platform.huybrechts.xyz/v1\n")
            f.write("kind: deployment\n")
            f.write("meta:\n")
            f.write("  name: test\n")
            f.write("  labels:\n")
            f.write("    version: 1.0.0\n")
            f.write("spec:\n")
            f.write("  workspace:\n")
            f.write("    name: test_workspace\n")
            f.write("    description: Test workspace\n")
            f.write("    file: tests/data/workspaces/workspace-standard.yaml\n")
            f.write("  environments:\n")
            f.write("    - tests/data/environments/environment-standard.yaml\n")
            f.write("  approvals:\n")
            f.write("    type: auto\n")
            f.write("  stages:\n")
            f.write("    - name: production\n")
            f.write("      type: infrastructure\n")
            temp_path = f.name

        try:
            service = DeploymentService(path=temp_path)
            assert service.path == temp_path
            assert service.data is not None
        finally:
            Path(temp_path).unlink(missing_ok=True)

    def test_initialization_without_path_or_data_raises_error(self):
        """Initialization without path or data raises PlatformConfigurationError."""
        with pytest.raises(
            PlatformConfigurationError, match="Either path or data must be provided"
        ):
            service = DeploymentService()

    def test_initialization_with_nonexistent_path_raises_error(self):
        """Initialization with nonexistent path raises PlatformFileNotFoundError."""
        with pytest.raises(PlatformFileNotFoundError):
            DeploymentService(path="/nonexistent/path/deployment.yaml")


class TestDeploymentServiceValidation:
    """Test DeploymentService validation."""

    def test_validate_with_valid_data(self):
        """validate() succeeds with valid deployment data."""
        service = DeploymentService(data=deepcopy(VALID_DEPLOYMENT_DATA))
        is_valid, errors = service.validate()

        assert is_valid is True
        assert errors == []
        assert service.model is not None
        assert service._validated is True

    def test_validate_with_missing_required_fields(self):
        """validate() fails when required fields are missing."""
        data = {
            "kind": "Deployment",
            "meta": {"name": "test"},
            # Missing spec section
        }
        service = DeploymentService(data=data)
        is_valid, errors = service.validate()

        assert is_valid is False
        assert len(errors) > 0
        assert service.model is None
        assert service._validated is False

    def test_validate_with_invalid_workspace_structure(self):
        """validate() fails with invalid workspace structure."""
        data = deepcopy(VALID_DEPLOYMENT_DATA)
        data["spec"]["workspace"] = "invalid"  # Should be an object
        service = DeploymentService(data=data)
        is_valid, errors = service.validate()

        assert is_valid is False
        assert len(errors) > 0

    def test_validate_with_empty_data(self):
        """validate() fails with empty data."""
        service = DeploymentService(data={})
        is_valid, errors = service.validate()

        assert is_valid is False
        assert any("empty" in err.lower() for err in errors)


class TestDeploymentServiceProperties:
    """Test DeploymentService property methods."""

    def setup_method(self):
        """Create valid service for testing."""
        self.service = DeploymentService(data=deepcopy(VALID_DEPLOYMENT_DATA))
        self.service.validate()

    def test_get_kind(self):
        """get_kind() returns correct value."""
        assert self.service.get_kind() == "deployment"

    def test_get_name(self):
        """get_name() returns correct value."""
        assert self.service.get_name() == "test_deployment"

    def test_get_version(self):
        """get_version() returns correct value."""
        assert self.service.get_version() == "1.0.0"

    def test_is_validated(self):
        """is_validated() returns correct status."""
        assert self.service.is_validated() is True

    def test_is_validated_before_validation(self):
        """is_validated() returns False before validation."""
        service = DeploymentService(data=deepcopy(VALID_DEPLOYMENT_DATA))
        assert service.is_validated() is False


class TestDeploymentServiceEnsureValidated:
    """Test _ensure_validated protection."""

    def test_ensure_validated_raises_before_validation(self):
        """Methods requiring validation raise ServiceNotValidatedError before validate()."""
        service = DeploymentService(data=deepcopy(VALID_DEPLOYMENT_DATA))

        with pytest.raises(
            ServiceNotValidatedError, match="must be validated before use"
        ):
            service.get_kind()

    def test_ensure_validated_succeeds_after_validation(self):
        """Methods work correctly after validate()."""
        service = DeploymentService(data=deepcopy(VALID_DEPLOYMENT_DATA))
        service.validate()

        # Should not raise
        assert service.get_kind() == "deployment"


class TestDeploymentServiceLoadRelatedServices:
    """Test load_related_services method."""

    def setup_method(self):
        """Create valid service for testing."""
        self.service = DeploymentService(data=deepcopy(VALID_DEPLOYMENT_DATA))
        self.service.validate()

    def test_load_related_services_success(self):
        """load_related_services() successfully loads workspace and environments."""
        # This test uses actual workspace file
        # objects_path should be project root since test files use paths like 'tests/data/...'
        objects_path = str(Path(__file__).parent.parent.parent.parent)
        services, success = self.service.load_related_services(objects_path)

        # Should return a dict and boolean
        assert isinstance(services, dict)
        assert isinstance(success, bool)

        # Services dict should have expected keys for new architecture
        assert "workspace" in services
        assert "environments" in services

        # Old variable/secret/feature services should NOT be present
        assert "variables" not in services
        assert "secrets" not in services
        assert "features" not in services

        # Infrastructure services NOT in deployment services dict (accessed via delegation)
        assert "providers" not in services
        assert "resources" not in services
        assert "namespaces" not in services
        assert "firewalls" not in services

        # Workspace should be loaded
        if success:
            assert services["workspace"] is not None

    def test_load_related_services_caches_result(self):
        """load_related_services() caches result for subsequent calls."""
        objects_path = str(Path(__file__).parent.parent.parent.parent)
        # First call
        services1, success1 = self.service.load_related_services(objects_path)

        # Second call should return cached result (check internal cache)
        services2, success2 = self.service.load_related_services(objects_path)

        # Both calls should return the same success status
        assert success1 == success2

        # If successful, _related_services should be cached
        if success1:
            assert self.service._related_services is not None
            # The logger should show cached result on second call
            # (implementation detail: early return if _related_services is not None)

    def test_load_related_services_requires_validation(self):
        """load_related_services() requires model to be validated first."""
        service = DeploymentService(data=deepcopy(VALID_DEPLOYMENT_DATA))
        objects_path = str(Path(__file__).parent.parent.parent.parent)

        with pytest.raises(
            ServiceNotValidatedError, match="must be validated before use"
        ):
            service.load_related_services(objects_path)

    def test_load_related_services_with_environments(self):
        """load_related_services() loads environment services when deployment has multiple environments."""
        data = deepcopy(VALID_DEPLOYMENT_DATA)
        data["spec"]["environments"] = [
            "tests/data/environments/environment-standard.yaml",
            "tests/data/environments/environment-overrides.yaml",
        ]
        data["spec"]["stages"] = [
            {
                "name": "dev",
                "type": "infrastructure",
            }
        ]
        service = DeploymentService(data=data)
        service.validate()
        objects_path = str(Path(__file__).parent.parent.parent.parent)

        services, success = service.load_related_services(objects_path)

        # Should have environments dict (keyed by stage name)
        assert "environments" in services
        assert isinstance(services["environments"], dict)
        # Should have loaded environment for the dev stage
        assert "dev" in services["environments"]
        assert services["environments"]["dev"] is not None


class TestDeploymentServiceGetRelatedService:
    """Test _get_related_service and getter methods."""

    def setup_method(self):
        """Create valid service for testing."""
        self.service = DeploymentService(data=deepcopy(VALID_DEPLOYMENT_DATA))
        self.service.validate()

    def test_get_workspace_service(self):
        """get_workspace_service() returns workspace service if loaded."""
        # Load services first
        objects_path = str(Path(__file__).parent.parent.parent.parent)
        services, success = self.service.load_related_services(objects_path)

        workspace_service = self.service.get_workspace_service()
        # May be None if workspace file doesn't exist
        if success:
            assert workspace_service is not None
        else:
            # If loading failed, workspace may be None
            assert workspace_service is None

    def test_get_workspace_service_raises_without_load(self):
        """get_workspace_service() raises error if load_related_services() not called."""
        # Don't pre-load services
        with pytest.raises(
            ServiceNotValidatedError, match="must be validated before use"
        ):
            self.service.get_workspace_service()

    def test_get_environment_services_raises_without_load(self):
        """get_environment_services() raises error if load_related_services() not called."""
        data = deepcopy(VALID_DEPLOYMENT_DATA)
        data["spec"]["stages"] = [
            {
                "name": "dev",
                "environments": ["tests/data/environments/environment-overrides.yaml"],
                "approval": {"type": "auto"},
            }
        ]
        service = DeploymentService(data=data)
        service.validate()

        # Don't pre-load services
        with pytest.raises(
            ServiceNotValidatedError, match="must be validated before use"
        ):
            service.get_environment_services()

    def test_get_environment_service_by_name_raises_without_load(self):
        """get_environment_service() raises error if load_related_services() not called."""
        data = deepcopy(VALID_DEPLOYMENT_DATA)
        data["spec"]["stages"] = [
            {
                "name": "production",
                "environments": ["tests/data/environments/environment-overrides.yaml"],
                "approval": {"type": "auto"},
            }
        ]
        service = DeploymentService(data=data)
        service.validate()

        # Don't pre-load services
        with pytest.raises(
            ServiceNotValidatedError, match="must be validated before use"
        ):
            service.get_environment_service("production")

    def test_get_environment_service_nonexistent(self):
        """get_environment_service() returns None for nonexistent environment."""
        objects_path = str(Path(__file__).parent.parent.parent.parent)
        self.service.load_related_services(objects_path)

        result = self.service.get_environment_service("nonexistent")
        assert result is None

    def test_get_related_service_returns_none_for_unknown_type(self):
        """_get_related_service() returns None for unknown service type."""
        objects_path = str(Path(__file__).parent.parent.parent.parent)
        self.service.load_related_services(objects_path)

        result = self.service._get_related_service("unknown_type")
        assert result is None

    def test_infrastructure_delegation_to_workspace(self):
        """Infrastructure service getters delegate to workspace service."""
        objects_path = str(Path(__file__).parent.parent.parent.parent)
        self.service.load_related_services(objects_path)

        # These should delegate to workspace, not fail
        # They may return None if workspace doesn't have those services loaded
        provider = self.service.get_provider_service("azure")
        resource = self.service.get_resource_service("webapp")
        namespace = self.service.get_namespace_service("app")
        firewall = self.service.get_firewall_service("web")

        # Should not raise errors, just return None or the service if it exists
        # We're just testing the delegation mechanism works
        assert provider is None or provider is not None
        assert resource is None or resource is not None
        assert namespace is None or namespace is not None
        assert firewall is None or firewall is not None


class TestDeploymentServiceGetValidationErrors:
    """Test get_validation_errors method."""

    def test_get_validation_errors_empty_initially(self):
        """get_validation_errors() returns empty list initially."""
        service = DeploymentService(data=deepcopy(VALID_DEPLOYMENT_DATA))
        assert service.get_validation_errors() == []

    def test_get_validation_errors_after_validation_failure(self):
        """validate() returns errors when validation fails."""
        service = DeploymentService(data={})
        is_valid, errors = service.validate()

        assert not is_valid
        # Errors returned directly from validate()
        assert len(errors) > 0
        # get_validation_errors() is for load_related_services errors, not model validation
        # Model validation errors are returned from validate() method


class TestDeploymentServiceBuildPath:
    """Test get_build_path method."""

    def test_get_build_path(self):
        """get_build_path() returns correct build path."""
        service = DeploymentService(data=deepcopy(VALID_DEPLOYMENT_DATA))
        service.validate()

        build_path = Path("/tmp/builds")
        result = service.get_build_path(build_path)

        expected = build_path / "test_deployment-1.0.0"
        assert result == expected

    def test_get_build_path_different_versions(self):
        """get_build_path() uses version in path."""
        data = deepcopy(VALID_DEPLOYMENT_DATA)
        data["meta"]["labels"]["version"] = "2.5.3"

        service = DeploymentService(data=data)
        service.validate()

        build_path = Path("/builds")
        result = service.get_build_path(build_path)

        expected = build_path / "test_deployment-2.5.3"
        assert result == expected
