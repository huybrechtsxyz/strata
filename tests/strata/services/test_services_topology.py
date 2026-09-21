#!/usr/bin/env python3
"""Tests for TopologyService loading and validation."""

from strata.services.topology_service import TopologyService


def test_topology_service_validates_from_data():
    """A TopologyService constructed from an in-memory dict validates successfully."""
    data = {
        "meta": {"name": "aks-platform"},
        "spec": {
            "type": "kubernetes",
            "components": [{"resource": "aks_cluster"}, {"resource": "blobstore"}],
        },
    }
    service = TopologyService(data=data)
    is_valid, errors = service.validate()
    assert is_valid
    assert errors == []
    assert service.model is not None
    assert service.model.spec.type == "kubernetes"
