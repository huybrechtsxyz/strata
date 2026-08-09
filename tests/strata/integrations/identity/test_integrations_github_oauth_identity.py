#!/usr/bin/env python3
"""Unit tests for GitHubOAuthIdentityIntegration — device-code login, no OIDC discovery (ADR-0067)."""

import json
import time
from unittest.mock import MagicMock, patch

import pytest

from strata.integrations.base_integration import BaseIntegration
from strata.integrations.identity.github_oauth_identity_integration import GitHubOAuthIdentityIntegration
from strata.models.auth_models import AuthenticationModel, OAuth2AuthenticationModel
from strata.models.capabilities import IIdentityProvider
from strata.models.integration_model import IntegrationEndpointsSpecModel, IntegrationModel
from strata.utils import identity_token_cache as cache


def _cfg(name="strata-control-plane", address=None) -> IntegrationModel:
    endpoints = IntegrationEndpointsSpecModel(address=address) if address else None
    return IntegrationModel(
        name=name,
        type="github_oauth",
        capabilities={"identity"},
        endpoints=endpoints,
        authentication=AuthenticationModel(
            method="oauth2",
            oauth2=OAuth2AuthenticationModel(client_id="GH_CLIENT_ID", client_secret="UNUSED"),
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
    monkeypatch.setenv("GH_CLIENT_ID", "client123")
    yield
    BaseIntegration._instances.clear()


class TestCapability:
    def test_declares_identity_provider_capability(self):
        assert IIdentityProvider in GitHubOAuthIdentityIntegration.CAPABILITIES


class TestConfig:
    def test_client_id_resolved_from_env(self):
        i = GitHubOAuthIdentityIntegration(_cfg())
        assert i._client_id == "client123"

    def test_default_scope(self):
        i = GitHubOAuthIdentityIntegration(_cfg())
        assert i._scope == "read:user user:email"

    def test_default_endpoints_are_github_dot_com(self):
        i = GitHubOAuthIdentityIntegration(_cfg())
        assert i._device_code_url == "https://github.com/login/device/code"
        assert i._token_url == "https://github.com/login/oauth/access_token"
        assert i._api_base == "https://api.github.com"

    def test_ghes_base_url_overrides_endpoints(self):
        i = GitHubOAuthIdentityIntegration(_cfg(address="https://github.example.com"))
        assert i._device_code_url == "https://github.example.com/login/device/code"
        assert i._token_url == "https://github.example.com/login/oauth/access_token"
        assert i._api_base == "https://github.example.com/api/v3"


class TestCheckAuthNoSession:
    def test_no_cache_means_not_logged_in(self):
        i = GitHubOAuthIdentityIntegration(_cfg())
        ok, detail = i.check_auth()
        assert ok is False
        assert "--login" in detail


class TestCheckAuthClassicNonExpiringToken:
    def test_token_without_expiry_is_trusted_without_a_live_call(self):
        i = GitHubOAuthIdentityIntegration(_cfg())
        cache.save_token(
            i.integration_name,
            {"access_token": "tok", "claims": {"preferred_username": "octocat", "email": "octocat@example.com"}},
        )
        with patch("urllib.request.urlopen") as mock_urlopen:
            ok, detail = i.check_auth()
        assert ok is True
        assert "octocat" in detail
        mock_urlopen.assert_not_called()


class TestCheckAuthExpiringToken:
    def test_valid_unexpired_token_passes(self):
        i = GitHubOAuthIdentityIntegration(_cfg())
        cache.save_token(
            i.integration_name,
            {
                "access_token": "tok",
                "refresh_token": "refresh",
                "expires_at": time.time() + 3600,
                "claims": {"preferred_username": "octocat"},
            },
        )
        ok, detail = i.check_auth()
        assert ok is True
        assert "octocat" in detail

    def test_expired_token_refreshes_silently(self):
        i = GitHubOAuthIdentityIntegration(_cfg())
        cache.save_token(
            i.integration_name,
            {
                "access_token": "old",
                "refresh_token": "refresh-me",
                "expires_at": time.time() - 10,
                "claims": {"preferred_username": "octocat"},
            },
        )
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.return_value = _mock_response({"access_token": "new", "expires_in": 3600})
            ok, detail = i.check_auth()

        assert ok is True
        assert "refreshed" in detail
        assert cache.load_token(i.integration_name)["access_token"] == "new"

    def test_expired_token_no_refresh_token_fails(self):
        i = GitHubOAuthIdentityIntegration(_cfg())
        cache.save_token(
            i.integration_name,
            {"access_token": "old", "expires_at": time.time() - 10, "claims": {}},
        )
        ok, detail = i.check_auth()
        assert ok is False
        assert "--login" in detail

    def test_expired_token_refresh_rejected_clears_cache(self):
        i = GitHubOAuthIdentityIntegration(_cfg())
        cache.save_token(
            i.integration_name,
            {"access_token": "old", "refresh_token": "bad", "expires_at": time.time() - 10, "claims": {}},
        )
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.return_value = _mock_response({"error": "bad_refresh_token"}, status=400)
            ok, _ = i.check_auth()

        assert ok is False
        assert cache.load_token(i.integration_name) is None


class TestLogin:
    def test_successful_device_code_login(self, capsys):
        i = GitHubOAuthIdentityIntegration(_cfg())
        with patch("urllib.request.urlopen") as mock_urlopen, patch("time.sleep"):
            mock_urlopen.side_effect = [
                _mock_response(
                    {
                        "device_code": "devcode",
                        "user_code": "ABCD-1234",
                        "verification_uri": "https://github.com/login/device",
                        "interval": 1,
                        "expires_in": 30,
                    }
                ),  # device code request
                _mock_response({"error": "authorization_pending"}, status=400),  # first poll
                _mock_response({"access_token": "tok", "token_type": "bearer", "scope": "read:user"}),  # second poll
                _mock_response({"id": 42, "login": "octocat", "email": "octocat@example.com"}),  # GET /user
            ]
            ok, detail = i.login()

        assert ok is True
        assert "octocat" in detail
        cached = cache.load_token(i.integration_name)
        assert cached["access_token"] == "tok"
        assert cached["claims"] == {"email": "octocat@example.com", "preferred_username": "octocat", "sub": "42"}
        assert "expires_at" not in cached  # classic token: no expiry returned
        assert "ABCD-1234" in capsys.readouterr().out

    def test_expiring_token_login_persists_expiry_and_refresh(self):
        i = GitHubOAuthIdentityIntegration(_cfg())
        with patch("urllib.request.urlopen") as mock_urlopen, patch("time.sleep"):
            mock_urlopen.side_effect = [
                _mock_response(
                    {
                        "device_code": "devcode",
                        "user_code": "ABCD-1234",
                        "verification_uri": "https://github.com/login/device",
                        "interval": 1,
                        "expires_in": 30,
                    }
                ),
                _mock_response({"access_token": "tok", "expires_in": 28800, "refresh_token": "refresh-tok"}),
                _mock_response({"id": 1, "login": "octocat"}),
            ]
            ok, _ = i.login()

        assert ok is True
        cached = cache.load_token(i.integration_name)
        assert cached["refresh_token"] == "refresh-tok"
        assert cached["expires_at"] > time.time()

    def test_device_authorization_request_failure(self):
        i = GitHubOAuthIdentityIntegration(_cfg())
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.return_value = _mock_response({"error": "bad_client_id"}, status=400)
            ok, detail = i.login()
        assert ok is False
        assert "failed" in detail.lower()

    def test_slow_down_increases_interval_and_retries(self):
        i = GitHubOAuthIdentityIntegration(_cfg())
        with patch("urllib.request.urlopen") as mock_urlopen, patch("time.sleep"):
            mock_urlopen.side_effect = [
                _mock_response(
                    {
                        "device_code": "devcode",
                        "user_code": "ABCD-1234",
                        "verification_uri": "https://github.com/login/device",
                        "interval": 1,
                        "expires_in": 30,
                    }
                ),
                _mock_response({"error": "slow_down"}, status=400),
                _mock_response({"access_token": "tok"}),
                _mock_response({"login": "octocat"}),
            ]
            ok, _ = i.login()
        assert ok is True

    def test_access_denied_fails_immediately(self):
        i = GitHubOAuthIdentityIntegration(_cfg())
        with patch("urllib.request.urlopen") as mock_urlopen, patch("time.sleep"):
            mock_urlopen.side_effect = [
                _mock_response(
                    {
                        "device_code": "devcode",
                        "user_code": "ABCD-1234",
                        "verification_uri": "https://github.com/login/device",
                        "interval": 1,
                        "expires_in": 30,
                    }
                ),
                _mock_response({"error": "access_denied"}, status=400),
            ]
            ok, detail = i.login()
        assert ok is False
        assert "failed" in detail.lower()


class TestGetAccessToken:
    def test_returns_none_when_not_logged_in(self):
        i = GitHubOAuthIdentityIntegration(_cfg())
        assert i.get_access_token() is None

    def test_returns_cached_token_when_valid(self):
        i = GitHubOAuthIdentityIntegration(_cfg())
        cache.save_token(i.integration_name, {"access_token": "tok", "claims": {}})
        assert i.get_access_token() == "tok"


class TestGetIdentityClaims:
    def test_returns_none_when_not_logged_in(self):
        i = GitHubOAuthIdentityIntegration(_cfg())
        assert i.get_identity_claims() is None

    def test_returns_cached_claims(self):
        i = GitHubOAuthIdentityIntegration(_cfg())
        cache.save_token(i.integration_name, {"access_token": "tok", "claims": {"preferred_username": "octocat"}})
        assert i.get_identity_claims() == {"preferred_username": "octocat"}
