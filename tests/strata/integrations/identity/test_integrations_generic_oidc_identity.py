#!/usr/bin/env python3
"""Unit tests for GenericOidcIdentityIntegration (ADR-0067)."""

import json
import time
from unittest.mock import MagicMock, patch

import pytest

from strata.integrations.base_integration import BaseIntegration
from strata.integrations.identity.generic_oidc_identity_integration import GenericOidcIdentityIntegration
from strata.models.auth_models import AuthenticationModel, OAuth2AuthenticationModel
from strata.models.capabilities import IIdentityProvider
from strata.models.integration_model import IntegrationEndpointsSpecModel, IntegrationModel
from strata.utils import identity_token_cache as cache

_DISCOVERY = {
    "device_authorization_endpoint": "https://issuer.example.com/device",
    "token_endpoint": "https://issuer.example.com/token",
    "userinfo_endpoint": "https://issuer.example.com/userinfo",
}


def _cfg(name="strata-control-plane") -> IntegrationModel:
    return IntegrationModel(
        name=name,
        type="generic_oidc",
        capabilities={"identity"},
        endpoints=IntegrationEndpointsSpecModel(address="https://issuer.example.com"),
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
    yield
    BaseIntegration._instances.clear()


class TestCapability:
    def test_declares_identity_provider_capability(self):
        assert IIdentityProvider in GenericOidcIdentityIntegration.CAPABILITIES


class TestConfig:
    def test_client_id_resolved_from_env(self):
        i = GenericOidcIdentityIntegration(_cfg())
        assert i._client_id == "abc123"

    def test_missing_env_var_raises(self, monkeypatch):
        monkeypatch.delenv("OIDC_CLIENT_ID", raising=False)
        i = GenericOidcIdentityIntegration(_cfg())
        with pytest.raises(ValueError):
            _ = i._client_id

    def test_default_scope(self):
        i = GenericOidcIdentityIntegration(_cfg())
        assert "openid" in i._scope


class TestCheckAuthNoSession:
    def test_no_cache_means_not_logged_in(self):
        i = GenericOidcIdentityIntegration(_cfg())
        ok, detail = i.check_auth()
        assert ok is False
        assert "--login" in detail


class TestCheckAuthValidSession:
    def test_valid_unexpired_token_passes(self):
        i = GenericOidcIdentityIntegration(_cfg())
        cache.save_token(
            i.integration_name,
            {
                "access_token": "tok",
                "refresh_token": "refresh",
                "expires_at": time.time() + 3600,
                "claims": {"email": "dev@example.com"},
            },
        )
        ok, detail = i.check_auth()
        assert ok is True
        assert "dev@example.com" in detail


class TestCheckAuthRefresh:
    def test_expired_token_refreshes_silently(self):
        i = GenericOidcIdentityIntegration(_cfg())
        cache.save_token(
            i.integration_name,
            {
                "access_token": "old",
                "refresh_token": "refresh-me",
                "expires_at": time.time() - 10,
                "claims": {"email": "dev@example.com"},
            },
        )
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.side_effect = [
                _mock_response(_DISCOVERY),
                _mock_response({"access_token": "new", "expires_in": 3600}),
            ]
            ok, detail = i.check_auth()

        assert ok is True
        assert "refreshed" in detail
        assert cache.load_token(i.integration_name)["access_token"] == "new"

    def test_expired_token_no_refresh_token_fails(self):
        i = GenericOidcIdentityIntegration(_cfg())
        cache.save_token(
            i.integration_name,
            {"access_token": "old", "expires_at": time.time() - 10, "claims": {}},
        )
        ok, detail = i.check_auth()
        assert ok is False
        assert "--login" in detail

    def test_expired_token_refresh_rejected_clears_cache(self):
        i = GenericOidcIdentityIntegration(_cfg())
        cache.save_token(
            i.integration_name,
            {"access_token": "old", "refresh_token": "bad", "expires_at": time.time() - 10, "claims": {}},
        )
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.side_effect = [
                _mock_response(_DISCOVERY),
                _mock_response({"error": "invalid_grant"}, status=400),
            ]
            ok, detail = i.check_auth()

        assert ok is False
        assert cache.load_token(i.integration_name) is None


class TestLogin:
    def test_successful_device_code_login(self, capsys):
        i = GenericOidcIdentityIntegration(_cfg())
        with patch("urllib.request.urlopen") as mock_urlopen, patch("time.sleep"):
            mock_urlopen.side_effect = [
                _mock_response(_DISCOVERY),  # discovery
                _mock_response(
                    {
                        "device_code": "devcode",
                        "user_code": "ABCD-1234",
                        "verification_uri": "https://issuer.example.com/activate",
                        "interval": 1,
                        "expires_in": 30,
                    }
                ),  # device authorization
                _mock_response({"error": "authorization_pending"}, status=400),  # first poll
                _mock_response(
                    {"access_token": "tok", "refresh_token": "ref", "expires_in": 3600}
                ),  # second poll — success
                _mock_response({"email": "dev@example.com", "sub": "u123"}),  # userinfo
            ]
            ok, detail = i.login()

        assert ok is True
        assert "dev@example.com" in detail
        cached = cache.load_token(i.integration_name)
        assert cached["access_token"] == "tok"
        assert cached["claims"]["email"] == "dev@example.com"
        assert "ABCD-1234" in capsys.readouterr().out

    def test_device_authorization_request_failure(self):
        i = GenericOidcIdentityIntegration(_cfg())
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.side_effect = [
                _mock_response(_DISCOVERY),
                _mock_response({"error": "invalid_client"}, status=400),
            ]
            ok, detail = i.login()
        assert ok is False
        assert "failed" in detail.lower()

    def test_discovery_unreachable(self):
        i = GenericOidcIdentityIntegration(_cfg())
        with patch("urllib.request.urlopen", side_effect=OSError("no network")):
            ok, detail = i.login()
        assert ok is False
        assert "issuer.example.com" in detail


class TestGetAccessToken:
    def test_returns_none_when_not_logged_in(self):
        i = GenericOidcIdentityIntegration(_cfg())
        assert i.get_access_token() is None

    def test_returns_cached_token_when_valid(self):
        i = GenericOidcIdentityIntegration(_cfg())
        cache.save_token(
            i.integration_name,
            {"access_token": "tok", "expires_at": time.time() + 3600, "claims": {}},
        )
        assert i.get_access_token() == "tok"


class TestGetIdentityClaims:
    def test_returns_none_when_not_logged_in(self):
        i = GenericOidcIdentityIntegration(_cfg())
        assert i.get_identity_claims() is None

    def test_returns_cached_claims(self):
        i = GenericOidcIdentityIntegration(_cfg())
        cache.save_token(
            i.integration_name,
            {"access_token": "tok", "expires_at": time.time() + 3600, "claims": {"sub": "u1"}},
        )
        assert i.get_identity_claims() == {"sub": "u1"}
