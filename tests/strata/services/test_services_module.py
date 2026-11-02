#!/usr/bin/env python3
"""Tests for ModuleService loading and validation."""

from strata.services.module_service import ModuleService


def test_module_service_validates_from_data():
    """A ModuleService constructed from an in-memory dict validates successfully."""
    data = {
        "meta": {"name": "authentik"},
        "spec": {
            "source": {"chart_name": "authentik", "remote": "goauthentik"},
            "default_labels": {"app.kubernetes.io/name": "authentik"},
        },
    }
    service = ModuleService(data=data)
    result = service.validate()
    assert result.ok
    assert result.messages() == []
    assert service.model is not None
    assert service.model.spec.source.chart_name == "authentik"
