#!/usr/bin/env python3
"""Unit tests for AwsIdentityIntegration — IAM Identity Center device-code login (ADR-0067)."""

import json
import time
from unittest.mock import MagicMock, patch

import pytest

from strata.integrations.base_integration import BaseIntegration
from strata.integrations.capabilities import IIdentityProvider
from strata.integrations.identity.aws_identity_integration import AwsIdentityIntegration
from strata.models.integration_model import IntegrationEndpointsSpecModel, IntegrationModel
from strata.utils import identity_token_cache as cache

_REGISTERED_CLIENT = {
    "client_id": "reg-client-id",
    "client_secret": "reg-client-secret",
    "client_secret_expires_at": time.time() + 3600,
}


def _cfg(name="strata-control-plane") -> IntegrationModel:
    return IntegrationModel(
        name=name,
        type="aws_identity_center",
        capabilities={"identity"},
        endpoints=IntegrationEndpointsSpecModel(address="https://my-sso-portal.awsapps.com/start"),
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
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    yield
    BaseIntegration._instances.clear()


class TestCapability:
    def test_declares_identity_provider_capability(self):
        assert IIdentityProvider in AwsIdentityIntegration.CAPABILITIES


class TestConfig:
    def test_start_url_from_endpoints(self):
        i = AwsIdentityIntegration(_cfg())
        assert i._start_url == "https://my-sso-portal.awsapps.com/start"

    def test_missing_endpoints_raises(self):
        cfg = IntegrationModel(name="x", type="aws_identity_center")
        i = AwsIdentityIntegration(cfg)
        with pytest.raises(ValueError):
            _ = i._start_url

    def test_region_from_env_var(self):
        i = AwsIdentityIntegration(_cfg())
        assert i._region == "us-east-1"
        assert i._endpoint == "https://oidc.us-east-1.amazonaws.com"

    def test_region_falls_back_to_aws_cli_integration(self, monkeypatch):
        monkeypatch.delenv("AWS_DEFAULT_REGION", raising=False)
        aws_cli = MagicMock()
        aws_cli.get_region.return_value = "eu-west-1"
        svc = MagicMock()
        svc.is_initialized.return_value = True
        svc.get_integrations_with_capability.return_value = ["aws"]
        svc.get_integration.return_value = aws_cli

        i = AwsIdentityIntegration(_cfg())
        with patch("strata.services.integration_service.IntegrationService.get_instance", return_value=svc):
            assert i._region == "eu-west-1"

    def test_no_region_available_raises(self, monkeypatch):
        monkeypatch.delenv("AWS_DEFAULT_REGION", raising=False)
        svc = MagicMock()
        svc.is_initialized.return_value = True
        svc.get_integrations_with_capability.return_value = []

        i = AwsIdentityIntegration(_cfg())
        with patch("strata.services.integration_service.IntegrationService.get_instance", return_value=svc):
            with pytest.raises(ValueError):
                _ = i._region


class TestCheckAuthNoSession:
    def test_no_cache_means_not_logged_in(self):
        i = AwsIdentityIntegration(_cfg())
        ok, detail = i.check_auth()
        assert ok is False
        assert "--login" in detail


class TestCheckAuthValidSession:
    def test_valid_unexpired_token_passes(self):
        i = AwsIdentityIntegration(_cfg())
        cache.save_token(
            i.integration_name,
            {
                "access_token": "tok",
                "refresh_token": "refresh",
                "expires_at": time.time() + 3600,
                "claims": {"email": "dev@example.com"},
                "registered_client": _REGISTERED_CLIENT,
            },
        )
        ok, detail = i.check_auth()
        assert ok is True
        assert "dev@example.com" in detail


class TestCheckAuthRefresh:
    def test_expired_token_refreshes_silently(self):
        i = AwsIdentityIntegration(_cfg())
        cache.save_token(
            i.integration_name,
            {
                "access_token": "old",
                "refresh_token": "refresh-me",
                "expires_at": time.time() - 10,
                "claims": {"email": "dev@example.com"},
                "registered_client": _REGISTERED_CLIENT,
            },
        )
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.return_value = _mock_response({"accessToken": "new", "expiresIn": 3600})
            ok, detail = i.check_auth()

        assert ok is True
        assert "refreshed" in detail
        assert cache.load_token(i.integration_name)["access_token"] == "new"

    def test_expired_token_no_refresh_token_fails(self):
        i = AwsIdentityIntegration(_cfg())
        cache.save_token(
            i.integration_name,
            {"access_token": "old", "expires_at": time.time() - 10, "claims": {}},
        )
        ok, detail = i.check_auth()
        assert ok is False
        assert "--login" in detail

    def test_expired_token_refresh_rejected_clears_cache(self):
        i = AwsIdentityIntegration(_cfg())
        cache.save_token(
            i.integration_name,
            {
                "access_token": "old",
                "refresh_token": "bad",
                "expires_at": time.time() - 10,
                "claims": {},
                "registered_client": _REGISTERED_CLIENT,
            },
        )
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.return_value = _mock_response({"error": "invalid_grant"}, status=400)
            ok, _ = i.check_auth()

        assert ok is False
        assert cache.load_token(i.integration_name) is None


class TestLogin:
    def test_successful_device_code_login_registers_client_once(self, capsys):
        i = AwsIdentityIntegration(_cfg())
        with patch("urllib.request.urlopen") as mock_urlopen, patch("time.sleep"):
            mock_urlopen.side_effect = [
                _mock_response(
                    {"clientId": "reg-id", "clientSecret": "reg-secret", "clientSecretExpiresAt": time.time() + 3600}
                ),  # RegisterClient
                _mock_response(
                    {
                        "deviceCode": "devcode",
                        "userCode": "ABCD-1234",
                        "verificationUri": "https://device.sso.amazonaws.com/",
                        "interval": 1,
                        "expiresIn": 30,
                    }
                ),  # StartDeviceAuthorization
                _mock_response({"error": "AuthorizationPendingException"}, status=400),  # first poll
                _mock_response({"accessToken": "tok", "expiresIn": 3600}),  # second poll — success
                _mock_response({"email": "dev@example.com", "sub": "u123"}),  # userinfo
            ]
            ok, detail = i.login()

        assert ok is True
        assert "dev@example.com" in detail
        cached = cache.load_token(i.integration_name)
        assert cached["access_token"] == "tok"
        assert cached["registered_client"]["client_id"] == "reg-id"
        assert "ABCD-1234" in capsys.readouterr().out

    def test_reuses_cached_client_registration(self):
        i = AwsIdentityIntegration(_cfg())
        cache.save_token(i.integration_name, {"registered_client": _REGISTERED_CLIENT})

        with patch("urllib.request.urlopen") as mock_urlopen, patch("time.sleep"):
            mock_urlopen.side_effect = [
                _mock_response(
                    {
                        "deviceCode": "devcode",
                        "userCode": "ABCD-1234",
                        "verificationUri": "https://device.sso.amazonaws.com/",
                        "interval": 1,
                        "expiresIn": 30,
                    }
                ),  # StartDeviceAuthorization — no RegisterClient call this time
                _mock_response({"accessToken": "tok", "expiresIn": 3600}),
                _mock_response({}),  # userinfo
            ]
            ok, _ = i.login()

        assert ok is True
        assert mock_urlopen.call_count == 3  # register_client skipped

    def test_device_authorization_request_failure(self):
        i = AwsIdentityIntegration(_cfg())
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.side_effect = [
                _mock_response(
                    {"clientId": "reg-id", "clientSecret": "reg-secret", "clientSecretExpiresAt": time.time() + 3600}
                ),
                _mock_response({"error": "InvalidRequestException"}, status=400),
            ]
            ok, detail = i.login()
        assert ok is False
        assert "failed" in detail.lower()

    def test_register_client_failure_is_reported(self):
        i = AwsIdentityIntegration(_cfg())
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.return_value = _mock_response({"error": "InvalidClientMetadataException"}, status=400)
            ok, detail = i.login()
        assert ok is False
        assert "register" in detail.lower()


class TestGetAccessToken:
    def test_returns_none_when_not_logged_in(self):
        i = AwsIdentityIntegration(_cfg())
        assert i.get_access_token() is None

    def test_returns_cached_token_when_valid(self):
        i = AwsIdentityIntegration(_cfg())
        cache.save_token(
            i.integration_name,
            {"access_token": "tok", "expires_at": time.time() + 3600, "claims": {}},
        )
        assert i.get_access_token() == "tok"


class TestGetIdentityClaims:
    def test_returns_none_when_not_logged_in(self):
        i = AwsIdentityIntegration(_cfg())
        assert i.get_identity_claims() is None

    def test_returns_cached_claims(self):
        i = AwsIdentityIntegration(_cfg())
        cache.save_token(
            i.integration_name,
            {"access_token": "tok", "expires_at": time.time() + 3600, "claims": {"sub": "u1"}},
        )
        assert i.get_identity_claims() == {"sub": "u1"}
