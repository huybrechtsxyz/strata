#!/usr/bin/env python3
"""Tests for ResourceService loading and validation."""

from strata.models.configuration_model import ConfigurationModel
from strata.models.provider_config_model import ProviderConfigModel
from strata.services.resource_service import ResourceService


def _minimal_resource_data() -> dict:
    return {
        "meta": {"name": "web-vm"},
        "spec": {
            "properties": {
                "provider_type": "kamatera",
                "resource_type": "virtual_machine",
            },
            "default_tags": {"environment": "test"},
        },
    }


def _configuration_with_kamatera_pointer() -> ConfigurationModel:
    return ConfigurationModel.model_validate(
        {
            "meta": {"name": "solution-config"},
            "spec": {"providers": [{"name": "kamatera", "file": "providers/kamatera.yaml"}]},
        }
    )


def _kamatera_provider_config(configuration_schema: dict | None = None) -> ProviderConfigModel:
    resource_entry = {"name": "virtual_machine"}
    if configuration_schema is not None:
        resource_entry["configuration"] = configuration_schema
        resource_entry["additional_configurations"] = False

    return ProviderConfigModel.model_validate(
        {
            "meta": {"name": "kamatera"},
            "spec": {
                "description": "Kamatera cloud provider",
                "regions": ["eu-west"],
                "resources": [resource_entry],
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
    """Phase 2: a provider type present in the configuration registry (as a pointer) validates."""
    service = ResourceService(data=_minimal_resource_data())
    is_valid, errors = service.validate(configuration_model=_configuration_with_kamatera_pointer())
    assert is_valid
    assert errors == []


def test_resource_service_rejects_unknown_resource_type():
    """Deep check: a resource type absent from the provider config's registry entry fails validation."""
    data = _minimal_resource_data()
    data["spec"]["properties"]["resource_type"] = "unknown_type"
    service = ResourceService(data=data)
    service.validate()
    is_valid, errors = service.validate_against_provider_config(_kamatera_provider_config())
    assert not is_valid
    assert any("is not valid for provider" in e for e in errors)


def test_resource_service_validates_configuration_schema():
    """Deep check: configuration fields are checked against the provider's declared schema."""
    data = _minimal_resource_data()
    data["spec"]["configuration"] = {"disk_size": "not-a-number"}
    provider_config = _kamatera_provider_config({"disk_size": {"pattern": r"^\d+$", "required": True}})

    service = ResourceService(data=data)
    service.validate()
    is_valid, errors = service.validate_against_provider_config(provider_config)
    assert not is_valid
    assert any("does not match required pattern" in e for e in errors)


def test_resource_service_rejects_disallowed_configuration_field():
    """Deep check: an undeclared configuration field is rejected when additional_configurations is False."""
    data = _minimal_resource_data()
    data["spec"]["configuration"] = {"unexpected_field": "value"}
    provider_config = _kamatera_provider_config({"disk_size": {"pattern": r"^\d+$", "required": False}})

    service = ResourceService(data=data)
    service.validate()
    is_valid, errors = service.validate_against_provider_config(provider_config)
    assert not is_valid
    assert any("is not allowed for resource type" in e for e in errors)
