"""Tests for the M2M verifier (ADR-0067 Step 10) — trusted-issuer selection and
access-token verification (real RS256 signing/verification via joserfc, no fakes
for the cryptography itself), mirroring `test_server_auth_oidc_relying_party.py`'s
`TestVerifyIdToken` pattern.
"""

from __future__ import annotations

import json
import time
from typing import Any, Dict
from unittest.mock import patch

import pytest
from joserfc import jwt as joserfc_jwt
from joserfc.jwk import RSAKey

from strata.server.auth.m2m_verifier import M2mVerifier, TrustedIssuer

_GITHUB_ISSUER = "https://token.actions.githubusercontent.com"
_GITHUB_AUDIENCE = "https://control-plane.example.test"
_ADO_ISSUER = "https://vstoken.dev.azure.com/some-org-id"
_ADO_AUDIENCE = "api://strata-control-plane"


class _FakeResponse:
    def __init__(self, payload: Dict[str, Any]) -> None:
        self._body = json.dumps(payload).encode("utf-8")
        self.status = 200

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *exc: Any) -> None:
        return None


def _discovery_doc(issuer: str) -> Dict[str, str]:
    return {
        "issuer": issuer,
        "jwks_uri": f"{issuer}/.well-known/jwks.json",
    }


def _sign_token(rsa_key: RSAKey, issuer: str, audience: str, **overrides: Any) -> str:
    now = int(time.time())
    claims = {
        "iss": issuer,
        "aud": audience,
        "sub": "repo:acme/widgets:ref:refs/heads/main",
        "exp": now + 300,
        "iat": now,
    }
    claims.update(overrides)
    return joserfc_jwt.encode({"alg": "RS256", "kid": rsa_key.kid}, claims, rsa_key)


def _make_urlopen_mock(*issuer_setups: Dict[str, Any]):
    """Route GET calls to the right fake discovery/JWKS response by URL.

    Each entry in *issuer_setups* is ``{"discovery": ..., "rsa_key": ...}``.
    """

    def _urlopen(req: Any, timeout: Any = None) -> _FakeResponse:
        url = req.full_url if hasattr(req, "full_url") else req
        for setup in issuer_setups:
            discovery = setup["discovery"]
            if url == f"{discovery['issuer']}/.well-known/openid-configuration":
                return _FakeResponse(discovery)
            if url == discovery["jwks_uri"]:
                return _FakeResponse({"keys": [setup["rsa_key"].as_dict(private=False)]})
        raise AssertionError(f"Unexpected URL requested in test: {url}")

    return _urlopen


@pytest.fixture
def github_rsa_key() -> RSAKey:
    return RSAKey.generate_key(2048, parameters={"kid": "github-key-1"}, private=True)


@pytest.fixture
def ado_rsa_key() -> RSAKey:
    return RSAKey.generate_key(2048, parameters={"kid": "ado-key-1"}, private=True)


@pytest.fixture
def github_issuer() -> TrustedIssuer:
    return TrustedIssuer(name="github-actions", issuer=_GITHUB_ISSUER, audience=_GITHUB_AUDIENCE)


@pytest.fixture
def ado_issuer() -> TrustedIssuer:
    return TrustedIssuer(name="azure-devops", issuer=_ADO_ISSUER, audience=_ADO_AUDIENCE, subject_claim="sub")


