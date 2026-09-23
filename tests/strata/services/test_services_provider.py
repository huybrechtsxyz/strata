#!/usr/bin/env python3
"""Tests for ProviderService loading and validation."""

import pytest

from strata.models.configuration_model import ConfigurationModel
from strata.models.provider_config_model import ProviderConfigModel
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
                "regions": [{"name": "eu-west"}, {"name": "eu-fr"}],
                "resources": [{"name": "vm"}],
            },
        }
    )


def test_provider_service_validates_from_data():
    """A ProviderService constructed from an in-memory dict validates successfully."""
    service = ProviderService(data=_minimal_provider_data())
    result = service.validate()
    assert result.ok
    assert result.messages() == []
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
    result = service.validate()
    assert result.ok
    assert result.messages() == []
    assert service.get_provider_type() == "kamatera"


def test_provider_service_invalid_data_reports_errors():
    """Missing required field surfaces a validation error, not an exception."""
    data = _minimal_provider_data()
    del data["spec"]["properties"]["region"]

    service = ProviderService(data=data)
    result = service.validate()
    assert not result.ok
    assert len(result.errors) > 0
    assert service.model is None


def test_provider_service_accessors_raise_before_valid():
    """Calling an accessor on invalid data raises instead of silently returning None."""
    data = _minimal_provider_data()
    del data["spec"]["properties"]["region"]

    service = ProviderService(data=data)
    with pytest.raises(ValueError, match="not valid"):
        service.get_provider_type()


def test_provider_service_accepts_valid_against_configuration():
    """Phase 2: a provider type present in the configuration registry (as a pointer) validates."""
    service = ProviderService(data=_minimal_provider_data())
    result = service.validate(configuration_model=_configuration_with_kamatera_pointer())
    assert result.ok
    assert result.messages() == []


def test_provider_service_rejects_unknown_type_against_configuration():
    """Phase 2: a provider type absent from the configuration registry fails validation."""
    data = _minimal_provider_data()
    data["spec"]["properties"]["type"] = "unknown-cloud"
    service = ProviderService(data=data)
    result = service.validate(configuration_model=_configuration_with_kamatera_pointer())
    assert not result.ok
    assert any("not found in configuration" in m for m in result.messages())


def test_provider_service_accepts_valid_against_provider_config():
    """A region present in the loaded ProviderConfig document's regions validates."""
    service = ProviderService(data=_minimal_provider_data())
    service.validate()
    result = service.validate_against_provider_config(_kamatera_provider_config())
    assert result.ok
    assert result.messages() == []


def test_provider_service_rejects_unknown_region_against_provider_config():
    """A region absent from the loaded ProviderConfig document's regions fails validation."""
    data = _minimal_provider_data()
    data["spec"]["properties"]["region"] = "us-east"
    service = ProviderService(data=data)
    service.validate()
    result = service.validate_against_provider_config(_kamatera_provider_config())
    assert not result.ok
    assert any("is not valid for provider" in m for m in result.messages())


def test_provider_service_accepts_valid_against_provider_config_with_geography_tag():
    """A region declared with a 'geography' tag still validates against a Provider's plain region string."""
    data = _minimal_provider_data()
    service = ProviderService(data=data)
    service.validate()
    provider_config = ProviderConfigModel.model_validate(
        {
            "meta": {"name": "kamatera"},
            "spec": {
                "description": "Kamatera cloud provider",
                "regions": [{"name": "eu-west", "geography": "europe"}, {"name": "eu-fr"}],
                "resources": [{"name": "vm"}],
            },
        }
    )
    result = service.validate_against_provider_config(provider_config)
    assert result.ok
    assert result.messages() == []


def test_provider_service_skips_phase_2_without_configuration():
    """Omitting configuration_model skips Phase 2 entirely (Phase 1 only)."""
    data = _minimal_provider_data()
    data["spec"]["properties"]["type"] = "anything-goes"
    service = ProviderService(data=data)
    result = service.validate()
    assert result.ok
    assert result.messages() == []


def test_provider_service_requires_path_or_data():
    """Constructing a service with neither path nor data raises immediately."""
    with pytest.raises(ValueError, match="Either path or data"):
        ProviderService()
