#!/usr/bin/env python3
"""
===============================================================================
Script Name   : test_services_provider.py
Author        : Vincent Huybrechts
Created       : 2026-02-09
Last Updated  : 2026-02-09
Version       : 1.0.0
Python Version: 3.12+
Description   : ProviderService test fixtures and utilities for strata CLI tests.
===============================================================================
"""

from pathlib import Path

import pytest

from strata.models.provider_model import ProviderModel
from strata.services.provider_service import ProviderService


def _data(relative_path: str) -> str:
    return str(Path(__file__).parent.parent.parent / "data" / relative_path)


class TestProviderService:
    @pytest.fixture
    def get_provider_service(self):
        return ProviderService(_data("providers/provider-standard.yaml"))

    def test_get_model_class(self, get_provider_service):
        service = get_provider_service
        model_class = service._get_model_class()
        assert model_class == ProviderModel

    def test_validate_standard(self, get_provider_service):
        service = get_provider_service
        is_valid, errors = service.validate()
        assert is_valid, f"Validation failed: {errors}"
        assert service.is_validated()

    def test_get_kind_after_validate(self, get_provider_service):
        service = get_provider_service
        service.validate()
        assert service.get_kind() == "provider"
