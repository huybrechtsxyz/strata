#!/usr/bin/env python3
"""Unit tests for AzureIdentityIntegration — az CLI reuse before OIDC fallback (ADR-0067)."""

from unittest.mock import MagicMock, patch

import pytest

from strata.integrations.base_integration import BaseIntegration
from strata.integrations.identity.azure_identity_integration import AzureIdentityIntegration
from strata.models.auth_models import AuthenticationModel, OAuth2AuthenticationModel
from strata.models.integration_model import IntegrationEndpointsSpecModel, IntegrationModel
from strata.utils import identity_token_cache as cache


def _cfg_with_tenant(name="strata-control-plane") -> IntegrationModel:
    return IntegrationModel(
        name=name,
        type="azure_ad",
        capabilities={"identity"},
        authentication=AuthenticationModel(
            method="oauth2",
            oauth2=OAuth2AuthenticationModel(
                client_id="OIDC_CLIENT_ID", client_secret="UNUSED", tenant_id="OIDC_TENANT_ID"
            ),
        ),
    )


def _cfg_with_address(name="strata-control-plane") -> IntegrationModel:
    return IntegrationModel(
        name=name,
        type="azure_ad",
        capabilities={"identity"},
        endpoints=IntegrationEndpointsSpecModel(address="https://login.microsoftonline.com/my-tenant/v2.0"),
        authentication=AuthenticationModel(
            method="oauth2",
            oauth2=OAuth2AuthenticationModel(client_id="OIDC_CLIENT_ID", client_secret="UNUSED"),
        ),
    )


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    BaseIntegration._instances.clear()
    monkeypatch.setattr(cache, "_CACHE_DIR", tmp_path / "identity")
    monkeypatch.setenv("OIDC_CLIENT_ID", "abc123")
    monkeypatch.setenv("OIDC_TENANT_ID", "my-tenant")
    yield
    BaseIntegration._instances.clear()


def _mock_service(azure_cli_integration=None):
    if azure_cli_integration is not None:
        azure_cli_integration.ensure_available.return_value = (True, "")
    svc = MagicMock()
    svc.is_initialized.return_value = True
    svc.get_integrations_with_capability.return_value = ["azure"] if azure_cli_integration else []
    svc.get_integration.return_value = azure_cli_integration
    return svc


class TestIssuerResolution:
    def test_derives_issuer_from_tenant_id_env_var(self):
        i = AzureIdentityIntegration(_cfg_with_tenant())
        assert i._issuer == "https://login.microsoftonline.com/my-tenant/v2.0"

    def test_endpoints_address_takes_precedence(self):
        i = AzureIdentityIntegration(_cfg_with_address())
        assert i._issuer == "https://login.microsoftonline.com/my-tenant/v2.0"

    def test_missing_tenant_and_address_raises(self):
        cfg = IntegrationModel(
            name="x",
            type="azure_ad",
            authentication=AuthenticationModel(
                method="oauth2",
                oauth2=OAuth2AuthenticationModel(client_id="OIDC_CLIENT_ID", client_secret="UNUSED"),
            ),
        )
        i = AzureIdentityIntegration(cfg)
        with pytest.raises(ValueError):
            _ = i._issuer


class TestCheckAuthReuse:
    def test_reuses_authenticated_azure_cli_session(self):
        i = AzureIdentityIntegration(_cfg_with_tenant())
        azure_cli = MagicMock()
        azure_cli.get_access_token.return_value = "reused-token"
        azure_cli.get_signed_in_user.return_value = {"name": "dev@example.com", "type": "user"}
        svc = _mock_service(azure_cli_integration=azure_cli)

        with patch("strata.services.integration_service.IntegrationService.get_instance", return_value=svc):
            ok, detail = i.check_auth()

        assert ok is True
        assert "dev@example.com" in detail
        azure_cli.get_access_token.assert_called_once_with(resource="abc123")

    def test_falls_back_to_oidc_when_azure_cli_not_configured(self):
        i = AzureIdentityIntegration(_cfg_with_tenant())
        svc = _mock_service(azure_cli_integration=None)

        with patch("strata.services.integration_service.IntegrationService.get_instance", return_value=svc):
            ok, detail = i.check_auth()

        assert ok is False
        assert "--login" in detail

    def test_falls_back_to_oidc_when_azure_cli_not_authenticated(self):
        i = AzureIdentityIntegration(_cfg_with_tenant())
        azure_cli = MagicMock()
        azure_cli.ensure_available.return_value = (False, "not logged in")
        svc = MagicMock()
        svc.is_initialized.return_value = True
        svc.get_integrations_with_capability.return_value = ["azure"]
        svc.get_integration.return_value = azure_cli

        with patch("strata.services.integration_service.IntegrationService.get_instance", return_value=svc):
            ok, detail = i.check_auth()

        assert ok is False
        azure_cli.get_access_token.assert_not_called()


