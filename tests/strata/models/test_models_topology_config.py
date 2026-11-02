#!/usr/bin/env python3
"""Tests for TopologyConfigModel (topology type registry, standalone kind) validation."""

import pytest
from pydantic import ValidationError

from strata.models.topology_config_model import TopologyConfigModel


def _minimal_topology_config() -> dict:
    return {
        "meta": {"name": "kubernetes"},
        "spec": {
            "components": [
                {"role": "control-plane", "required": True, "min_count": 1, "max_count": 3},
                {"role": "worker", "required": False, "min_count": 0, "max_count": 0, "uses_module": True},
            ],
        },
    }


def test_topology_config_minimal_is_valid():
    """A topology config document with component role constraints validates successfully."""
    model = TopologyConfigModel.model_validate(_minimal_topology_config())
    assert model.meta.name == "kubernetes"
    assert model.spec.components[0].role == "control-plane"
    assert model.apiVersion.value == "strata.huybrechts.xyz/v2"
    assert model.kind.value == "topologyconfig"


def test_topology_config_components_is_optional():
    """spec.components may be omitted entirely."""
    data = {"meta": {"name": "kubernetes"}, "spec": {}}
    model = TopologyConfigModel.model_validate(data)
    assert model.spec.components is None
    assert model.spec.additional_components is False


def test_topology_config_rejects_duplicate_component_roles():
    """Duplicate component roles within one topology config raise a ValidationError."""
    data = _minimal_topology_config()
    data["spec"]["components"] = [{"role": "worker"}, {"role": "worker"}]
    with pytest.raises(ValidationError, match="Duplicate"):
        TopologyConfigModel.model_validate(data)


def test_topology_config_component_max_count_below_min_count_is_rejected():
    """A component role's max_count must be >= min_count, unless max_count is 0 (unlimited)."""
    data = _minimal_topology_config()
    data["spec"]["components"] = [{"role": "worker", "min_count": 5, "max_count": 2}]
    with pytest.raises(ValidationError, match="max_count"):
        TopologyConfigModel.model_validate(data)


def test_topology_config_component_max_count_zero_means_unlimited():
    """max_count=0 (default) means unlimited, regardless of min_count."""
    data = _minimal_topology_config()
    data["spec"]["components"] = [{"role": "worker", "min_count": 5, "max_count": 0}]
    model = TopologyConfigModel.model_validate(data)
    assert model.spec.components[0].max_count == 0
