#!/usr/bin/env python3
"""Tests for ConfigurationService loading and validation."""

from strata.services.configuration_service import ConfigurationService


def test_configuration_service_validates_from_data():
    """A ConfigurationService constructed from an in-memory dict validates successfully."""
    data = {
        "meta": {"name": "solution-config"},
        "spec": {
            "providers": ["kamatera"],
        },
    }
    service = ConfigurationService(data=data)
    result = service.validate()
    assert result.ok
    assert result.messages() == []
    assert service.model is not None
    assert service.model.spec.providers[0] == "kamatera"