class TestLoginReuse:
    def test_reuse_means_no_separate_sign_in(self, capsys):
        i = AzureIdentityIntegration(_cfg_with_tenant())
        azure_cli = MagicMock()
        azure_cli.get_access_token.return_value = "reused-token"
        azure_cli.get_signed_in_user.return_value = {"name": "dev@example.com", "type": "user"}
        svc = _mock_service(azure_cli_integration=azure_cli)

        with patch("strata.services.integration_service.IntegrationService.get_instance", return_value=svc):
            ok, detail = i.login()

        assert ok is True
        assert "Reused existing az CLI login" in detail
        assert "" == capsys.readouterr().out  # no device-code prompt printed

    def test_falls_back_to_device_code_flow(self):
        i = AzureIdentityIntegration(_cfg_with_tenant())
        svc = _mock_service(azure_cli_integration=None)

        with (
            patch("strata.services.integration_service.IntegrationService.get_instance", return_value=svc),
            patch("urllib.request.urlopen", side_effect=OSError("no network")),
        ):
            ok, detail = i.login()

        assert ok is False
        assert "issuer" in detail.lower() or "login.microsoftonline.com" in detail


class TestGetAccessTokenReuse:
    def test_returns_reused_token(self):
        i = AzureIdentityIntegration(_cfg_with_tenant())
        azure_cli = MagicMock()
        azure_cli.get_access_token.return_value = "reused-token"
        svc = _mock_service(azure_cli_integration=azure_cli)

        with patch("strata.services.integration_service.IntegrationService.get_instance", return_value=svc):
            assert i.get_access_token() == "reused-token"

    def test_falls_back_to_cached_oidc_token_when_no_reuse(self):
        i = AzureIdentityIntegration(_cfg_with_tenant())
        svc = _mock_service(azure_cli_integration=None)
        cache.save_token(i.integration_name, {"access_token": "oidc-token", "expires_at": 9999999999, "claims": {}})

        with patch("strata.services.integration_service.IntegrationService.get_instance", return_value=svc):
            assert i.get_access_token() == "oidc-token"


class TestGetIdentityClaimsReuse:
    def test_returns_claims_from_reused_session(self):
        i = AzureIdentityIntegration(_cfg_with_tenant())
        azure_cli = MagicMock()
        azure_cli.get_access_token.return_value = "reused-token"
        azure_cli.get_signed_in_user.return_value = {"name": "dev@example.com", "type": "user"}
        svc = _mock_service(azure_cli_integration=azure_cli)

        with patch("strata.services.integration_service.IntegrationService.get_instance", return_value=svc):
            claims = i.get_identity_claims()

        assert claims == {
            "email": "dev@example.com",
            "preferred_username": "dev@example.com",
            "sub": "dev@example.com",
        }

    def test_falls_back_to_cached_claims_when_no_reuse(self):
        i = AzureIdentityIntegration(_cfg_with_tenant())
        svc = _mock_service(azure_cli_integration=None)
        cache.save_token(
            i.integration_name,
            {"access_token": "tok", "expires_at": 9999999999, "claims": {"sub": "u1"}},
        )

        with patch("strata.services.integration_service.IntegrationService.get_instance", return_value=svc):
            assert i.get_identity_claims() == {"sub": "u1"}
