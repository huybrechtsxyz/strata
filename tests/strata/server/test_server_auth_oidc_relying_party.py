"""Tests for the OIDC relying party (ADR-0067 Step 7) — discovery, PKCE authorize URL,
code exchange, and id_token verification (real RS256 signing/verification via joserfc,
authlib's JOSE backend — no fakes for the cryptography itself).
"""

from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional
from unittest.mock import patch

import pytest
from joserfc import jwt as joserfc_jwt
from joserfc.jwk import RSAKey

from strata.server.auth.oidc_relying_party import OidcRelyingParty, OidcRelyingPartyConfig

_ISSUER = "https://idp.example.test"
_CLIENT_ID = "strata-control-plane"
_REDIRECT_BASE = "https://control-plane.example.test"


class _FakeResponse:
    def __init__(self, payload: Dict[str, Any], status: int = 200) -> None:
        self._body = json.dumps(payload).encode("utf-8")
        self.status = status

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *exc: Any) -> None:
        return None


@pytest.fixture
def rsa_key() -> RSAKey:
    return RSAKey.generate_key(2048, parameters={"kid": "test-key-1"}, private=True)


@pytest.fixture
def discovery_doc() -> Dict[str, str]:
    return {
        "issuer": _ISSUER,
        "authorization_endpoint": f"{_ISSUER}/authorize",
        "token_endpoint": f"{_ISSUER}/token",
        "userinfo_endpoint": f"{_ISSUER}/userinfo",
        "jwks_uri": f"{_ISSUER}/.well-known/jwks.json",
    }


def _sign_id_token(rsa_key: RSAKey, **claim_overrides: Any) -> str:
    now = int(time.time())
    claims = {
        "iss": _ISSUER,
        "aud": _CLIENT_ID,
        "sub": "user-123",
        "email": "user@example.test",
        "exp": now + 300,
        "iat": now,
        "nonce": "test-nonce",
    }
    claims.update(claim_overrides)
    return joserfc_jwt.encode({"alg": "RS256", "kid": rsa_key.kid}, claims, rsa_key)


def _make_urlopen_mock(
    discovery_doc: Dict[str, Any],
    rsa_key: RSAKey,
    token_response: Optional[Dict[str, Any]] = None,
    userinfo_response: Optional[Dict[str, Any]] = None,
):
    """Route GET/POST calls to the right fake response by URL, mirroring the real IdP's routes."""

    def _urlopen(req, timeout=None):  # noqa: ANN001 - matches urllib.request.urlopen's call shape
        url = req.full_url if hasattr(req, "full_url") else req
        if url == f"{_ISSUER}/.well-known/openid-configuration":
            return _FakeResponse(discovery_doc)
        if url == discovery_doc["jwks_uri"]:
            return _FakeResponse({"keys": [rsa_key.as_dict(private=False)]})
        if url == discovery_doc["token_endpoint"]:
            return _FakeResponse(token_response if token_response is not None else {"error": "not configured"})
        if url == discovery_doc["userinfo_endpoint"]:
            return _FakeResponse(userinfo_response if userinfo_response is not None else {})
        raise AssertionError(f"Unexpected URL requested in test: {url}")

    return _urlopen


class TestDiscover:
    def test_fetches_and_caches_discovery_document(self, discovery_doc: Dict[str, Any], rsa_key: RSAKey) -> None:
        rp = OidcRelyingParty(
            OidcRelyingPartyConfig(issuer=_ISSUER, client_id=_CLIENT_ID, redirect_base=_REDIRECT_BASE)
        )
        with patch("urllib.request.urlopen", side_effect=_make_urlopen_mock(discovery_doc, rsa_key)) as mock_urlopen:
            first = rp.discover()
            second = rp.discover()
        assert first == discovery_doc
        assert second == discovery_doc
        assert mock_urlopen.call_count == 1  # cached on the second call


