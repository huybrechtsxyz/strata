#!/usr/bin/env python3
"""Tests for ProviderService loading and validation."""

import pytest

from strata.models.configuration_model import ConfigurationModel
from strata.services.provider_service import ProviderService


def _minimal_provider_data() -> dict:
    return {
        "meta": {"name": "kamatera-primary"},
        "spec": {
            "properties": {
                "type": "kamatera",
                "region": "eu-west",
            }
        },
    }


def _configuration_with_kamatera() -> ConfigurationModel:
    return ConfigurationModel.model_validate(
        {
            "meta": {"name": "solution-config"},
            "spec": {
                "providers": [
                    {
                        "name": "kamatera",
                        "description": "Kamatera cloud provider",
                        "regions": ["eu-west", "eu-fr"],
                        "resources": [{"name": "vm"}],
                    }
                ]
            },
        }
    )


def test_provider_service_validates_from_data():
    """A ProviderService constructed from an in-memory dict validates successfully."""
    service = ProviderService(data=_minimal_provider_data())
    is_valid, errors = service.validate()
    assert is_valid
    assert errors == []
    assert service.get_provider_type() == "kamatera"
    assert service.get_provider_region() == "eu-west"


def test_provider_service_validates_from_file(tmp_path):
    """A ProviderService constructed from a YAML file path validates successfully."""
    yaml_content = """
meta:
  name: kamatera-primary
spec:
  properties:
    type: kamatera
    region: eu-west
"""
    yaml_file = tmp_path / "provider.yaml"
    yaml_file.write_text(yaml_content, encoding="utf-8")

    service = ProviderService(path=str(yaml_file))
    is_valid, errors = service.validate()
    assert is_valid
    assert errors == []
    assert service.get_provider_type() == "kamatera"


def test_provider_service_invalid_data_reports_errors():
    """Missing required field surfaces a validation error, not an exception."""
    data = _minimal_provider_data()
    del data["spec"]["properties"]["region"]

    service = ProviderService(data=data)
    is_valid, errors = service.validate()
    assert not is_valid
    assert len(errors) > 0
    assert service.model is None


def test_provider_service_accessors_raise_before_valid():
    """Calling an accessor on invalid data raises instead of silently returning None."""
    data = _minimal_provider_data()
    del data["spec"]["properties"]["region"]

    service = ProviderService(data=data)
    with pytest.raises(ValueError, match="not valid"):
        service.get_provider_type()


def test_provider_service_accepts_valid_against_configuration():
    """Phase 2: a provider type/region present in the configuration registry validates."""
    service = ProviderService(data=_minimal_provider_data())
    is_valid, errors = service.validate(configuration_model=_configuration_with_kamatera())
    assert is_valid
    assert errors == []


def test_provider_service_rejects_unknown_type_against_configuration():
    """Phase 2: a provider type absent from the configuration registry fails validation."""
    data = _minimal_provider_data()
    data["spec"]["properties"]["type"] = "unknown-cloud"
    service = ProviderService(data=data)
    is_valid, errors = service.validate(configuration_model=_configuration_with_kamatera())
    assert not is_valid
    assert any("not found in configuration" in e for e in errors)


def test_provider_service_rejects_unknown_region_against_configuration():
    """Phase 2: a region absent from the provider's configured region list fails validation."""
    data = _minimal_provider_data()
    data["spec"]["properties"]["region"] = "us-east"
    service = ProviderService(data=data)
    is_valid, errors = service.validate(configuration_model=_configuration_with_kamatera())
    assert not is_valid
    assert any("is not valid for provider" in e for e in errors)


def test_provider_service_skips_phase_2_without_configuration():
    """Omitting configuration_model skips Phase 2 entirely (Phase 1 only)."""
    data = _minimal_provider_data()
    data["spec"]["properties"]["type"] = "anything-goes"
    service = ProviderService(data=data)
    is_valid, errors = service.validate()
    assert is_valid
    assert errors == []


def test_provider_service_requires_path_or_data():
    """Constructing a service with neither path nor data raises immediately."""
    with pytest.raises(ValueError, match="Either path or data"):
        ProviderService()
