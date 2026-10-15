#!/usr/bin/env python3
"""Tests for ResourceService loading and validation."""

from strata.models.configuration_model import ConfigurationModel
from strata.models.provider_config_model import ProviderConfigModel
from strata.services.resource_service import ResourceService


def _minimal_resource_data() -> dict:
    return {
        "meta": {"name": "web-vm"},
        "spec": {
            "provider_type": "kamatera",
            "resource_type": "virtual_machine",
            "default_tags": {"environment": "test"},
        },
    }


def _configuration_with_kamatera_pointer() -> ConfigurationModel:
    return ConfigurationModel.model_validate(
        {
            "meta": {"name": "solution-config"},
            "spec": {"providers": ["kamatera"]},
        }
    )


def _kamatera_provider_config() -> ProviderConfigModel:
    return ProviderConfigModel.model_validate(
        {
            "meta": {"name": "kamatera"},
            "spec": {
                "description": "Kamatera cloud provider",
                "regions": [{"name": "eu-west"}],
                "resources": [{"name": "virtual_machine"}],
            },
        }
    )


def test_resource_service_validates_from_data():
    """A ResourceService constructed from an in-memory dict validates successfully."""
    service = ResourceService(data=_minimal_resource_data())
    result = service.validate()
    assert result.ok
    assert result.messages() == []
    assert service.get_provider_type() == "kamatera"
    assert service.get_resource_type() == "virtual_machine"


def test_resource_service_accepts_valid_against_configuration():
    """Phase 2: a provider type present in the configuration registry (as a pointer) validates."""
    service = ResourceService(data=_minimal_resource_data())
    result = service.validate(configuration_model=_configuration_with_kamatera_pointer())
    assert result.ok
    assert result.messages() == []


def test_resource_service_rejects_unknown_resource_type():
    """Deep check: a resource type absent from the provider config's registry entry fails validation."""
    data = _minimal_resource_data()
    data["spec"]["resource_type"] = "unknown_type"
    service = ResourceService(data=data)
    service.validate()
    result = service.validate_against_provider_config(_kamatera_provider_config())
    assert not result.ok
    assert any("is not valid for provider" in m for m in result.messages())
