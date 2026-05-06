#!/usr/bin/env python3
"""
===============================================================================
Script Name   : test_services_namespace.py
Author        : Vincent Huybrechts
Created       : 2026-02-09
Last Updated  : 2026-02-09
Version       : 1.0.0
Python Version: 3.12+
Description   : NamespaceService test fixtures and utilities for xyz-platform CLI tests.
===============================================================================
"""

from pathlib import Path

import pytest

from xyz_platform.models.namespace_model import NamespaceModel
from xyz_platform.services.namespace_service import NamespaceService


def _data(relative_path: str) -> str:
    return str(Path(__file__).parent.parent.parent / "data" / relative_path)


class TestNamespaceService:
    @pytest.fixture
    def get_namespace_service(self):
        return NamespaceService(_data("namespaces/namespace-standard.yaml"))

    def test_get_model_class(self, get_namespace_service):
        service = get_namespace_service
        model_class = service._get_model_class()
        assert model_class == NamespaceModel

    def test_validate_standard(self, get_namespace_service):
        service = get_namespace_service
        is_valid, errors = service.validate()
        assert is_valid, f"Validation failed: {errors}"
        assert service.is_validated()

    def test_get_kind_after_validate(self, get_namespace_service):
        service = get_namespace_service
        service.validate()
        assert service.get_kind() == "namespace"
