"""Tests for refresh-token encryption at rest (ADR-0067 Step 8)."""

from __future__ import annotations

import pytest
from joserfc.errors import JoseError

from strata.server.auth.refresh_crypto import decrypt_refresh_token, encrypt_refresh_token


class TestEncryptDecryptRoundTrip:
    def test_round_trip_returns_original_value(self) -> None:
        encrypted = encrypt_refresh_token("my-refresh-token-value", "secret")
        assert decrypt_refresh_token(encrypted, "secret") == "my-refresh-token-value"

    def test_encrypted_value_does_not_contain_the_plaintext(self) -> None:
        encrypted = encrypt_refresh_token("super-secret-refresh-token", "secret")
        assert "super-secret-refresh-token" not in encrypted

    def test_wrong_key_fails_to_decrypt(self) -> None:
        encrypted = encrypt_refresh_token("my-refresh-token-value", "secret")
        with pytest.raises(JoseError):
            decrypt_refresh_token(encrypted, "wrong-secret")

    def test_tampered_ciphertext_fails_to_decrypt(self) -> None:
        encrypted = encrypt_refresh_token("my-refresh-token-value", "secret")
        parts = encrypted.split(".")
        # Flip a character in the ciphertext segment — GCM's authentication tag must reject this.
        tampered_ciphertext = ("A" if parts[3][0] != "A" else "B") + parts[3][1:]
        tampered = ".".join([parts[0], parts[1], parts[2], tampered_ciphertext, parts[4]])
        with pytest.raises(JoseError):
            decrypt_refresh_token(tampered, "secret")

    def test_two_encryptions_of_the_same_value_differ(self) -> None:
        # GCM uses a random IV per encryption — encrypting the same plaintext twice
        # must not produce identical ciphertext.
        first = encrypt_refresh_token("same-value", "secret")
        second = encrypt_refresh_token("same-value", "secret")
        assert first != second
