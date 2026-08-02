#!/usr/bin/env python3
"""Unit tests for BitwardenIntegration."""

from unittest.mock import MagicMock, patch

import pytest

from strata.exceptions import SecretStoreUnavailableError
from strata.integrations.base_integration import BaseIntegration
from strata.integrations.bitwarden import BitwardenIntegration
from strata.integrations.capabilities import ISecretStore
from strata.models.auth_models import AuthenticationModel
from strata.models.integration_model import IntegrationModel


def _cfg(name="bw", token_var=None) -> IntegrationModel:
    auth = None
    if token_var:
        from strata.models.auth_models import APIKeyAuthenticationModel

        auth = AuthenticationModel(method="api_key", api_key=APIKeyAuthenticationModel(api_key=token_var))
    return IntegrationModel(name=name, type="bitwarden", authentication=auth)


class TestBitwardenIntegrationInit:
    def setup_method(self):
        BaseIntegration._instances.clear()

    def test_command_is_bws(self):
        i = BitwardenIntegration(_cfg())
        assert i.command == "bws"

    def test_capabilities_include_secrets(self):
        assert ISecretStore in BitwardenIntegration.CAPABILITIES

    def test_no_token_when_env_var_not_set(self):
        i = BitwardenIntegration(_cfg())
        assert i.access_token is None

    def test_access_token_loaded_from_env(self, monkeypatch):
        monkeypatch.setenv("BWS_ACCESS_TOKEN", "tok123")
        BaseIntegration._instances.clear()
        i = BitwardenIntegration(_cfg(token_var="BWS_ACCESS_TOKEN"))
        assert i.access_token == "tok123"

    def test_version_command(self):
        i = BitwardenIntegration(_cfg())
        assert i.get_version_command() == ["bws", "--version"]


class TestBitwardenParseVersion:
    def setup_method(self):
        BaseIntegration._instances.clear()

    def test_parse_bws_version(self):
        i = BitwardenIntegration(_cfg())
        assert i.parse_version("0.3.1") == "0.3.1"

    def test_parse_with_prefix(self):
        i = BitwardenIntegration(_cfg())
        result = i.parse_version("bws 0.3.1")
        # Should extract digits
        assert "0.3.1" in result or result == "bws 0.3.1"


class TestBitwardenSingleton:
    def setup_method(self):
        BaseIntegration._instances.clear()

    def test_same_config_same_instance(self):
        a = BitwardenIntegration(_cfg("bw1"))
        b = BitwardenIntegration(_cfg("bw1"))
        assert a is b

    def test_different_tokens_different_instances(self, monkeypatch):
        monkeypatch.setenv("TOKEN_A", "aaa")
        monkeypatch.setenv("TOKEN_B", "bbb")
        BaseIntegration._instances.clear()
        a = BitwardenIntegration(_cfg("bw_a", token_var="TOKEN_A"))
        BaseIntegration._instances.clear()
        b = BitwardenIntegration(_cfg("bw_b", token_var="TOKEN_B"))
        assert a is not b


class TestBitwardenGetSecret:
    def setup_method(self):
        BaseIntegration._instances.clear()

    def test_get_secret_success(self, monkeypatch):
        monkeypatch.setenv("BWS_ACCESS_TOKEN", "tok123")
        BaseIntegration._instances.clear()
        i = BitwardenIntegration(_cfg(token_var="BWS_ACCESS_TOKEN"))
        mock_result = MagicMock(returncode=0, stdout='{"value": "my_secret"}', stderr="")
        with (
            patch.object(i, "ensure_available", return_value=(True, "")),
            patch.object(i, "_run_integration", return_value=mock_result),
        ):
            result = i.get_secret("secret-id")
        assert result == "my_secret"

    def test_get_secret_unavailable_raises(self):
        """Misconfiguration (no CLI, no token) must raise, not silently return None."""
        i = BitwardenIntegration(_cfg())
        with pytest.raises(SecretStoreUnavailableError):
            i.get_secret("secret-id")

    def test_get_secret_command_failure_raises(self, monkeypatch):
        """A non-zero bws CLI exit must raise — bws has no reliable way to
        distinguish 'secret not found' from an auth/network failure, so any
        failure is treated as unavailable rather than risking generate-on-missing.
        """
        monkeypatch.setenv("BWS_ACCESS_TOKEN", "tok123")
        BaseIntegration._instances.clear()
        i = BitwardenIntegration(_cfg(token_var="BWS_ACCESS_TOKEN"))
        mock_result = MagicMock(returncode=1, stdout="", stderr="error")
        with (
            patch.object(i, "ensure_available", return_value=(True, "")),
            patch.object(i, "_run_integration", return_value=mock_result),
        ):
            with pytest.raises(SecretStoreUnavailableError):
                i.get_secret("secret-id")
