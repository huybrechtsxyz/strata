#!/usr/bin/env python3
"""
===============================================================================
Script Name   : test_services_unknown.py
Author        : Vincent Huybrechts
Created       : 2026-02-09
Last Updated  : 2026-02-09
Version       : 1.0.0
Python Version: 3.12+
Description   : UnknownService test fixtures and utilities for strata CLI tests.
===============================================================================
"""

from pathlib import Path

import pytest

from strata.models.unknown_model import UnknownModel
from strata.services.firewall_service import FirewallService
from strata.services.namespace_service import NamespaceService
from strata.services.provider_service import ProviderService
from strata.services.resource_service import ResourceService
from strata.services.unknown_service import UnknownService
from strata.services.workspace_service import WorkspaceService


def _data(relative_path: str) -> str:
    return str(Path(__file__).parent.parent.parent / "data" / relative_path)


class TestUnknownService:
    @pytest.fixture
    def get_unknown_service(self):
        return UnknownService(_data("workspaces/workspace-standard.yaml"))

    @pytest.fixture
    def get_firewall_service(self):
        return UnknownService(_data("firewalls/firewall-standard.yaml"))

    @pytest.fixture
    def get_namespace_service(self):
        return UnknownService(_data("namespaces/namespace-standard.yaml"))

    @pytest.fixture
    def get_provider_service(self):
        return UnknownService(_data("providers/provider-standard.yaml"))

    @pytest.fixture
    def get_virtualmachine_service(self):
        return UnknownService(_data("resources/resource-standard.yaml"))

    @pytest.fixture
    def get_workspace_service(self):
        return UnknownService(_data("workspaces/workspace-standard.yaml"))

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
