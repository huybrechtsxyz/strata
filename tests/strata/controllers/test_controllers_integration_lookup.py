#!/usr/bin/env python3
"""Unit tests for the shared sibling-integration lookup helper (ADR-0003)."""

from unittest.mock import MagicMock, patch

from strata.controllers.integration_lookup import find_available_integration_with_capability


def _mock_service(capability_map):
    """capability_map: {capability_class: [(name, integration_or_None), ...]}"""
    svc = MagicMock()
    svc.is_initialized.return_value = True

    def get_integrations_with_capability(capability):
        return [name for name, _ in capability_map.get(capability, [])]

    def get_integration(name):
        for entries in capability_map.values():
            for entry_name, integration in entries:
                if entry_name == name:
                    return integration
        return None

    svc.get_integrations_with_capability.side_effect = get_integrations_with_capability
    svc.get_integration.side_effect = get_integration
    return svc


class _Capability:
    """Stand-in capability marker type for tests."""


class TestFindAvailableIntegrationWithCapability:
    def test_returns_first_available_integration(self):
        integration = MagicMock()
        integration.ensure_available.return_value = (True, "")
        svc = _mock_service({_Capability: [("foo", integration)]})
        with patch("strata.services.integration_service.IntegrationService.get_instance", return_value=svc):
            assert find_available_integration_with_capability(_Capability) is integration

    def test_skips_unavailable_integrations(self):
        unavailable = MagicMock()
        unavailable.ensure_available.return_value = (False, "not logged in")
        available = MagicMock()
        available.ensure_available.return_value = (True, "")
        svc = _mock_service({_Capability: [("first", unavailable), ("second", available)]})
        with patch("strata.services.integration_service.IntegrationService.get_instance", return_value=svc):
            assert find_available_integration_with_capability(_Capability) is available

    def test_returns_none_when_nothing_configured(self):
        svc = _mock_service({_Capability: []})
        with patch("strata.services.integration_service.IntegrationService.get_instance", return_value=svc):
            assert find_available_integration_with_capability(_Capability) is None

    def test_returns_none_when_all_unavailable(self):
        unavailable = MagicMock()
        unavailable.ensure_available.return_value = (False, "")
        svc = _mock_service({_Capability: [("foo", unavailable)]})
        with patch("strata.services.integration_service.IntegrationService.get_instance", return_value=svc):
            assert find_available_integration_with_capability(_Capability) is None

    def test_initializes_integrations_if_not_already(self):
        svc = _mock_service({_Capability: []})
        svc.is_initialized.return_value = False
        with patch("strata.services.integration_service.IntegrationService.get_instance", return_value=svc):
            find_available_integration_with_capability(_Capability)
        svc.initialize_integrations.assert_called_once()

    def test_skips_integration_that_resolves_to_none_by_name(self):
        svc = _mock_service({_Capability: [("foo", None)]})
        with patch("strata.services.integration_service.IntegrationService.get_instance", return_value=svc):
            assert find_available_integration_with_capability(_Capability) is None

    def test_never_raises_when_integration_service_throws(self):
        with patch(
            "strata.services.integration_service.IntegrationService.get_instance",
            side_effect=RuntimeError("boom"),
        ):
            assert find_available_integration_with_capability(_Capability) is None
