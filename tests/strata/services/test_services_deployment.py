#!/usr/bin/env python3
"""Unit tests for DeploymentService."""

from pathlib import Path

import pytest

from strata.models.deployment_model import DeploymentModel
from strata.services.deployment_service import DeploymentService


def _data(relative_path: str) -> str:
    return str(Path(__file__).parent.parent.parent / "data" / relative_path)


class TestDeploymentService:
    @pytest.fixture
    def service(self):
        return DeploymentService(_data("deployments/deployment-standard.yaml"))

    @pytest.fixture
    def invalid_service(self):
        return DeploymentService(_data("deployments/deployment-invalid.yaml"))

    def test_get_model_class(self, service):
        assert service._get_model_class() == DeploymentModel

    def test_validate_standard(self, service):
        is_valid, errors = service.validate()
        assert is_valid, f"Validation failed: {errors}"
        assert errors == []
        assert service.is_validated()
        assert service.model is not None

    def test_validate_sets_model(self, service):
        service.validate()
        assert isinstance(service.model, DeploymentModel)

    def test_get_kind_after_validate(self, service):
        service.validate()
        assert service.get_kind() == "deployment"

    def test_get_name_after_validate(self, service):
        service.validate()
        assert service.get_name() == "valid_platform"

    def test_validate_invalid_file(self, invalid_service):
        is_valid, errors = invalid_service.validate()
        assert not is_valid
        assert len(errors) > 0

    def test_validate_empty_data(self):
        service = DeploymentService(data={})
        is_valid, errors = service.validate()
        assert not is_valid
        assert len(errors) > 0

    def test_validate_in_memory_data(self):
        data = {
            "apiVersion": "platform.huybrechts.xyz/v1",
            "kind": "deployment",
            "meta": {"name": "test_deploy"},
            "spec": {
                "workspace": {"name": "test_workspace", "file": "workspace.yaml"},
                "environments": ["environment.yaml"],
                "layers": {"environment": "dev"},
                "stages": [{"name": "dev", "type": "infrastructure"}],
            },
        }
        service = DeploymentService(data=data)
        is_valid, errors = service.validate()
        assert is_valid, f"Validation failed: {errors}"

    def test_validate_dynamic_no_config_model(self, service):
        """Phase 2 without configuration_model always passes."""
        is_valid, errors = service._validate_dynamic()
        assert is_valid
        assert errors == []
