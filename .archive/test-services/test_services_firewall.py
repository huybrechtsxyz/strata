#!/usr/bin/env python3
"""
===============================================================================
Script Name   : test_services_firewall.py
Author        : Vincent Huybrechts
Version       : 1.0.0
Python Version: 3.12+
Description   : FirewallService test fixtures and utilities for xyz-platform CLI tests.
===============================================================================
"""

import pytest

from tests.xyz_platform.test_common import get_data_path
from xyz_platform.models.firewall_model import FirewallModel
from xyz_platform.services.firewall_service import FirewallService


class TestFirewallService:

    @pytest.fixture
    def get_firewall_service(self):
        return FirewallService(get_data_path("firewalls/firewall-standard.yaml"))

    def test_get_model_class(self, get_firewall_service):
        service = get_firewall_service
        model_class = service._get_model_class()
        assert model_class == FirewallModel
