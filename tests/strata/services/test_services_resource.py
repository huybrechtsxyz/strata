#!/usr/bin/env python3
"""
===============================================================================
Script Name   : test_services_resource.py
Author        : Vincent Huybrechts
Created       : 2026-02-09
Last Updated  : 2026-02-09
Version       : 1.0.0
Python Version: 3.12+
Description   : ResourceService test fixtures and utilities for strata CLI tests.
===============================================================================
"""

from pathlib import Path

import pytest

from strata.models.resource_model import ResourceModel
from strata.services.configuration_service import ConfigurationService
from strata.services.resource_service import ResourceService


def get_data_path(relative_path: str) -> str:
    """Helper to get test data path."""
    return str(Path(__file__).parent.parent.parent / "data" / relative_path)


class TestResourceService:
    @pytest.fixture
    def get_resource_service(self):
        return ResourceService(get_data_path("resources/resource-standard.yaml"))

    @pytest.fixture
    def get_configuration_service(self):
        """Fixture for configuration service with test data."""
        ConfigurationService.reset()
        config_svc = ConfigurationService.get_instance()
        success, errors = config_svc.load_from_paths([get_data_path("configurations/configuration-standard.yaml")])
        assert success, f"Configuration loading failed: {errors}"
        is_valid, val_errors = config_svc.validate()
        assert is_valid, f"Configuration validation failed: {val_errors}"
        return config_svc

    def test_get_model_class(self, get_resource_service):
        service = get_resource_service
        model_class = service._get_model_class()
        assert model_class == ResourceModel

    def test_validate_with_configuration(self, get_resource_service, get_configuration_service):
        """Test resource validation with configuration service."""
        resource_svc = get_resource_service
        config_svc = get_configuration_service

        is_valid, errors = resource_svc.validate(configuration_model=config_svc.model)
        assert is_valid, f"Validation failed: {errors}"
        assert len(errors) == 0

    def test_configuration_schema_validation_required_fields(self, get_configuration_service):
        """Test that missing required fields are caught."""
        # Create resource with missing required field
        resource_data = {
            "apiVersion": "platform.huybrechts.xyz/v1",
            "kind": "resource",
            "meta": {"name": "test_resource"},
            "spec": {
                "properties": {
                    "provider_type": "kamatera",
                    "resource_type": "vm_standard",
                },
                "configuration": {
                    "cpu": 2,
                    # Missing required fields: ram_mb, disk_gb
                },
            },
        }

        resource_svc = ResourceService(data=resource_data)
        config_svc = get_configuration_service

        is_valid, errors = resource_svc.validate(configuration_model=config_svc.model)
        assert not is_valid
        assert any("Required configuration field 'ram_mb' is missing" in err for err in errors)
        assert any("Required configuration field 'disk_gb' is missing" in err for err in errors)

    def test_configuration_schema_validation_optional_fields(self, get_configuration_service):
        """Test that optional fields can be omitted."""
        # Create resource without optional field
        resource_data = {
            "apiVersion": "platform.huybrechts.xyz/v1",
            "kind": "resource",
            "meta": {"name": "test_resource"},
            "spec": {
                "properties": {
                    "provider_type": "kamatera",
                    "resource_type": "vm_standard",
                },
                "configuration": {
                    "cpu": 2,
                    "ram_mb": 4096,
                    "disk_gb": 50,
                    # custom_tag is optional - omitted
                },
            },
        }

        resource_svc = ResourceService(data=resource_data)
        config_svc = get_configuration_service

        is_valid, errors = resource_svc.validate(configuration_model=config_svc.model)
        assert is_valid
        assert len(errors) == 0

    def test_configuration_schema_validation_pattern_mismatch(self, get_configuration_service):
        """Test that pattern validation catches invalid values."""
        # Create resource with invalid pattern
        resource_data = {
            "apiVersion": "platform.huybrechts.xyz/v1",
            "kind": "resource",
            "meta": {"name": "test_resource"},
            "spec": {
                "properties": {
                    "provider_type": "kamatera",
                    "resource_type": "vm_standard",
                },
                "configuration": {
                    "cpu": 0,  # Invalid: pattern requires 1-99
                    "ram_mb": 4096,
                    "disk_gb": 50,
                },
            },
        }

        resource_svc = ResourceService(data=resource_data)
        config_svc = get_configuration_service

        is_valid, errors = resource_svc.validate(configuration_model=config_svc.model)
        assert not is_valid
        assert any("does not match required pattern" in err for err in errors)

    def test_configuration_schema_validation_additional_fields_not_allowed(self, get_configuration_service):
        """Test that extra fields are rejected when additional_configurations=False."""
        # Create resource with extra field
        resource_data = {
            "apiVersion": "platform.huybrechts.xyz/v1",
            "kind": "resource",
            "meta": {"name": "test_resource"},
            "spec": {
                "properties": {
                    "provider_type": "kamatera",
                    "resource_type": "vm_standard",
                },
                "configuration": {
                    "cpu": 2,
                    "ram_mb": 4096,
                    "disk_gb": 50,
                    "extra_field": "value",  # Not in schema, additional_configurations=false
                },
            },
        }

        resource_svc = ResourceService(data=resource_data)
        config_svc = get_configuration_service

        is_valid, errors = resource_svc.validate(configuration_model=config_svc.model)
        assert not is_valid
        assert any("'extra_field' is not allowed" in err for err in errors)

    def test_configuration_schema_validation_additional_fields_allowed(self, get_configuration_service):
        """Test that extra fields are permitted when additional_configurations=True."""
        # Create resource with extra field for vm_highmem (which has additional_configurations=true)
        resource_data = {
            "apiVersion": "platform.huybrechts.xyz/v1",
            "kind": "resource",
            "meta": {"name": "test_resource"},
            "spec": {
                "properties": {
                    "provider_type": "kamatera",
                    "resource_type": "vm_highmem",
                },
                "configuration": {
                    "cpu": 4,
                    "ram_mb": 16384,
                    "extra_field": "value",  # Should be allowed
                },
            },
        }

        resource_svc = ResourceService(data=resource_data)
        config_svc = get_configuration_service

        is_valid, errors = resource_svc.validate(configuration_model=config_svc.model)
        assert is_valid
        assert len(errors) == 0
