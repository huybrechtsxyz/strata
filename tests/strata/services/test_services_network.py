#!/usr/bin/env python3
"""Tests for NetworkService loading and validation."""

from strata.services.network_service import NetworkService


def test_network_service_validates_from_data():
    """A NetworkService constructed from an in-memory dict validates successfully."""
    data = {
        "meta": {"name": "core-network"},
        "spec": {
            "networks": [
                {
                    "name": "vpc-main",
                    "address_space": ["10.0.0.0/16"],
                    "subnets": [{"name": "web", "cidr": "10.0.1.0/24"}],
                    "default_tags": {"environment": "test"},
                }
            ]
        },
    }
    service = NetworkService(data=data)
    is_valid, errors = service.validate()
    assert is_valid
    assert errors == []
    assert service.model is not None
    assert service.model.spec.networks[0].name == "vpc-main"
