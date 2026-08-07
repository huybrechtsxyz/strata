#!/usr/bin/env python3
"""Unit tests for GoogleIdentityIntegration — gcloud CLI reuse before OIDC fallback (ADR-0067)."""

import base64
import json
from unittest.mock import MagicMock

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


def _resolver(gcloud_cli_integration=None):
    """Simulate the `IdentityController`-injected sibling resolver (see azure identity tests)."""
    return lambda capability: gcloud_cli_integration


class TestIssuer:
    def test_defaults_to_google_accounts(self):
        i = GoogleIdentityIntegration(_cfg())
        assert i._issuer == "https://accounts.google.com"


class TestCheckAuthReuse:
    def test_reuses_authenticated_gcloud_cli_session(self):
        i = GoogleIdentityIntegration(_cfg())
        gcloud_cli = MagicMock()
        gcloud_cli.get_identity_token.return_value = _id_token()
        i.set_sibling_resolver(_resolver(gcloud_cli))

        ok, detail = i.check_auth()

        assert ok is True
        assert "dev@example.com" in detail
        gcloud_cli.get_identity_token.assert_called_once_with(audience="abc123")

    def test_falls_back_to_oidc_when_gcloud_cli_not_configured(self):
        i = GoogleIdentityIntegration(_cfg())
        i.set_sibling_resolver(_resolver(None))

        ok, detail = i.check_auth()

        assert ok is False
        assert "--login" in detail

    def test_falls_back_when_gcloud_cli_not_authenticated(self):
        i = GoogleIdentityIntegration(_cfg())
        # The resolver only ever returns an *available* integration, so an unauthenticated
        # gcloud_cli is indistinguishable from "not configured" — resolver returns None.
        i.set_sibling_resolver(_resolver(None))

        ok, _ = i.check_auth()

        assert ok is False


class TestLoginReuse:
    def test_reuse_means_no_separate_sign_in(self, capsys):
        i = GoogleIdentityIntegration(_cfg())
        gcloud_cli = MagicMock()
        gcloud_cli.get_identity_token.return_value = _id_token()
        i.set_sibling_resolver(_resolver(gcloud_cli))

        ok, detail = i.login()

        assert ok is True
        assert "Reused existing gcloud CLI login" in detail
        assert capsys.readouterr().out == ""


class TestGetAccessTokenReuse:
    def test_returns_reused_identity_token(self):
        i = GoogleIdentityIntegration(_cfg())
        gcloud_cli = MagicMock()
        gcloud_cli.get_identity_token.return_value = _id_token()
        i.set_sibling_resolver(_resolver(gcloud_cli))

        token = i.get_access_token()

        assert token == _id_token()

    def test_falls_back_to_cached_oidc_token_when_no_reuse(self):
        i = GoogleIdentityIntegration(_cfg())
        i.set_sibling_resolver(_resolver(None))
        cache.save_token(i.integration_name, {"access_token": "oidc-token", "expires_at": 9999999999, "claims": {}})

        assert i.get_access_token() == "oidc-token"


class TestGetIdentityClaimsReuse:
    def test_returns_claims_decoded_from_reused_id_token(self):
        i = GoogleIdentityIntegration(_cfg())
        gcloud_cli = MagicMock()
        gcloud_cli.get_identity_token.return_value = _id_token(email="dev@example.com", sub="u123")
        i.set_sibling_resolver(_resolver(gcloud_cli))

        claims = i.get_identity_claims()

        assert claims == {"email": "dev@example.com", "preferred_username": "dev@example.com", "sub": "u123"}

    def test_falls_back_to_cached_claims_when_no_reuse(self):
        i = GoogleIdentityIntegration(_cfg())
        i.set_sibling_resolver(_resolver(None))
        cache.save_token(
            i.integration_name,
            {"access_token": "tok", "expires_at": 9999999999, "claims": {"sub": "u1"}},
        )

        assert i.get_identity_claims() == {"sub": "u1"}
