#!/usr/bin/env python3
"""
===============================================================================
Script Name   : test_services_provider.py
Author        : Vincent Huybrechts
Version       : 1.0.0
Python Version: 3.12+
Description   : ProviderService test fixtures and utilities for xyz-platform CLI tests.
===============================================================================
"""

import pytest

from tests.xyz_platform.test_common import get_data_path
from xyz_platform.models.provider_model import ProviderModel
from xyz_platform.services.provider_service import ProviderService


class TestProviderService:

    @pytest.fixture
    def get_provider_service(self):
        return ProviderService(get_data_path("providers/provider-standard.yaml"))

    def test_get_model_class(self, get_provider_service):
        service = get_provider_service
        model_class = service._get_model_class()
        assert model_class == ProviderModel

    def test_get_provider_type(self, get_provider_service):
        service = get_provider_service
        is_valid, errors = service.validate()
        assert is_valid, f"Provider validation failed: {errors}"

        provider_type = service.get_provider_type()
        assert provider_type == "kamatera"

    def test_get_provider_region(self, get_provider_service):
        service = get_provider_service
        is_valid, errors = service.validate()
        assert is_valid, f"Provider validation failed: {errors}"

        provider_region = service.get_provider_region()
        assert provider_region == "eu-fr"
