#!/usr/bin/env python3
"""Tests for ProviderModel YAML validation."""

import pytest
from pydantic import ValidationError

from strata.models.provider_model import ProviderModel


def _minimal_provider() -> dict:
    return {
        "meta": {"name": "kamatera-primary"},
        "spec": {
            "properties": {
                "type": "kamatera",
                "region": "eu-west",
            }
        },
    }


def test_provider_minimal_is_valid():
    """A minimal provider (only required fields) validates successfully."""
    model = ProviderModel.model_validate(_minimal_provider())
    assert model.meta.name == "kamatera-primary"
    assert model.spec.properties.type == "kamatera"
    assert model.spec.properties.region == "eu-west"
    assert model.apiVersion.value == "strata.huybrechts.xyz/v2"
    assert model.kind.value == "provider"


def test_provider_full_is_valid():
    """A provider with authentication, references, and lifecycle validates successfully."""
    data = _minimal_provider()
    data["spec"]["authentication"] = {
        "method": "cli",
        "cli": {"use_cli": True},
    }
    data["spec"]["references"] = {
        "variables": ["internal_network_cidr"],
        "secrets": ["api_token"],
    }
    data["spec"]["lifecycle"] = {
        "deploy_plan_before": {"scripts": ["scripts/validate.sh"]},
        "deploy_provision": {"description": "Provision the provider", "scripts": ["scripts/provision.py"]},
    }
    data["spec"]["configuration"] = {"skip_provider_registration": True}
    data["spec"]["custom"] = {"costcenter": "strata"}
    model = ProviderModel.model_validate(data)
    assert model.spec.authentication.method == "cli"
    assert model.spec.references.variables == ["internal_network_cidr"]
    assert model.spec.lifecycle.root["deploy_plan_before"].scripts == ["scripts/validate.sh"]
    assert model.spec.configuration == {"skip_provider_registration": True}
    assert model.spec.custom == {"costcenter": "strata"}


def test_provider_missing_required_field_is_invalid():
    """Missing spec.properties.region raises a ValidationError."""
    data = _minimal_provider()
    del data["spec"]["properties"]["region"]
    with pytest.raises(ValidationError):
        ProviderModel.model_validate(data)


def test_provider_rejects_unknown_fields():
    """Extra/unknown fields are rejected (extra='forbid')."""
    data = _minimal_provider()
    data["spec"]["properties"]["unknown_field"] = "oops"
    with pytest.raises(ValidationError):
        ProviderModel.model_validate(data)


def test_provider_name_must_be_lowercase():
    """meta.name must match the PlatformName pattern (lowercase, dashes/underscores)."""
    data = _minimal_provider()
    data["meta"]["name"] = "Invalid-Name"
    with pytest.raises(ValidationError):
        ProviderModel.model_validate(data)
