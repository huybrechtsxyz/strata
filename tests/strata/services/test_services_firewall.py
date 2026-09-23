#!/usr/bin/env python3
"""Tests for FirewallService loading and validation."""

from strata.services.firewall_service import FirewallService


def test_firewall_service_validates_from_data():
    """A FirewallService constructed from an in-memory dict validates successfully."""
    data = {
        "meta": {"name": "web-firewall"},
        "spec": {
            "allow": [
                {"direction": "in", "proto": "tcp", "port": 443, "from": "0.0.0.0/0"},
            ],
            "default_tags": {"environment": "test"},
        },
    }
    service = FirewallService(data=data)
    result = service.validate()
    assert result.ok
    assert result.messages() == []
    assert service.model is not None
    assert service.model.spec.allow[0].direction.value == "in"
