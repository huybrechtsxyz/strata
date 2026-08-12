"""Tests for PKCE/state helpers (ADR-0067 Step 7)."""

from __future__ import annotations

import base64
import hashlib

from strata.server.auth.pkce import code_challenge_s256, generate_code_verifier, generate_nonce, generate_state


class TestGenerateState:
    def test_returns_a_nonempty_string(self) -> None:
        assert len(generate_state()) > 20

    def test_two_calls_are_different(self) -> None:
        assert generate_state() != generate_state()

    def test_is_urlsafe_base64_no_padding(self) -> None:
        value = generate_state()
        assert "=" not in value
        assert "+" not in value
        assert "/" not in value


class TestGenerateNonce:
    def test_returns_a_nonempty_string(self) -> None:
        assert len(generate_nonce()) > 20

    def test_two_calls_are_different(self) -> None:
        assert generate_nonce() != generate_nonce()

    def test_distinct_from_state_generation_output_space(self) -> None:
        # Not the same call site — a nonce and a state should never collide by construction.
        assert generate_nonce() != generate_state()


class TestGenerateCodeVerifier:
    def test_length_within_rfc7636_bounds(self) -> None:
        # RFC 7636 §4.1: 43-128 characters.
        verifier = generate_code_verifier()
        assert 43 <= len(verifier) <= 128

    def test_two_calls_are_different(self) -> None:
        assert generate_code_verifier() != generate_code_verifier()


class TestCodeChallengeS256:
    def test_matches_manual_rfc7636_computation(self) -> None:
        verifier = "test-verifier-value-1234567890"
        expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode()
        assert code_challenge_s256(verifier) == expected

    def test_is_deterministic(self) -> None:
        verifier = generate_code_verifier()
        assert code_challenge_s256(verifier) == code_challenge_s256(verifier)

    def test_different_verifiers_produce_different_challenges(self) -> None:
        assert code_challenge_s256(generate_code_verifier()) != code_challenge_s256(generate_code_verifier())
