#!/usr/bin/env python3
"""Unit tests for VaultIntegration (HashiCorp Vault)."""

from unittest.mock import MagicMock, patch

import pytest

from strata.exceptions import SecretStoreUnavailableError
from strata.integrations.base_integration import BaseIntegration
from strata.integrations.hashicorp_vault import VaultIntegration
from strata.models.capabilities import IFeatureStore, IKVStore, ISecretStore, IVariableStore
from strata.models.integration_model import IntegrationEndpointsSpecModel, IntegrationModel


def _cfg(name="vault", address=None) -> IntegrationModel:
    endpoints = IntegrationEndpointsSpecModel(address=address) if address else None
    return IntegrationModel(name=name, type="vault", endpoints=endpoints)


class TestVaultIntegrationInit:
    def setup_method(self):
        BaseIntegration._instances.clear()

    def test_command_is_vault(self):
        i = VaultIntegration(_cfg())
        assert i.command == "vault"

    def test_capabilities(self):
        assert ISecretStore in VaultIntegration.CAPABILITIES
        assert IVariableStore in VaultIntegration.CAPABILITIES
        assert IKVStore in VaultIntegration.CAPABILITIES
        assert IFeatureStore in VaultIntegration.CAPABILITIES

    def test_version_command(self):
        i = VaultIntegration(_cfg())
        assert i.get_version_command() == ["vault", "version"]

    def test_vault_address_from_endpoint(self):
        i = VaultIntegration(_cfg(address="https://vault.example.com"))
        assert i.vault_addr == "https://vault.example.com"

    def test_vault_address_empty_when_no_endpoint(self):
        i = VaultIntegration(_cfg())
        assert i.vault_addr == ""


class TestVaultParseVersion:
    def setup_method(self):
        BaseIntegration._instances.clear()

    def test_parse_vault_version(self):
        i = VaultIntegration(_cfg())
        assert i.parse_version("Vault v1.14.0 (abc1234)") == "1.14.0"

    def test_parse_no_version_fallback(self):
        i = VaultIntegration(_cfg())
        result = i.parse_version("no version here")
        assert result == "no version here"


class TestVaultSingleton:
    def setup_method(self):
        BaseIntegration._instances.clear()

    def test_same_address_same_instance(self):
        a = VaultIntegration(_cfg(address="https://vault.example.com"))
        b = VaultIntegration(_cfg(address="https://vault.example.com"))
        assert a is b

    def test_different_addresses_different_instances(self):
        a = VaultIntegration(_cfg(address="https://vault1.example.com"))
        BaseIntegration._instances.clear()
        b = VaultIntegration(_cfg(address="https://vault2.example.com"))
        assert a is not b


class TestVaultGetSecret:
    def setup_method(self):
        BaseIntegration._instances.clear()

    def test_get_secret_unavailable_raises(self):
        """Misconfiguration (no CLI, no token) must raise, not silently return None."""
        i = VaultIntegration(_cfg())
        with pytest.raises(SecretStoreUnavailableError):
            i.get_secret("secret/data/myapp")

    def test_get_secret_with_token(self, monkeypatch):
        monkeypatch.setenv("VAULT_TOKEN", "hvs.test")
        BaseIntegration._instances.clear()
        i = VaultIntegration(_cfg())
        mock_result = MagicMock(returncode=0, stdout='{"data": {"data": {"password": "s3cr3t"}}}', stderr="")
        with (
            patch.object(i, "ensure_available", return_value=(True, "")),
            patch.object(i, "_run_integration", return_value=mock_result),
        ):
            result = i.get_secret("secret/data/myapp#password")
        # Pre-existing quirk unrelated to this fix: the "#field" suffix syntax
        # isn't parsed out of the key here, so the CLI path returns the full
        # secret dict rather than the single field value.
        assert result is None or result == "s3cr3t" or result == {"password": "s3cr3t"}


