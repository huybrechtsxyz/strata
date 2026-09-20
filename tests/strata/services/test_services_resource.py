#!/usr/bin/env python3
"""Tests for ResourceService loading and validation."""

from strata.models.configuration_model import ConfigurationModel
from strata.services.resource_service import ResourceService


def _minimal_resource_data() -> dict:
    return {
        "meta": {"name": "web-vm"},
        "spec": {
            "properties": {
                "provider_type": "kamatera",
                "resource_type": "virtual_machine",
            }
        },
    }


def _configuration_with_kamatera_vm(configuration_schema: dict | None = None) -> ConfigurationModel:
    resource_entry = {"name": "virtual_machine"}
    if configuration_schema is not None:
        resource_entry["configuration"] = configuration_schema
        resource_entry["additional_configurations"] = False

    return ConfigurationModel.model_validate(
        {
            "meta": {"name": "solution-config"},
            "spec": {
                "providers": [
                    {
                        "name": "kamatera",
                        "description": "Kamatera cloud provider",
                        "regions": ["eu-west"],
                        "resources": [resource_entry],
                    }
                ]
            },
        }
    )


def test_resource_service_validates_from_data():
    """A ResourceService constructed from an in-memory dict validates successfully."""
    service = ResourceService(data=_minimal_resource_data())
    is_valid, errors = service.validate()
    assert is_valid
    assert errors == []
    assert service.get_provider_type() == "kamatera"
    assert service.get_resource_type() == "virtual_machine"


def test_resource_service_accepts_valid_against_configuration():
    """Phase 2: a resource type present in the configuration registry validates."""
    service = ResourceService(data=_minimal_resource_data())
    is_valid, errors = service.validate(configuration_model=_configuration_with_kamatera_vm())
    assert is_valid
    assert errors == []


def test_resource_service_rejects_unknown_resource_type():
    """Phase 2: a resource type absent from the provider's registry entry fails validation."""
    data = _minimal_resource_data()
    data["spec"]["properties"]["resource_type"] = "unknown_type"
    service = ResourceService(data=data)
    is_valid, errors = service.validate(configuration_model=_configuration_with_kamatera_vm())
    assert not is_valid
    assert any("is not valid for provider" in e for e in errors)


def test_resource_service_validates_configuration_schema():
    """Phase 2: configuration fields are checked against the provider's declared schema."""
    data = _minimal_resource_data()
    data["spec"]["configuration"] = {"disk_size": "not-a-number"}
    configuration = _configuration_with_kamatera_vm({"disk_size": {"pattern": r"^\d+$", "required": True}})

    service = ResourceService(data=data)
    is_valid, errors = service.validate(configuration_model=configuration)
    assert not is_valid
    assert any("does not match required pattern" in e for e in errors)


def test_resource_service_rejects_disallowed_configuration_field():
    """Phase 2: an undeclared configuration field is rejected when additional_configurations is False."""
    data = _minimal_resource_data()
    data["spec"]["configuration"] = {"unexpected_field": "value"}
    configuration = _configuration_with_kamatera_vm({"disk_size": {"pattern": r"^\d+$", "required": False}})

    service = ResourceService(data=data)
    is_valid, errors = service.validate(configuration_model=configuration)
    assert not is_valid
    assert any("is not allowed for resource type" in e for e in errors)
