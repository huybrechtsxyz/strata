#!/usr/bin/env python3
"""Tests for ConfigurationModel YAML validation."""

import pytest
from pydantic import ValidationError

from strata.models.configuration_model import ConfigurationModel


def _minimal_configuration() -> dict:
    return {
        "meta": {"name": "solution-config"},
        "spec": {
            "providers": [
                {
                    "name": "kamatera",
                    "description": "Kamatera cloud provider",
                    "regions": ["eu-west", "eu-fr", "us-east"],
                    "resources": [{"name": "vm", "category": "compute"}],
                }
            ]
        },
    }


def test_configuration_minimal_is_valid():
    """A minimal configuration with one provider registry entry validates successfully."""
    model = ConfigurationModel.model_validate(_minimal_configuration())
    assert model.meta.name == "solution-config"
    assert model.spec.providers[0].name == "kamatera"
    assert model.spec.providers[0].regions == ["eu-west", "eu-fr", "us-east"]


def test_configuration_rejects_duplicate_provider_names():
    """Duplicate provider names across the registry raise a ValidationError."""
    data = _minimal_configuration()
    data["spec"]["providers"].append(dict(data["spec"]["providers"][0]))
    with pytest.raises(ValidationError, match="Duplicate"):
        ConfigurationModel.model_validate(data)