class TestVerify:
    def test_valid_token_returns_claims(self, github_rsa_key: RSAKey, github_issuer: TrustedIssuer) -> None:
        verifier = M2mVerifier([github_issuer])
        token = _sign_token(github_rsa_key, _GITHUB_ISSUER, _GITHUB_AUDIENCE)
        discovery = _discovery_doc(_GITHUB_ISSUER)

        with patch(
            "urllib.request.urlopen",
            side_effect=_make_urlopen_mock({"discovery": discovery, "rsa_key": github_rsa_key}),
        ):
            claims = verifier.verify(token)

        assert claims["sub"] == "repo:acme/widgets:ref:refs/heads/main"
        assert claims["_trusted_issuer_name"] == "github-actions"

    def test_subject_defaults_to_sub_claim(self, github_rsa_key: RSAKey, github_issuer: TrustedIssuer) -> None:
        """ADR-0067 Step 9 — RBAC reads `_subject`, never a raw claim name per issuer."""
        verifier = M2mVerifier([github_issuer])
        token = _sign_token(github_rsa_key, _GITHUB_ISSUER, _GITHUB_AUDIENCE)
        discovery = _discovery_doc(_GITHUB_ISSUER)

        with patch(
            "urllib.request.urlopen",
            side_effect=_make_urlopen_mock({"discovery": discovery, "rsa_key": github_rsa_key}),
        ):
            claims = verifier.verify(token)

        assert claims["_subject"] == claims["sub"]

    def test_subject_uses_configured_subject_claim(self, github_rsa_key: RSAKey) -> None:
        issuer = TrustedIssuer(
            name="github-actions", issuer=_GITHUB_ISSUER, audience=_GITHUB_AUDIENCE, subject_claim="repository"
        )
        verifier = M2mVerifier([issuer])
        token = _sign_token(github_rsa_key, _GITHUB_ISSUER, _GITHUB_AUDIENCE, repository="acme/widgets")
        discovery = _discovery_doc(_GITHUB_ISSUER)

        with patch(
            "urllib.request.urlopen",
            side_effect=_make_urlopen_mock({"discovery": discovery, "rsa_key": github_rsa_key}),
        ):
            claims = verifier.verify(token)

        assert claims["_subject"] == "acme/widgets"

    def test_selects_the_right_issuer_among_several(
        self, github_rsa_key: RSAKey, ado_rsa_key: RSAKey, github_issuer: TrustedIssuer, ado_issuer: TrustedIssuer
    ) -> None:
        verifier = M2mVerifier([github_issuer, ado_issuer])
        token = _sign_token(ado_rsa_key, _ADO_ISSUER, _ADO_AUDIENCE)
        github_discovery = _discovery_doc(_GITHUB_ISSUER)
        ado_discovery = _discovery_doc(_ADO_ISSUER)

        with patch(
            "urllib.request.urlopen",
            side_effect=_make_urlopen_mock(
                {"discovery": github_discovery, "rsa_key": github_rsa_key},
                {"discovery": ado_discovery, "rsa_key": ado_rsa_key},
            ),
        ):
            claims = verifier.verify(token)

        assert claims["_trusted_issuer_name"] == "azure-devops"

    def test_untrusted_issuer_is_rejected(self, github_issuer: TrustedIssuer) -> None:
        verifier = M2mVerifier([github_issuer])
        other_key = RSAKey.generate_key(2048, parameters={"kid": "k1"}, private=True)
        token = _sign_token(other_key, "https://not-configured.example.test", "some-audience")

        with pytest.raises(ValueError, match="not a configured trusted issuer"):
            verifier.verify(token)

    def test_wrong_audience_is_rejected(self, github_rsa_key: RSAKey, github_issuer: TrustedIssuer) -> None:
        verifier = M2mVerifier([github_issuer])
        token = _sign_token(github_rsa_key, _GITHUB_ISSUER, "someone-elses-audience")
        discovery = _discovery_doc(_GITHUB_ISSUER)

        with patch(
            "urllib.request.urlopen",
            side_effect=_make_urlopen_mock({"discovery": discovery, "rsa_key": github_rsa_key}),
        ):
            with pytest.raises(ValueError):
                verifier.verify(token)

    def test_expired_token_is_rejected(self, github_rsa_key: RSAKey, github_issuer: TrustedIssuer) -> None:
        verifier = M2mVerifier([github_issuer])
        token = _sign_token(github_rsa_key, _GITHUB_ISSUER, _GITHUB_AUDIENCE, exp=int(time.time()) - 120)
        discovery = _discovery_doc(_GITHUB_ISSUER)

        with patch(
            "urllib.request.urlopen",
            side_effect=_make_urlopen_mock({"discovery": discovery, "rsa_key": github_rsa_key}),
        ):
            with pytest.raises(ValueError):
                verifier.verify(token)

    def test_tampered_signature_is_rejected(self, github_rsa_key: RSAKey, github_issuer: TrustedIssuer) -> None:
        verifier = M2mVerifier([github_issuer])
        token = _sign_token(github_rsa_key, _GITHUB_ISSUER, _GITHUB_AUDIENCE)
        tampered = token[:-4] + ("A" if token[-4] != "A" else "B") + token[-3:]
        discovery = _discovery_doc(_GITHUB_ISSUER)

        with patch(
            "urllib.request.urlopen",
            side_effect=_make_urlopen_mock({"discovery": discovery, "rsa_key": github_rsa_key}),
        ):
            with pytest.raises(ValueError):
                verifier.verify(tampered)

    def test_signed_by_a_different_key_is_rejected(self, github_rsa_key: RSAKey, github_issuer: TrustedIssuer) -> None:
        verifier = M2mVerifier([github_issuer])
        other_key = RSAKey.generate_key(2048, parameters={"kid": "github-key-1"}, private=True)  # same kid
        token = _sign_token(other_key, _GITHUB_ISSUER, _GITHUB_AUDIENCE)
        discovery = _discovery_doc(_GITHUB_ISSUER)

        with patch(
            "urllib.request.urlopen",
            side_effect=_make_urlopen_mock({"discovery": discovery, "rsa_key": github_rsa_key}),
        ):
            with pytest.raises(ValueError):
                verifier.verify(token)

    def test_malformed_token_is_rejected(self, github_issuer: TrustedIssuer) -> None:
        verifier = M2mVerifier([github_issuer])
        with pytest.raises(ValueError, match="malformed"):
            verifier.verify("not-a-jwt")

    def test_discovery_and_jwks_are_cached_per_issuer(
        self, github_rsa_key: RSAKey, github_issuer: TrustedIssuer
    ) -> None:
        verifier = M2mVerifier([github_issuer])
        token = _sign_token(github_rsa_key, _GITHUB_ISSUER, _GITHUB_AUDIENCE)
        discovery = _discovery_doc(_GITHUB_ISSUER)

        with patch(
            "urllib.request.urlopen",
            side_effect=_make_urlopen_mock({"discovery": discovery, "rsa_key": github_rsa_key}),
        ) as mock_urlopen:
            verifier.verify(token)
            second_token = _sign_token(github_rsa_key, _GITHUB_ISSUER, _GITHUB_AUDIENCE)
            verifier.verify(second_token)

        # discovery + jwks fetched once each, not once per verify() call
        assert mock_urlopen.call_count == 2
