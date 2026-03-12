#!/usr/bin/env python3
"""
===============================================================================
Script Name   : test_services_unknown.py
Author        : Vincent Huybrechts
Created       : 2026-02-09
Last Updated  : 2026-02-09
Version       : 1.0.0
Python Version: 3.12+
Description   : UnknownService test fixtures and utilities for xyz-platform CLI tests.
===============================================================================
"""

import pytest

from tests.xyz_platform.test_common import get_data_path
from xyz_platform.models.unknown_model import UnknownModel
from xyz_platform.services.firewall_service import FirewallService
from xyz_platform.services.namespace_service import NamespaceService
from xyz_platform.services.provider_service import ProviderService
from xyz_platform.services.unknown_service import UnknownService
from xyz_platform.services.resource_service import ResourceService
from xyz_platform.services.workspace_service import WorkspaceService


class TestUnknownService:

    @pytest.fixture
    def get_unknown_service(self):
        return UnknownService(get_data_path("workspaces/workspace-standard.yaml"))

    @pytest.fixture
    def get_firewall_service(self):
        return UnknownService(get_data_path("firewalls/firewall-standard.yaml"))

    @pytest.fixture
    def get_namespace_service(self):
        return UnknownService(get_data_path("namespaces/namespace-standard.yaml"))

    @pytest.fixture
    def get_provider_service(self):
        return UnknownService(get_data_path("providers/provider-standard.yaml"))

    @pytest.fixture
    def get_virtualmachine_service(self):
        return UnknownService(get_data_path("resources/resource-standard.yaml"))

    @pytest.fixture
    def get_workspace_service(self):
        return UnknownService(get_data_path("workspaces/workspace-standard.yaml"))

    def test_get_model_class(self, get_unknown_service):
        service = get_unknown_service
        model_class = service._get_model_class()
        assert model_class == UnknownModel

    def test_is_workspace(self, get_workspace_service):
        service = get_workspace_service
        service.validate()
        assert service.is_workspace() is True

    def test_kind_is_firewall(self, get_firewall_service):
        service = get_firewall_service
        service.validate()
        result = service.get_service_by_kind()
        assert isinstance(result, FirewallService)

    def test_kind_is_namespace(self, get_namespace_service):
        service = get_namespace_service
        service.validate()
        result = service.get_service_by_kind()
        assert isinstance(result, NamespaceService)

    def test_kind_is_provider(self, get_provider_service):
        service = get_provider_service
        service.validate()
        result = service.get_service_by_kind()
        assert isinstance(result, ProviderService)

    def test_kind_is_resource(self, get_virtualmachine_service):
        service = get_virtualmachine_service
        service.validate()
        result = service.get_service_by_kind()
        assert isinstance(result, ResourceService)

    def test_kind_is_workspace(self, get_workspace_service):
        service = get_workspace_service
        service.validate()
        result = service.get_service_by_kind()
        assert isinstance(result, WorkspaceService)