class TestBuildAuthorizationUrl:
    def test_includes_pkce_state_and_nonce(self, discovery_doc: Dict[str, Any], rsa_key: RSAKey) -> None:
        rp = OidcRelyingParty(
            OidcRelyingPartyConfig(issuer=_ISSUER, client_id=_CLIENT_ID, redirect_base=_REDIRECT_BASE)
        )
        with patch("urllib.request.urlopen", side_effect=_make_urlopen_mock(discovery_doc, rsa_key)):
            url = rp.build_authorization_url("state-abc", "challenge-xyz", "nonce-123")

        assert url.startswith(f"{_ISSUER}/authorize?")
        assert "state=state-abc" in url
        assert "code_challenge=challenge-xyz" in url
        assert "code_challenge_method=S256" in url
        assert "nonce=nonce-123" in url
        assert f"redirect_uri={urllib.parse.quote(rp.redirect_uri, safe='')}" in url


class TestExchangeCode:
    def test_successful_exchange_returns_token_response(self, discovery_doc: Dict[str, Any], rsa_key: RSAKey) -> None:
        rp = OidcRelyingParty(
            OidcRelyingPartyConfig(issuer=_ISSUER, client_id=_CLIENT_ID, redirect_base=_REDIRECT_BASE)
        )
        id_token = _sign_id_token(rsa_key)
        token_response = {"access_token": "at-123", "id_token": id_token, "expires_in": 3600}
        with patch(
            "urllib.request.urlopen",
            side_effect=_make_urlopen_mock(discovery_doc, rsa_key, token_response=token_response),
        ):
            ok, data = rp.exchange_code("auth-code", "verifier-value")

        assert ok is True
        assert data["access_token"] == "at-123"

    def test_failed_exchange_returns_error_payload(self, discovery_doc: Dict[str, Any], rsa_key: RSAKey) -> None:
        rp = OidcRelyingParty(
            OidcRelyingPartyConfig(issuer=_ISSUER, client_id=_CLIENT_ID, redirect_base=_REDIRECT_BASE)
        )
        with patch(
            "urllib.request.urlopen",
            side_effect=_make_urlopen_mock(discovery_doc, rsa_key, token_response={"error": "invalid_grant"}),
        ):
            ok, data = rp.exchange_code("bad-code", "verifier-value")

        assert ok is False
        assert data["error"] == "invalid_grant"


