#!/usr/bin/env python3
"""Tests for ConfigurationModel YAML validation.

`spec.providers`/`spec.topologies` are thin `{name, file}` pointers to
standalone `ProviderConfigModel`/`TopologyConfigModel` documents (ADR-0014)
— the actual registry content (regions/resources, component roles) is
tested in `test_models_provider_config.py`/`test_models_topology_config.py`.
"""

import pytest
from pydantic import ValidationError

from strata.models.configuration_model import ConfigurationModel


def _minimal_configuration() -> dict:
    return {
        "meta": {"name": "solution-config"},
        "spec": {
            "providers": ["kamatera"],
        },
    }


def test_configuration_minimal_is_valid():
    """A minimal configuration with one provider registry pointer validates successfully."""
    model = ConfigurationModel.model_validate(_minimal_configuration())
    assert model.meta.name == "solution-config"
    assert model.spec.providers[0] == "kamatera"


def test_configuration_rejects_duplicate_provider_names():
    """Duplicate provider names across the registry raise a ValidationError."""
    data = _minimal_configuration()
    data["spec"]["providers"].append(data["spec"]["providers"][0])
    with pytest.raises(ValidationError, match="Duplicate"):
        ConfigurationModel.model_validate(data)


def test_configuration_topologies_is_optional():
    """spec.topologies may be omitted entirely."""
    model = ConfigurationModel.model_validate(_minimal_configuration())
    assert model.spec.topologies is None
    assert model.spec.additional_topologies is False


def test_configuration_accepts_topology_registry_pointer():
    """spec.topologies accepts a plain TopologyConfig document name."""
    data = _minimal_configuration()
    data["spec"]["topologies"] = ["kubernetes"]
    model = ConfigurationModel.model_validate(data)
    assert model.spec.topologies[0] == "kubernetes"


def test_configuration_rejects_duplicate_topology_names():
    """Duplicate topology type names across the registry raise a ValidationError."""
    data = _minimal_configuration()
    data["spec"]["topologies"] = ["kubernetes", "kubernetes"]
    with pytest.raises(ValidationError, match="Duplicate"):
        ConfigurationModel.model_validate(data)


# ---------------------------------------------------------------------------
# spec.security (docs/_gap_v1.md gap #7 — real usage: cfg-int-deployment's
# config/stores.yaml)
# ---------------------------------------------------------------------------


def test_configuration_security_is_optional():
    """spec.security may be omitted entirely — no restriction declared."""
    model = ConfigurationModel.model_validate(_minimal_configuration())
    assert model.spec.security is None


def test_configuration_accepts_security_allow_lists():
    data = _minimal_configuration()
    data["spec"]["security"] = {
        "allowed_secret_stores": ["github", "azure-keyvault"],
        "allowed_variable_stores": ["constant", "azure-appconfig"],
        "allowed_feature_stores": ["constant", "azure-appconfig"],
    }
    model = ConfigurationModel.model_validate(data)
    assert model.spec.security.allowed_secret_stores == ["github", "azure-keyvault"]
    assert model.spec.security.allowed_variable_stores == ["constant", "azure-appconfig"]
    assert model.spec.security.allowed_feature_stores == ["constant", "azure-appconfig"]


def test_configuration_security_fields_are_each_independently_optional():
    data = _minimal_configuration()
    data["spec"]["security"] = {"allowed_secret_stores": ["azure-keyvault"]}
    model = ConfigurationModel.model_validate(data)
    assert model.spec.security.allowed_secret_stores == ["azure-keyvault"]
    assert model.spec.security.allowed_variable_stores is None
    assert model.spec.security.allowed_feature_stores is None


def test_configuration_security_rejects_unknown_fields():
    """extra='forbid' still applies to the nested security model."""
    data = _minimal_configuration()
    data["spec"]["security"] = {"allowed_widget_stores": ["nope"]}
    with pytest.raises(ValidationError):
        ConfigurationModel.model_validate(data)
