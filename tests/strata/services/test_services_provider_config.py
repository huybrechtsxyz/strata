#!/usr/bin/env python3
"""Tests for ProviderConfigService loading and validation."""

from strata.services.provider_config_service import ProviderConfigService


def test_provider_config_service_validates_from_data():
    """A ProviderConfigService constructed from an in-memory dict validates successfully."""
    data = {
        "meta": {"name": "kamatera"},
        "spec": {
            "description": "Kamatera cloud provider",
            "regions": [{"name": "eu-west"}],
            "resources": [{"name": "vm"}],
        },
    }
    service = ProviderConfigService(data=data)
    result = service.validate()
    assert result.ok
    assert result.messages() == []
    assert service.model is not None
    assert service.model.meta.name == "kamatera"
