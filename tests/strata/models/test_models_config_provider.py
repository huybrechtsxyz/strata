#!/usr/bin/env python3
"""Tests for ConfigurationProviderModel (provider registry) validation."""

import pytest
from pydantic import ValidationError

from strata.models.config_provider_model import ConfigurationProviderModel


def _minimal_provider_entry() -> dict:
    return {
        "name": "kamatera",
        "description": "Kamatera cloud provider",
        "regions": ["eu-west", "eu-fr", "us-east"],
        "resources": [{"name": "vm", "category": "compute"}],
    }


def test_provider_entry_minimal_is_valid():
    """A minimal provider registry entry validates successfully."""
    model = ConfigurationProviderModel.model_validate(_minimal_provider_entry())
    assert model.name == "kamatera"
    assert model.regions == ["eu-west", "eu-fr", "us-east"]


def test_provider_entry_requires_regions_unless_additional_regions():
    """additional_regions=False (default) with no regions raises a ValidationError."""
    data = _minimal_provider_entry()
    del data["regions"]
    with pytest.raises(ValidationError, match="additional_regions"):
        ConfigurationProviderModel.model_validate(data)


def test_provider_entry_allows_no_regions_with_additional_regions():
    """additional_regions=True allows omitting the regions list."""
    data = _minimal_provider_entry()
    del data["regions"]
    data["additional_regions"] = True
    model = ConfigurationProviderModel.model_validate(data)
    assert model.regions is None


def test_provider_entry_requires_resources_unless_additional_resources():
    """additional_resources=False (default) with no resources raises a ValidationError."""
    data = _minimal_provider_entry()
    del data["resources"]
    with pytest.raises(ValidationError, match="additional_resources"):
        ConfigurationProviderModel.model_validate(data)


def test_provider_entry_rejects_duplicate_region_names():
    """Duplicate region names within a provider raise a ValidationError."""
    data = _minimal_provider_entry()
    data["regions"] = ["eu-west", "eu-west"]
    with pytest.raises(ValidationError, match="Duplicate"):
        ConfigurationProviderModel.model_validate(data)


def test_provider_entry_rejects_duplicate_resource_names():
    """Duplicate resource names within a provider raise a ValidationError."""
    data = _minimal_provider_entry()
    data["resources"] = [{"name": "vm"}, {"name": "vm"}]
    with pytest.raises(ValidationError, match="Duplicate"):
        ConfigurationProviderModel.model_validate(data)
