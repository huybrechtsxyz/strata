#!/usr/bin/env python3
"""Tests for ProviderConfigModel (provider type registry, standalone kind) validation."""

import pytest
from pydantic import ValidationError

from strata.models.provider_config_model import ProviderConfigModel


def _minimal_provider_config() -> dict:
    return {
        "meta": {"name": "kamatera"},
        "spec": {
            "description": "Kamatera cloud provider",
            "regions": [{"name": "eu-west"}, {"name": "eu-fr"}, {"name": "us-east"}],
            "resources": [{"name": "vm", "category": "compute"}],
        },
    }


def test_provider_config_minimal_is_valid():
    """A minimal provider config document validates successfully."""
    model = ProviderConfigModel.model_validate(_minimal_provider_config())
    assert model.meta.name == "kamatera"
    assert [r.name for r in model.spec.regions] == ["eu-west", "eu-fr", "us-east"]
    assert model.apiVersion.value == "strata.huybrechts.xyz/v2"
    assert model.kind.value == "providerconfig"


def test_provider_config_requires_regions_unless_additional_regions():
    """additional_regions=False (default) with no regions raises a ValidationError."""
    data = _minimal_provider_config()
    del data["spec"]["regions"]
    with pytest.raises(ValidationError, match="additional_regions"):
        ProviderConfigModel.model_validate(data)


def test_provider_config_allows_no_regions_with_additional_regions():
    """additional_regions=True allows omitting the regions list."""
    data = _minimal_provider_config()
    del data["spec"]["regions"]
    data["spec"]["additional_regions"] = True
    model = ProviderConfigModel.model_validate(data)
    assert model.spec.regions is None


def test_provider_config_requires_resources_unless_additional_resources():
    """additional_resources=False (default) with no resources raises a ValidationError."""
    data = _minimal_provider_config()
    del data["spec"]["resources"]
    with pytest.raises(ValidationError, match="additional_resources"):
        ProviderConfigModel.model_validate(data)


def test_provider_config_rejects_duplicate_region_names():
    """Duplicate region names within a provider config raise a ValidationError."""
    data = _minimal_provider_config()
    data["spec"]["regions"] = [{"name": "eu-west"}, {"name": "eu-west"}]
    with pytest.raises(ValidationError, match="Duplicate"):
        ProviderConfigModel.model_validate(data)


def test_provider_config_region_accepts_geography_tag():
    """A region entry may carry an optional 'geography' tag alongside its name."""
    data = _minimal_provider_config()
    data["spec"]["regions"] = [
        {"name": "eu-west", "geography": "europe"},
        {"name": "eu-fr", "geography": "europe"},
        {"name": "us-east"},
    ]
    model = ProviderConfigModel.model_validate(data)
    assert model.spec.regions[0].name == "eu-west"
    assert model.spec.regions[0].geography == "europe"
    assert model.spec.regions[2].name == "us-east"
    assert model.spec.regions[2].geography is None


def test_provider_config_rejects_duplicate_region_names_with_geography_tag():
    """Duplicate region names are caught even when one entry carries a 'geography' tag."""
    data = _minimal_provider_config()
    data["spec"]["regions"] = [{"name": "eu-west"}, {"name": "eu-west", "geography": "europe"}]
    with pytest.raises(ValidationError, match="Duplicate"):
        ProviderConfigModel.model_validate(data)


def test_provider_config_rejects_duplicate_resource_names():
    """Duplicate resource names within a provider config raise a ValidationError."""
    data = _minimal_provider_config()
    data["spec"]["resources"] = [{"name": "vm"}, {"name": "vm"}]
    with pytest.raises(ValidationError, match="Duplicate"):
        ProviderConfigModel.model_validate(data)
