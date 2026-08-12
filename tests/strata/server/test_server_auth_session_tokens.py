"""Tests for the interim stateless session tokens (ADR-0067 Step 7)."""

from __future__ import annotations

import time

from strata.server.auth.session_tokens import mint_session_token, verify_session_token


class TestMintAndVerifySessionToken:
    def test_round_trip_returns_original_claims(self) -> None:
        token = mint_session_token({"sub": "user-1", "email": "user@example.test"}, "secret", ttl_seconds=60)
        claims = verify_session_token(token, "secret")
        assert claims is not None
        assert claims["sub"] == "user-1"
        assert claims["email"] == "user@example.test"

    def test_wrong_secret_fails_verification(self) -> None:
        token = mint_session_token({"sub": "user-1"}, "secret", ttl_seconds=60)
        assert verify_session_token(token, "wrong-secret") is None

    def test_tampered_payload_fails_verification(self) -> None:
        token = mint_session_token({"sub": "user-1"}, "secret", ttl_seconds=60)
        payload_b64, signature = token.split(".", 1)
        tampered = payload_b64 + "x." + signature
        assert verify_session_token(tampered, "secret") is None

    def test_expired_token_fails_verification(self) -> None:
        token = mint_session_token({"sub": "user-1"}, "secret", ttl_seconds=-1)
        assert verify_session_token(token, "secret") is None

    def test_not_yet_expired_token_verifies(self) -> None:
        token = mint_session_token({"sub": "user-1"}, "secret", ttl_seconds=60)
        assert verify_session_token(token, "secret") is not None

    def test_malformed_token_returns_none(self) -> None:
        assert verify_session_token("not-a-valid-token", "secret") is None

    def test_exp_claim_is_embedded_and_roughly_correct(self) -> None:
        before = time.time()
        token = mint_session_token({}, "secret", ttl_seconds=100)
        claims = verify_session_token(token, "secret")
        assert claims is not None
        assert before + 90 < claims["exp"] < before + 110
