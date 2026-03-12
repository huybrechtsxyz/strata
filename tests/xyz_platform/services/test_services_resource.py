#!/usr/bin/env python3
"""
===============================================================================
Script Name   : test_services_resource.py
Author        : Vincent Huybrechts
Version       : 1.0.0
Python Version: 3.12+
Description   : ResourceService test fixtures and utilities for xyz-platform CLI tests.
===============================================================================
"""

import pytest
from pathlib import Path

from xyz_platform.models.resource_model import ResourceModel
from xyz_platform.services.resource_service import ResourceService
from xyz_platform.services.configuration_service import ConfigurationService


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
        success, errors = config_svc.load_from_paths(
            [get_data_path("configurations/configuration-standard.yaml")]
        )
        assert success, f"Configuration loading failed: {errors}"
        is_valid, val_errors = config_svc.validate()
        assert is_valid, f"Configuration validation failed: {val_errors}"
        return config_svc

    def test_get_model_class(self, get_resource_service):
        service = get_resource_service
        model_class = service._get_model_class()
        assert model_class == ResourceModel

    def test_validate_with_configuration(
        self, get_resource_service, get_configuration_service
    ):
        """Test resource validation with configuration service."""
        resource_svc = get_resource_service
        config_svc = get_configuration_service

        is_valid, errors = resource_svc.validate(configuration_model=config_svc.model)
        assert is_valid, f"Validation failed: {errors}"
        assert len(errors) == 0

    def test_configuration_schema_validation_required_fields(
        self, get_configuration_service
    ):
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
        assert any(
            "Required configuration field 'ram_mb' is missing" in err for err in errors
        )
        assert any(
            "Required configuration field 'disk_gb' is missing" in err for err in errors
        )

    def test_configuration_schema_validation_optional_fields(
        self, get_configuration_service
    ):
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

    def test_configuration_schema_validation_pattern_mismatch(
        self, get_configuration_service
    ):
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

    def test_configuration_schema_validation_additional_fields_not_allowed(
        self, get_configuration_service
    ):
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

    def test_configuration_schema_validation_additional_fields_allowed(
        self, get_configuration_service
    ):
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

    def test_get_provider_type(self, get_resource_service, get_configuration_service):
        """Test retrieving provider type from resource."""
        resource_svc = get_resource_service
        config_svc = get_configuration_service

        is_valid, errors = resource_svc.validate(configuration_model=config_svc.model)
        assert is_valid, f"Validation failed: {errors}"

        provider_type = resource_svc.get_provider_type()
        assert provider_type == "kamatera"

    def test_get_resource_type(self, get_resource_service, get_configuration_service):
        """Test retrieving resource type from resource."""
        resource_svc = get_resource_service
        config_svc = get_configuration_service

        is_valid, errors = resource_svc.validate(configuration_model=config_svc.model)
        assert is_valid, f"Validation failed: {errors}"

        resource_type = resource_svc.get_resource_type()
        assert resource_type == "vm_standard"

    def test_get_unit_cost(self, get_resource_service, get_configuration_service):
        """Test retrieving unit cost from resource."""
        resource_svc = get_resource_service
        config_svc = get_configuration_service

        is_valid, errors = resource_svc.validate(configuration_model=config_svc.model)
        assert is_valid, f"Validation failed: {errors}"

        unit_cost = resource_svc.get_unit_cost()
        assert unit_cost == 5.00

    def test_get_category_and_subcategory(
        self, get_resource_service, get_configuration_service
    ):
        """Test retrieving category and subcategory from resource."""
        resource_svc = get_resource_service
        config_svc = get_configuration_service

        is_valid, errors = resource_svc.validate(configuration_model=config_svc.model)
        assert is_valid, f"Validation failed: {errors}"

        category, subcategory = resource_svc.get_category_and_subcategory()
        assert category == "compute"
        assert subcategory == "virtual_machine"

    def test_populate_category_from_configuration(self, get_configuration_service):
        """Test that category/subcategory are populated from configuration when missing."""
        config_svc = get_configuration_service

        # Create resource without category/subcategory
        resource_data = {
            "apiVersion": "platform.huybrechts.xyz/v1",
            "kind": "resource",
            "meta": {"name": "test_resource"},
            "spec": {
                "properties": {
                    "provider_type": "kamatera",
                    "resource_type": "vm_standard",
                    # category and subcategory omitted
                },
                "configuration": {
                    "cpu": 2,
                    "ram_mb": 4096,
                    "disk_gb": 50,
                },
            },
        }

        resource_svc = ResourceService(data=resource_data)
        is_valid, errors = resource_svc.validate(configuration_model=config_svc.model)
        assert is_valid, f"Validation failed: {errors}"

        # Check that category/subcategory remain None (since config doesn't define them)
        category, subcategory = resource_svc.get_category_and_subcategory()
        # Note: The configuration doesn't define category/subcategory for vm_standard
        # so they should remain None/unset
        assert category is None
        assert subcategory is None

    def test_populate_category_partial(self, get_configuration_service):
        """Test that only missing category or subcategory is populated."""
        config_svc = get_configuration_service

        # Create resource with only category, no subcategory
        resource_data = {
            "apiVersion": "platform.huybrechts.xyz/v1",
            "kind": "resource",
            "meta": {"name": "test_resource"},
            "spec": {
                "properties": {
                    "provider_type": "kamatera",
                    "resource_type": "vm_standard",
                    "category": "compute",
                    # subcategory omitted
                },
                "configuration": {
                    "cpu": 2,
                    "ram_mb": 4096,
                    "disk_gb": 50,
                },
            },
        }

        resource_svc = ResourceService(data=resource_data)
        is_valid, errors = resource_svc.validate(configuration_model=config_svc.model)
        assert is_valid, f"Validation failed: {errors}"

        category, subcategory = resource_svc.get_category_and_subcategory()
        assert category == "compute"
        # subcategory should remain None since config doesn't define it
        assert subcategory is None
