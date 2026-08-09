#!/usr/bin/env python3
"""Unit tests for IdentityController (ADR-0067)."""

from unittest.mock import MagicMock, patch

from strata.controllers.identity_controller import IdentityController


def _mock_service(integration=None, names=None):
    svc = MagicMock()
    svc.is_initialized.return_value = True
    svc.get_integrations_with_capability.return_value = names or (["idp"] if integration else [])
    svc.get_integration.return_value = integration
    return svc


class TestGetIntegration:
    def test_resolves_by_name(self):
        integration = MagicMock()
        svc = _mock_service(integration=integration)
        with patch("strata.services.integration_service.IntegrationService.get_instance", return_value=svc):
            ctrl = IdentityController()
            result = ctrl._get_integration("idp")
        svc.get_integration.assert_called_once_with("idp")
        assert result is integration

    def test_resolves_first_identity_capable_integration_when_no_name_given(self):
        integration = MagicMock()
        svc = _mock_service(integration=integration, names=["idp"])
        with patch("strata.services.integration_service.IntegrationService.get_instance", return_value=svc):
            ctrl = IdentityController()
            result = ctrl._get_integration()
        assert result is integration

    def test_none_when_no_identity_integration_configured(self):
        svc = _mock_service(integration=None, names=[])
        with patch("strata.services.integration_service.IntegrationService.get_instance", return_value=svc):
            ctrl = IdentityController()
            assert ctrl._get_integration() is None

    def test_initializes_integrations_if_not_already(self):
        svc = _mock_service()
        svc.is_initialized.return_value = False
        with patch("strata.services.integration_service.IntegrationService.get_instance", return_value=svc):
            ctrl = IdentityController()
            ctrl._get_integration()
        svc.initialize_integrations.assert_called_once()


class TestEnsureLoggedIn:
    def test_no_integration_configured(self):
        svc = _mock_service(integration=None, names=[])
        with patch("strata.services.integration_service.IntegrationService.get_instance", return_value=svc):
            ctrl = IdentityController()
            ok, detail = ctrl.ensure_logged_in()
        assert ok is False
        assert "identity" in detail.lower()

    def test_already_authenticated_skips_login(self):
        integration = MagicMock()
        integration.check_auth.return_value = (True, "Authenticated as dev@example.com")
        svc = _mock_service(integration=integration, names=["idp"])
        with patch("strata.services.integration_service.IntegrationService.get_instance", return_value=svc):
            ctrl = IdentityController()
            ok, detail = ctrl.ensure_logged_in()
        assert ok is True
        integration.login.assert_not_called()

    def test_triggers_login_lazily_when_not_authenticated(self):
        integration = MagicMock()
        integration.check_auth.return_value = (False, "Not logged in.")
        integration.login.return_value = (True, "Logged in as dev@example.com")
        svc = _mock_service(integration=integration, names=["idp"])
        with patch("strata.services.integration_service.IntegrationService.get_instance", return_value=svc):
            ctrl = IdentityController()
            ok, detail = ctrl.ensure_logged_in()
        assert ok is True
        integration.login.assert_called_once()


class TestGetToken:
    def test_returns_none_without_integration(self):
        svc = _mock_service(integration=None, names=[])
        with patch("strata.services.integration_service.IntegrationService.get_instance", return_value=svc):
            ctrl = IdentityController()
            assert ctrl.get_token() is None

    def test_returns_none_when_login_fails(self):
        integration = MagicMock()
        integration.check_auth.return_value = (False, "no")
        integration.login.return_value = (False, "no")
        svc = _mock_service(integration=integration, names=["idp"])
        with patch("strata.services.integration_service.IntegrationService.get_instance", return_value=svc):
            ctrl = IdentityController()
            assert ctrl.get_token() is None

    def test_returns_token_when_authenticated(self):
        integration = MagicMock()
        integration.check_auth.return_value = (True, "ok")
        integration.get_access_token.return_value = "tok-123"
        svc = _mock_service(integration=integration, names=["idp"])
        with patch("strata.services.integration_service.IntegrationService.get_instance", return_value=svc):
            ctrl = IdentityController()
            assert ctrl.get_token() == "tok-123"


class TestGetActorIdentity:
    def test_returns_none_without_integration(self):
        svc = _mock_service(integration=None, names=[])
        with patch("strata.services.integration_service.IntegrationService.get_instance", return_value=svc):
            ctrl = IdentityController()
            assert ctrl.get_actor_identity() is None

    def test_returns_none_when_not_authenticated(self):
        integration = MagicMock()
        integration.check_auth.return_value = (False, "no")
        svc = _mock_service(integration=integration, names=["idp"])
        with patch("strata.services.integration_service.IntegrationService.get_instance", return_value=svc):
            ctrl = IdentityController()
            assert ctrl.get_actor_identity() is None

    def test_prefers_email_over_preferred_username_and_sub(self):
        integration = MagicMock()
        integration.check_auth.return_value = (True, "ok")
        integration.get_identity_claims.return_value = {
            "email": "dev@example.com",
            "preferred_username": "dev",
            "sub": "u123",
        }
        svc = _mock_service(integration=integration, names=["idp"])
        with patch("strata.services.integration_service.IntegrationService.get_instance", return_value=svc):
            ctrl = IdentityController()
            assert ctrl.get_actor_identity() == "dev@example.com"

    def test_falls_back_to_sub_when_no_email_or_username(self):
        integration = MagicMock()
        integration.check_auth.return_value = (True, "ok")
        integration.get_identity_claims.return_value = {"sub": "u123"}
        svc = _mock_service(integration=integration, names=["idp"])
        with patch("strata.services.integration_service.IntegrationService.get_instance", return_value=svc):
            ctrl = IdentityController()
            assert ctrl.get_actor_identity() == "u123"

    def test_returns_none_without_claims(self):
        integration = MagicMock()
        integration.check_auth.return_value = (True, "ok")
        integration.get_identity_claims.return_value = None
        svc = _mock_service(integration=integration, names=["idp"])
        with patch("strata.services.integration_service.IntegrationService.get_instance", return_value=svc):
            ctrl = IdentityController()
            assert ctrl.get_actor_identity() is None
