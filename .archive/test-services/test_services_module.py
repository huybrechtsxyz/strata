#!/usr/bin/env python3
"""
===============================================================================
Script Name   : test_services_module.py
Author        : Vincent Huybrechts
Version       : 1.0.0
Python Version: 3.12+
Description   : ModuleService test fixtures and utilities for xyz-platform CLI tests.
===============================================================================
"""

import pytest

from tests.xyz_platform.test_common import get_data_path
from xyz_platform.models.module_model import ModuleModel
from xyz_platform.services.module_service import ModuleService


class TestModuleService:

    @pytest.fixture
    def get_module_service(self):
        return ModuleService(get_data_path("modules/module-standard.yaml"))

    def test_get_model_class(self, get_module_service):
        service = get_module_service
        model_class = service._get_model_class()
        assert model_class == ModuleModel