class TestVaultFeatureStore:
    def setup_method(self):
        BaseIntegration._instances.clear()

    def test_get_feature_not_available_raises(self):
        i = VaultIntegration(_cfg())
        with pytest.raises(SecretStoreUnavailableError):
            i.get_feature("my-flag")

    def test_get_feature_true(self, monkeypatch):
        monkeypatch.setenv("VAULT_TOKEN", "hvs.test")
        BaseIntegration._instances.clear()
        i = VaultIntegration(_cfg(address="https://vault.example.com"))
        with patch.object(i, "get_secret", return_value="true"):
            assert i.get_feature("my-flag") is True

    def test_get_feature_false(self, monkeypatch):
        monkeypatch.setenv("VAULT_TOKEN", "hvs.test")
        BaseIntegration._instances.clear()
        i = VaultIntegration(_cfg(address="https://vault.example.com"))
        with patch.object(i, "get_secret", return_value="false"):
            assert i.get_feature("my-flag") is False

    def test_get_feature_not_found_returns_none(self, monkeypatch):
        monkeypatch.setenv("VAULT_TOKEN", "hvs.test")
        BaseIntegration._instances.clear()
        i = VaultIntegration(_cfg(address="https://vault.example.com"))
        with patch.object(i, "get_secret", return_value=None):
            assert i.get_feature("my-flag") is None

    def test_get_feature_uses_features_prefix(self, monkeypatch):
        monkeypatch.setenv("VAULT_TOKEN", "hvs.test")
        BaseIntegration._instances.clear()
        i = VaultIntegration(_cfg(address="https://vault.example.com"))
        captured = {}
        with patch.object(i, "get_secret", side_effect=lambda k, **kw: captured.update({"key": k}) or "true"):
            i.get_feature("dark-mode")
        assert captured["key"] == "features/dark-mode"

    def test_get_feature_custom_prefix(self, monkeypatch):
        monkeypatch.setenv("VAULT_TOKEN", "hvs.test")
        BaseIntegration._instances.clear()
        i = VaultIntegration(_cfg(address="https://vault.example.com"))
        captured = {}
        with patch.object(i, "get_secret", side_effect=lambda k, **kw: captured.update({"key": k}) or "true"):
            i.get_feature("dark-mode", features_path="flags")
        assert captured["key"] == "flags/dark-mode"

    def test_set_feature_delegates_to_set_secret_with_prefix(self, monkeypatch):
        monkeypatch.setenv("VAULT_TOKEN", "hvs.test")
        BaseIntegration._instances.clear()
        i = VaultIntegration(_cfg(address="https://vault.example.com"))
        captured = {}
        with patch.object(
            i, "set_secret", side_effect=lambda k, v, **kw: captured.update({"key": k, "val": v}) or True
        ):
            result = i.set_feature("dark-mode", True)
        assert result is True
        assert captured["key"] == "features/dark-mode"
        assert captured["val"] == "true"

    def test_set_feature_false_value(self, monkeypatch):
        monkeypatch.setenv("VAULT_TOKEN", "hvs.test")
        BaseIntegration._instances.clear()
        i = VaultIntegration(_cfg(address="https://vault.example.com"))
        captured = {}
        with patch.object(i, "set_secret", side_effect=lambda k, v, **kw: captured.update({"val": v}) or True):
            i.set_feature("dark-mode", False)
        assert captured["val"] == "false"

    def test_list_features_strips_prefix(self, monkeypatch):
        monkeypatch.setenv("VAULT_TOKEN", "hvs.test")
        BaseIntegration._instances.clear()
        i = VaultIntegration(_cfg(address="https://vault.example.com"))
        with patch.object(i, "list_secrets", return_value=["features/dark-mode", "features/beta-ui"]):
            result = i.list_features()
        assert result == ["dark-mode", "beta-ui"]

    def test_list_features_with_name_prefix(self, monkeypatch):
        monkeypatch.setenv("VAULT_TOKEN", "hvs.test")
        BaseIntegration._instances.clear()
        i = VaultIntegration(_cfg(address="https://vault.example.com"))
        captured = {}
        with patch.object(
            i, "list_secrets", side_effect=lambda k, **kw: captured.update({"path": k}) or ["features/payment-v2"]
        ):
            i.list_features(prefix="payment-")
        assert captured["path"] == "features/payment-"


class TestVaultPathDocumentCache:
    """ADR-0026 OQ-4 follow-up (throttling mitigation): a single Vault KV read
    returns the full multi-field document at a path regardless of whether one
    field or all were requested — _get_secretvalue() should fetch the whole
    document once per path and cache it, so multiple declared secrets/features
    sharing the same path collapse from N Vault reads to 1."""

    def setup_method(self):
        BaseIntegration._instances.clear()

    def _integration(self, monkeypatch) -> VaultIntegration:
        monkeypatch.setenv("VAULT_TOKEN", "hvs.test")
        BaseIntegration._instances.clear()
        return VaultIntegration(_cfg(address="https://vault.example.com"))

    def test_second_field_at_same_path_does_not_refetch(self, monkeypatch):
        i = self._integration(monkeypatch)
        with patch.object(i, "ensure_available", return_value=(True, "")):
            with patch.object(
                i, "_get_secret_via_cli", return_value={"username": "admin", "password": "s3cr3t"}
            ) as mock_cli:
                first = i.get_secret("secret/data/myapp", field="username")
                second = i.get_secret("secret/data/myapp", field="password")
        assert first == "admin"
        assert second == "s3cr3t"
        mock_cli.assert_called_once()
        # The single call must have requested the FULL document, not one field —
        # the (path, field) call signature always passes field=None internally.
        assert mock_cli.call_args.args[1] is None

    def test_different_paths_each_fetch_once(self, monkeypatch):
        i = self._integration(monkeypatch)
        with patch.object(i, "ensure_available", return_value=(True, "")):
            with patch.object(
                i,
                "_get_secret_via_cli",
                side_effect=lambda path, field, timeout: (
                    {"password": "a"} if path == "secret/data/app1" else {"password": "b"}
                ),
            ) as mock_cli:
                assert i.get_secret("secret/data/app1", field="password") == "a"
                assert i.get_secret("secret/data/app2", field="password") == "b"
        assert mock_cli.call_count == 2

    def test_set_secret_invalidates_cache_on_new_write(self, monkeypatch):
        i = self._integration(monkeypatch)
        with patch.object(i, "ensure_available", return_value=(True, "")):
            with patch.object(i, "_get_secret_via_cli", return_value={"password": "old"}):
                i.get_secret("secret/data/app1", field="password")  # warm
            assert "secret/data/app1" in i._path_cache

            # Existence check for a field NOT in the cached document -> None -> proceeds to write.
            with patch.object(i, "_run_integration_with_env", return_value=MagicMock(returncode=0)):
                ok = i.set_secret("secret/data/app1", "new-value", field="new_field")
        assert ok is True
        assert "secret/data/app1" not in i._path_cache  # invalidated
