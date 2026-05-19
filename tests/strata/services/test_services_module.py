#!/usr/bin/env python3
"""
===============================================================================
Script Name   : test_services_module.py
Author        : Vincent Huybrechts
Created       : 2026-02-09
Last Updated  : 2026-02-09
Version       : 1.0.0
Python Version: 3.12+
Description   : ModuleService test fixtures and utilities for strata CLI tests.
===============================================================================
"""

from pathlib import Path

import pytest

from strata.models.module_model import ModuleModel
from strata.services.module_service import ModuleService


def _data(relative_path: str) -> str:
    return str(Path(__file__).parent.parent.parent / "data" / relative_path)


class TestModuleService:
    @pytest.fixture
    def get_module_service(self):
        return ModuleService(_data("modules/module-standard.yaml"))

    def test_get_model_class(self, get_module_service):
        service = get_module_service
        model_class = service._get_model_class()
        assert model_class == ModuleModel

    def test_validate_standard(self, get_module_service):
        service = get_module_service
        is_valid, errors = service.validate()
        assert is_valid, f"Validation failed: {errors}"
        assert service.is_validated()

    def test_get_kind_after_validate(self, get_module_service):
        service = get_module_service
        service.validate()
        assert service.get_kind() == "module"
