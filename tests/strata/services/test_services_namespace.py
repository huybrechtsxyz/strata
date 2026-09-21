#!/usr/bin/env python3
"""Tests for NamespaceService loading and validation."""

from strata.services.namespace_service import NamespaceService


def test_namespace_service_validates_from_data():
    """A NamespaceService constructed from an in-memory dict validates successfully."""
    data = {
        "meta": {"name": "myapp"},
        "spec": {
            "modules": [{"name": "template_module", "file": "config/myapp/modules/template-module.yaml"}],
        },
    }
    service = NamespaceService(data=data)
    is_valid, errors = service.validate()
    assert is_valid
    assert errors == []
    assert service.model is not None
    assert service.model.spec.modules[0].name == "template_module"
