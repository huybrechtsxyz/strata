#!/usr/bin/env python3
"""Tests for DnsService loading and validation."""

from strata.services.dns_service import DnsService


def test_dns_service_validates_from_data():
    """A DnsService constructed from an in-memory dict validates successfully."""
    data = {
        "meta": {"name": "public-dns"},
        "spec": {
            "zones": [
                {
                    "name": "example.com",
                    "records": [{"name": "@", "type": "A", "value": "1.2.3.4"}],
                    "default_tags": {"environment": "test"},
                }
            ]
        },
    }
    service = DnsService(data=data)
    result = service.validate()
    assert result.ok
    assert result.messages() == []
    assert service.model is not None
    assert service.model.spec.zones[0].name == "example.com"
