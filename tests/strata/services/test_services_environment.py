#!/usr/bin/env python3
"""Unit tests for EnvironmentService."""

from pathlib import Path

import pytest

from strata.models.environment_model import EnvironmentModel
from strata.services.environment_service import EnvironmentService


def _data(relative_path: str) -> str:
    return str(Path(__file__).parent.parent.parent / "data" / relative_path)


class TestEnvironmentService:
    @pytest.fixture
    def service(self):
        return EnvironmentService(_data("environments/environment-standard.yaml"))

    def test_get_model_class(self, service):
        assert service._get_model_class() == EnvironmentModel

    def test_validate_standard(self, service):
        is_valid, errors = service.validate()
        assert is_valid, f"Validation failed: {errors}"
        assert errors == []
        assert service.is_validated()
        assert service.model is not None

    def test_validate_sets_model(self, service):
        service.validate()
        assert isinstance(service.model, EnvironmentModel)

    def test_get_kind_after_validate(self, service):
        service.validate()
        assert service.get_kind() == "environment"

    def test_get_name_after_validate(self, service):
        service.validate()
        assert service.get_name() == "valid_environment"

    def test_validate_empty_data(self):
        service = EnvironmentService(data={})
        is_valid, errors = service.validate()
        assert not is_valid
        assert len(errors) > 0

    def test_validate_in_memory_data(self):
        data = {
            "apiVersion": "platform.huybrechts.xyz/v1",
            "kind": "environment",
            "meta": {
                "name": "test_env",
                "labels": {"version": "1.0.0", "environment": "dev"},
            },
            "spec": {},
        }
        service = EnvironmentService(data=data)
        is_valid, errors = service.validate()
        assert is_valid, f"Validation failed: {errors}"

    def test_validate_dynamic_no_config_model(self, service):
        """Phase 2 without configuration_model always passes."""
        is_valid, errors = service._validate_dynamic()
        assert is_valid
        assert errors == []

    def test_validate_environment_with_overrides(self):
        service = EnvironmentService(_data("environments/environment-overrides.yaml"))
        is_valid, errors = service.validate()
        assert is_valid, f"Validation failed: {errors}"
