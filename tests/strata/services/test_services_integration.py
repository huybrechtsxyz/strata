#!/usr/bin/env python3
"""Tests for IntegrationService loading and validation."""

from strata.services.integration_service import IntegrationService


def test_integration_service_validates_from_data():
    """An IntegrationService constructed from an in-memory dict validates successfully."""
    data = {
        "meta": {"name": "vault-main"},
        "spec": {"type": "vault", "capabilities": ["secrets"]},
    }
    service = IntegrationService(data=data)
    is_valid, errors = service.validate()
    assert is_valid
    assert errors == []
    assert service.model is not None
    assert service.model.spec.type == "vault"
