#!/usr/bin/env python3
"""Tests for NamespaceService loading and validation."""

from strata.services.namespace_service import NamespaceService


def test_namespace_service_validates_from_data():
    """A NamespaceService constructed from an in-memory dict validates successfully."""
    data = {
        "meta": {"name": "myapp"},
        "spec": {
            "modules": [{"name": "template_module", "module": "template-module"}],
            "default_labels": {"environment": "test"},
        },
    }
    service = NamespaceService(data=data)
    result = service.validate()
    assert result.ok
    assert result.messages() == []
    assert service.model is not None
    assert service.model.spec.modules[0].name == "template_module"
