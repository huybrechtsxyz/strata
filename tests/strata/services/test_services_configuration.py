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
    is_valid, errors = service.validate()
    assert is_valid
    assert errors == []
    assert service.model is not None
    assert service.model.spec.providers[0] == "kamatera"
