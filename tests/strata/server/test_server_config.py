"""Tests for ServerRuntimeConfig's loopback-or-TLS bind rule (ADR-0065 Step 2.1)."""

from __future__ import annotations

from pathlib import Path

from strata.server.config import ServerRuntimeConfig


class TestValidateBind:
    def test_loopback_without_tls_is_allowed(self) -> None:
        config = ServerRuntimeConfig(host="127.0.0.1", port=8443)
        assert config.validate_bind() is None

    def test_ipv6_loopback_without_tls_is_allowed(self) -> None:
        config = ServerRuntimeConfig(host="::1", port=8443)
        assert config.validate_bind() is None

    def test_localhost_without_tls_is_allowed(self) -> None:
        config = ServerRuntimeConfig(host="localhost", port=8443)
        assert config.validate_bind() is None

    def test_non_loopback_without_tls_is_refused(self) -> None:
        config = ServerRuntimeConfig(host="0.0.0.0", port=8443)
        error = config.validate_bind()
        assert error is not None
        assert "TLS" in error

    def test_non_loopback_with_only_cert_is_refused(self) -> None:
        config = ServerRuntimeConfig(host="0.0.0.0", port=8443, tls_cert=Path("cert.pem"))
        assert config.validate_bind() is not None

    def test_non_loopback_with_only_key_is_refused(self) -> None:
        config = ServerRuntimeConfig(host="0.0.0.0", port=8443, tls_key=Path("key.pem"))
        assert config.validate_bind() is not None

    def test_non_loopback_with_both_cert_and_key_is_allowed(self) -> None:
        config = ServerRuntimeConfig(host="0.0.0.0", port=8443, tls_cert=Path("cert.pem"), tls_key=Path("key.pem"))
        assert config.validate_bind() is None


class TestHelperPredicates:
    def test_is_loopback_true_for_loopback_hosts(self) -> None:
        assert ServerRuntimeConfig(host="127.0.0.1", port=1).is_loopback() is True

    def test_is_loopback_false_for_other_hosts(self) -> None:
        assert ServerRuntimeConfig(host="10.0.0.5", port=1).is_loopback() is False

    def test_has_tls_false_when_missing_either(self) -> None:
        assert ServerRuntimeConfig(host="x", port=1, tls_cert=Path("c")).has_tls() is False
        assert ServerRuntimeConfig(host="x", port=1, tls_key=Path("k")).has_tls() is False

    def test_has_tls_true_when_both_present(self) -> None:
        assert ServerRuntimeConfig(host="x", port=1, tls_cert=Path("c"), tls_key=Path("k")).has_tls() is True
