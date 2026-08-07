#!/usr/bin/env python3
"""Unit tests for GoogleIdentityIntegration — gcloud CLI reuse before OIDC fallback (ADR-0067)."""

import base64
import json
from unittest.mock import MagicMock, patch

import pytest

from strata.integrations.base_integration import BaseIntegration
from strata.integrations.identity.google_identity_integration import GoogleIdentityIntegration
from strata.models.auth_models import AuthenticationModel, OAuth2AuthenticationModel
from strata.models.integration_model import IntegrationModel
from strata.utils import identity_token_cache as cache


def _id_token(email: str = "dev@example.com", sub: str = "u123") -> str:
    header = base64.urlsafe_b64encode(json.dumps({"alg": "RS256"}).encode()).decode().rstrip("=")
    body = base64.urlsafe_b64encode(json.dumps({"email": email, "sub": sub}).encode()).decode().rstrip("=")
    return f"{header}.{body}.fakesig"


def _cfg(name="strata-control-plane") -> IntegrationModel:
    return IntegrationModel(
        name=name,
        type="google",
        capabilities={"identity"},
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
    yield
    BaseIntegration._instances.clear()


def _mock_service(gcloud_cli_integration=None):
    if gcloud_cli_integration is not None:
        gcloud_cli_integration.ensure_available.return_value = (True, "")
    svc = MagicMock()
    svc.is_initialized.return_value = True
    svc.get_integrations_with_capability.return_value = ["gcloud"] if gcloud_cli_integration else []
    svc.get_integration.return_value = gcloud_cli_integration
    return svc


class TestIssuer:
    def test_defaults_to_google_accounts(self):
        i = GoogleIdentityIntegration(_cfg())
        assert i._issuer == "https://accounts.google.com"


class TestCheckAuthReuse:
    def test_reuses_authenticated_gcloud_cli_session(self):
        i = GoogleIdentityIntegration(_cfg())
        gcloud_cli = MagicMock()
        gcloud_cli.get_identity_token.return_value = _id_token()
        svc = _mock_service(gcloud_cli_integration=gcloud_cli)

        with patch("strata.services.integration_service.IntegrationService.get_instance", return_value=svc):
            ok, detail = i.check_auth()

        assert ok is True
        assert "dev@example.com" in detail
        gcloud_cli.get_identity_token.assert_called_once_with(audience="abc123")

    def test_falls_back_to_oidc_when_gcloud_cli_not_configured(self):
        i = GoogleIdentityIntegration(_cfg())
        svc = _mock_service(gcloud_cli_integration=None)

        with patch("strata.services.integration_service.IntegrationService.get_instance", return_value=svc):
            ok, detail = i.check_auth()

        assert ok is False
        assert "--login" in detail

    def test_falls_back_when_gcloud_cli_not_authenticated(self):
        i = GoogleIdentityIntegration(_cfg())
        gcloud_cli = MagicMock()
        gcloud_cli.ensure_available.return_value = (False, "not logged in")
        svc = MagicMock()
        svc.is_initialized.return_value = True
        svc.get_integrations_with_capability.return_value = ["gcloud"]
        svc.get_integration.return_value = gcloud_cli

        with patch("strata.services.integration_service.IntegrationService.get_instance", return_value=svc):
            ok, _ = i.check_auth()

        assert ok is False
        gcloud_cli.get_identity_token.assert_not_called()


class TestLoginReuse:
    def test_reuse_means_no_separate_sign_in(self, capsys):
        i = GoogleIdentityIntegration(_cfg())
        gcloud_cli = MagicMock()
        gcloud_cli.get_identity_token.return_value = _id_token()
        svc = _mock_service(gcloud_cli_integration=gcloud_cli)

        with patch("strata.services.integration_service.IntegrationService.get_instance", return_value=svc):
            ok, detail = i.login()

        assert ok is True
        assert "Reused existing gcloud CLI login" in detail
        assert capsys.readouterr().out == ""


class TestGetAccessTokenReuse:
    def test_returns_reused_identity_token(self):
        i = GoogleIdentityIntegration(_cfg())
        gcloud_cli = MagicMock()
        gcloud_cli.get_identity_token.return_value = _id_token()
        svc = _mock_service(gcloud_cli_integration=gcloud_cli)

        with patch("strata.services.integration_service.IntegrationService.get_instance", return_value=svc):
            token = i.get_access_token()

        assert token == _id_token()

    def test_falls_back_to_cached_oidc_token_when_no_reuse(self):
        i = GoogleIdentityIntegration(_cfg())
        svc = _mock_service(gcloud_cli_integration=None)
        cache.save_token(i.integration_name, {"access_token": "oidc-token", "expires_at": 9999999999, "claims": {}})

        with patch("strata.services.integration_service.IntegrationService.get_instance", return_value=svc):
            assert i.get_access_token() == "oidc-token"


class TestGetIdentityClaimsReuse:
    def test_returns_claims_decoded_from_reused_id_token(self):
        i = GoogleIdentityIntegration(_cfg())
        gcloud_cli = MagicMock()
        gcloud_cli.get_identity_token.return_value = _id_token(email="dev@example.com", sub="u123")
        svc = _mock_service(gcloud_cli_integration=gcloud_cli)

        with patch("strata.services.integration_service.IntegrationService.get_instance", return_value=svc):
            claims = i.get_identity_claims()

        assert claims == {"email": "dev@example.com", "preferred_username": "dev@example.com", "sub": "u123"}

    def test_falls_back_to_cached_claims_when_no_reuse(self):
        i = GoogleIdentityIntegration(_cfg())
        svc = _mock_service(gcloud_cli_integration=None)
        cache.save_token(
            i.integration_name,
            {"access_token": "tok", "expires_at": 9999999999, "claims": {"sub": "u1"}},
        )

        with patch("strata.services.integration_service.IntegrationService.get_instance", return_value=svc):
            assert i.get_identity_claims() == {"sub": "u1"}
