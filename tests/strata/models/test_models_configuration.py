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
            "providers": [{"name": "kamatera", "file": "providers/kamatera.yaml"}],
        },
    }


def test_configuration_minimal_is_valid():
    """A minimal configuration with one provider registry pointer validates successfully."""
    model = ConfigurationModel.model_validate(_minimal_configuration())
    assert model.meta.name == "solution-config"
    assert model.spec.providers[0].name == "kamatera"
    assert model.spec.providers[0].file == "providers/kamatera.yaml"


def test_configuration_rejects_duplicate_provider_names():
    """Duplicate provider names across the registry raise a ValidationError."""
    data = _minimal_configuration()
    data["spec"]["providers"].append(dict(data["spec"]["providers"][0]))
    with pytest.raises(ValidationError, match="Duplicate"):
        ConfigurationModel.model_validate(data)


def test_configuration_topologies_is_optional():
    """spec.topologies may be omitted entirely."""
    model = ConfigurationModel.model_validate(_minimal_configuration())
    assert model.spec.topologies is None
    assert model.spec.additional_topologies is False


def test_configuration_accepts_topology_registry_pointer():
    """spec.topologies accepts a simple name+file pointer to a standalone TopologyConfig document."""
    data = _minimal_configuration()
    data["spec"]["topologies"] = [{"name": "kubernetes", "file": "topologies/kubernetes.yaml"}]
    model = ConfigurationModel.model_validate(data)
    assert model.spec.topologies[0].name == "kubernetes"


def test_configuration_rejects_duplicate_topology_names():
    """Duplicate topology type names across the registry raise a ValidationError."""
    data = _minimal_configuration()
    data["spec"]["topologies"] = [
        {"name": "kubernetes", "file": "topologies/kubernetes.yaml"},
        {"name": "kubernetes", "file": "topologies/kubernetes-2.yaml"},
    ]
    with pytest.raises(ValidationError, match="Duplicate"):
        ConfigurationModel.model_validate(data)

