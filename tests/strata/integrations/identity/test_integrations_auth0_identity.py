#!/usr/bin/env python3
"""Unit tests for Auth0IdentityIntegration — thin subclass of the generic OIDC flow (ADR-0067)."""

import json
import time
from unittest.mock import MagicMock, patch

import pytest

from strata.integrations.base_integration import BaseIntegration
from strata.integrations.identity.auth0_identity_integration import Auth0IdentityIntegration
from strata.models.auth_models import AuthenticationModel, OAuth2AuthenticationModel
from strata.models.capabilities import IIdentityProvider
from strata.models.integration_model import IntegrationEndpointsSpecModel, IntegrationModel
from strata.utils import identity_token_cache as cache

_DISCOVERY = {
    "device_authorization_endpoint": "https://my-tenant.us.auth0.com/oauth/device/code",
    "token_endpoint": "https://my-tenant.us.auth0.com/oauth/token",
    "userinfo_endpoint": "https://my-tenant.us.auth0.com/userinfo",
}


def _cfg_with_domain(name="strata-control-plane") -> IntegrationModel:
    return IntegrationModel(
        name=name,
        type="auth0",
        capabilities={"identity"},
        authentication=AuthenticationModel(
            method="oauth2",
            oauth2=OAuth2AuthenticationModel(
                client_id="OIDC_CLIENT_ID", client_secret="UNUSED", tenant_id="AUTH0_DOMAIN"
            ),
        ),
    )


def _cfg_with_address(name="strata-control-plane") -> IntegrationModel:
    return IntegrationModel(
        name=name,
        type="auth0",
        capabilities={"identity"},
        endpoints=IntegrationEndpointsSpecModel(address="https://my-tenant.us.auth0.com"),
        authentication=AuthenticationModel(
            method="oauth2",
            oauth2=OAuth2AuthenticationModel(client_id="OIDC_CLIENT_ID", client_secret="UNUSED"),
        ),
    )


def _mock_response(payload: dict, status: int = 200):
    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps(payload).encode("utf-8")
    mock_resp.status = status
    mock_cm = MagicMock()
    mock_cm.__enter__.return_value = mock_resp
    return mock_cm


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    BaseIntegration._instances.clear()
    monkeypatch.setattr(cache, "_CACHE_DIR", tmp_path / "identity")
    monkeypatch.setenv("OIDC_CLIENT_ID", "abc123")
    monkeypatch.setenv("AUTH0_DOMAIN", "my-tenant.us.auth0.com")
    yield
    BaseIntegration._instances.clear()


class TestCapability:
    def test_declares_identity_provider_capability(self):
        assert IIdentityProvider in Auth0IdentityIntegration.CAPABILITIES


class TestIssuerResolution:
    def test_derives_issuer_from_domain_env_var(self):
        i = Auth0IdentityIntegration(_cfg_with_domain())
        assert i._issuer == "https://my-tenant.us.auth0.com"

    def test_endpoints_address_takes_precedence(self):
        i = Auth0IdentityIntegration(_cfg_with_address())
        assert i._issuer == "https://my-tenant.us.auth0.com"

    def test_missing_domain_and_address_raises(self):
        cfg = IntegrationModel(
            name="x",
            type="auth0",
            authentication=AuthenticationModel(
                method="oauth2",
                oauth2=OAuth2AuthenticationModel(client_id="OIDC_CLIENT_ID", client_secret="UNUSED"),
            ),
        )
        i = Auth0IdentityIntegration(cfg)
        with pytest.raises(ValueError):
            _ = i._issuer

    def test_missing_domain_env_var_raises(self, monkeypatch):
        monkeypatch.delenv("AUTH0_DOMAIN", raising=False)
        i = Auth0IdentityIntegration(_cfg_with_domain())
        with pytest.raises(ValueError):
            _ = i._issuer


class TestNoReusePath:
    """Unlike Azure/Google, Auth0 has no existing strata integration to reuse."""

    def test_check_auth_goes_straight_to_generic_oidc_cache(self):
        i = Auth0IdentityIntegration(_cfg_with_domain())
        ok, detail = i.check_auth()
        assert ok is False
        assert "--login" in detail

    def test_valid_cached_session_passes(self):
        i = Auth0IdentityIntegration(_cfg_with_domain())
        cache.save_token(
            i.integration_name,
            {
                "access_token": "tok",
                "expires_at": time.time() + 3600,
                "claims": {"email": "dev@example.com"},
            },
        )
        ok, detail = i.check_auth()
        assert ok is True
        assert "dev@example.com" in detail


class TestLogin:
    def test_successful_device_code_login(self, capsys):
        i = Auth0IdentityIntegration(_cfg_with_domain())
        with patch("urllib.request.urlopen") as mock_urlopen, patch("time.sleep"):
            mock_urlopen.side_effect = [
                _mock_response(_DISCOVERY),
                _mock_response(
                    {
                        "device_code": "devcode",
                        "user_code": "ABCD-1234",
                        "verification_uri": "https://my-tenant.us.auth0.com/activate",
                        "interval": 1,
                        "expires_in": 30,
                    }
                ),
                _mock_response({"access_token": "tok", "expires_in": 3600}),
                _mock_response({"email": "dev@example.com", "sub": "auth0|u123"}),
            ]
            ok, detail = i.login()

        assert ok is True
        assert "dev@example.com" in detail
        cached = cache.load_token(i.integration_name)
        assert cached["access_token"] == "tok"
        assert "ABCD-1234" in capsys.readouterr().out

    def test_discovery_unreachable(self):
        i = Auth0IdentityIntegration(_cfg_with_domain())
        with patch("urllib.request.urlopen", side_effect=OSError("no network")):
            ok, detail = i.login()
        assert ok is False
        assert "my-tenant.us.auth0.com" in detail


class TestGetAccessTokenAndClaims:
    def test_get_access_token_returns_cached_token(self):
        i = Auth0IdentityIntegration(_cfg_with_domain())
        cache.save_token(i.integration_name, {"access_token": "tok", "expires_at": time.time() + 3600, "claims": {}})
        assert i.get_access_token() == "tok"

    def test_get_identity_claims_returns_cached_claims(self):
        i = Auth0IdentityIntegration(_cfg_with_domain())
        cache.save_token(
            i.integration_name,
            {"access_token": "tok", "expires_at": time.time() + 3600, "claims": {"sub": "auth0|u1"}},
        )
        assert i.get_identity_claims() == {"sub": "auth0|u1"}