class TestVerifyIdToken:
    def test_valid_token_returns_claims(self, discovery_doc: Dict[str, Any], rsa_key: RSAKey) -> None:
        rp = OidcRelyingParty(
            OidcRelyingPartyConfig(issuer=_ISSUER, client_id=_CLIENT_ID, redirect_base=_REDIRECT_BASE)
        )
        id_token = _sign_id_token(rsa_key)
        with patch("urllib.request.urlopen", side_effect=_make_urlopen_mock(discovery_doc, rsa_key)):
            claims = rp.verify_id_token(id_token, nonce="test-nonce")

        assert claims["sub"] == "user-123"
        assert claims["email"] == "user@example.test"

    def test_wrong_nonce_is_rejected(self, discovery_doc: Dict[str, Any], rsa_key: RSAKey) -> None:
        rp = OidcRelyingParty(
            OidcRelyingPartyConfig(issuer=_ISSUER, client_id=_CLIENT_ID, redirect_base=_REDIRECT_BASE)
        )
        id_token = _sign_id_token(rsa_key)
        with patch("urllib.request.urlopen", side_effect=_make_urlopen_mock(discovery_doc, rsa_key)):
            with pytest.raises(ValueError, match="nonce"):
                rp.verify_id_token(id_token, nonce="a-different-nonce")

    def test_wrong_audience_is_rejected(self, discovery_doc: Dict[str, Any], rsa_key: RSAKey) -> None:
        rp = OidcRelyingParty(
            OidcRelyingPartyConfig(issuer=_ISSUER, client_id=_CLIENT_ID, redirect_base=_REDIRECT_BASE)
        )
        id_token = _sign_id_token(rsa_key, aud="someone-elses-client-id")
        with patch("urllib.request.urlopen", side_effect=_make_urlopen_mock(discovery_doc, rsa_key)):
            with pytest.raises(ValueError):
                rp.verify_id_token(id_token, nonce="test-nonce")

    def test_wrong_issuer_is_rejected(self, discovery_doc: Dict[str, Any], rsa_key: RSAKey) -> None:
        rp = OidcRelyingParty(
            OidcRelyingPartyConfig(issuer=_ISSUER, client_id=_CLIENT_ID, redirect_base=_REDIRECT_BASE)
        )
        id_token = _sign_id_token(rsa_key, iss="https://not-the-real-idp.example.test")
        with patch("urllib.request.urlopen", side_effect=_make_urlopen_mock(discovery_doc, rsa_key)):
            with pytest.raises(ValueError):
                rp.verify_id_token(id_token, nonce="test-nonce")

    def test_expired_token_is_rejected(self, discovery_doc: Dict[str, Any], rsa_key: RSAKey) -> None:
        rp = OidcRelyingParty(
            OidcRelyingPartyConfig(issuer=_ISSUER, client_id=_CLIENT_ID, redirect_base=_REDIRECT_BASE)
        )
        id_token = _sign_id_token(rsa_key, exp=int(time.time()) - 120)
        with patch("urllib.request.urlopen", side_effect=_make_urlopen_mock(discovery_doc, rsa_key)):
            with pytest.raises(ValueError):
                rp.verify_id_token(id_token, nonce="test-nonce")

    def test_tampered_signature_is_rejected(self, discovery_doc: Dict[str, Any], rsa_key: RSAKey) -> None:
        rp = OidcRelyingParty(
            OidcRelyingPartyConfig(issuer=_ISSUER, client_id=_CLIENT_ID, redirect_base=_REDIRECT_BASE)
        )
        id_token = _sign_id_token(rsa_key)
        tampered = id_token[:-4] + ("A" if id_token[-4] != "A" else "B") + id_token[-3:]
        with patch("urllib.request.urlopen", side_effect=_make_urlopen_mock(discovery_doc, rsa_key)):
            with pytest.raises(ValueError):
                rp.verify_id_token(tampered, nonce="test-nonce")

    def test_signed_by_a_different_key_is_rejected(self, discovery_doc: Dict[str, Any], rsa_key: RSAKey) -> None:
        rp = OidcRelyingParty(
            OidcRelyingPartyConfig(issuer=_ISSUER, client_id=_CLIENT_ID, redirect_base=_REDIRECT_BASE)
        )
        other_key = RSAKey.generate_key(2048, parameters={"kid": "test-key-1"}, private=True)
        id_token = _sign_id_token(other_key)  # same kid, different actual key material
        with patch("urllib.request.urlopen", side_effect=_make_urlopen_mock(discovery_doc, rsa_key)):
            with pytest.raises(ValueError):
                rp.verify_id_token(id_token, nonce="test-nonce")


class TestClientCredentialsToken:
    def test_requires_client_secret(self, discovery_doc: Dict[str, Any], rsa_key: RSAKey) -> None:
        rp = OidcRelyingParty(
            OidcRelyingPartyConfig(issuer=_ISSUER, client_id=_CLIENT_ID, redirect_base=_REDIRECT_BASE)
        )
        ok, data = rp.client_credentials_token()
        assert ok is False
        assert "client_secret" in data["error"]

    def test_successful_request_with_client_secret(self, discovery_doc: Dict[str, Any], rsa_key: RSAKey) -> None:
        rp = OidcRelyingParty(
            OidcRelyingPartyConfig(
                issuer=_ISSUER, client_id=_CLIENT_ID, redirect_base=_REDIRECT_BASE, client_secret="shh"
            )
        )
        token_response = {"access_token": "m2m-token", "expires_in": 3600}
        with patch(
            "urllib.request.urlopen",
            side_effect=_make_urlopen_mock(discovery_doc, rsa_key, token_response=token_response),
        ):
            ok, data = rp.client_credentials_token()
        assert ok is True
        assert data["access_token"] == "m2m-token"
