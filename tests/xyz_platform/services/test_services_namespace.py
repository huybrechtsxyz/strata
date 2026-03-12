#!/usr/bin/env python3
"""
===============================================================================
Script Name   : test_services_namespace.py
Author        : Vincent Huybrechts
Version       : 1.0.0
Python Version: 3.12+
Description   : NamespaceService test fixtures and utilities for xyz-platform CLI tests.
===============================================================================
"""

import pytest

from tests.xyz_platform.test_common import get_data_path
from xyz_platform.models.namespace_model import NamespaceModel
from xyz_platform.services.namespace_service import NamespaceService


class TestNamespaceService:

    @pytest.fixture
    def get_namespace_service(self):
        return NamespaceService(get_data_path("namespaces/namespace-standard.yaml"))

    def test_get_model_class(self, get_namespace_service):
        service = get_namespace_service
        model_class = service._get_model_class()
        assert model_class == NamespaceModel
