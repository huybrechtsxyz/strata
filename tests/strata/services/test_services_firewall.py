#!/usr/bin/env python3
"""
===============================================================================
Script Name   : test_services_firewall.py
Author        : Vincent Huybrechts
Created       : 2026-02-09
Last Updated  : 2026-02-09
Version       : 1.0.0
Python Version: 3.12+
Description   : FirewallService test fixtures and utilities for strata CLI tests.
===============================================================================
"""

from pathlib import Path

import pytest

from strata.models.firewall_model import FirewallModel
from strata.services.firewall_service import FirewallService


def _data(relative_path: str) -> str:
    return str(Path(__file__).parent.parent.parent / "data" / relative_path)


class TestFirewallService:
    @pytest.fixture
    def get_firewall_service(self):
        return FirewallService(_data("firewalls/firewall-standard.yaml"))

    def test_get_model_class(self, get_firewall_service):
        service = get_firewall_service
        model_class = service._get_model_class()
        assert model_class == FirewallModel

    def test_validate_standard(self, get_firewall_service):
        service = get_firewall_service
        is_valid, errors = service.validate()
        assert is_valid, f"Validation failed: {errors}"
        assert service.is_validated()

    def test_get_kind_after_validate(self, get_firewall_service):
        service = get_firewall_service
        service.validate()
        assert service.get_kind() == "firewall"
