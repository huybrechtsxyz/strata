#!/usr/bin/env python3
"""Tests for TopologyConfigService loading and validation."""

from strata.services.topology_config_service import TopologyConfigService


def test_topology_config_service_validates_from_data():
    """A TopologyConfigService constructed from an in-memory dict validates successfully."""
    data = {
        "meta": {"name": "kubernetes"},
        "spec": {"components": [{"role": "control-plane"}]},
    }
    service = TopologyConfigService(data=data)
    result = service.validate()
    assert result.ok
    assert result.messages() == []
    assert service.model is not None
    assert service.model.meta.name == "kubernetes"
