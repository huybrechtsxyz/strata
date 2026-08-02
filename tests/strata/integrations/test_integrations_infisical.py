#!/usr/bin/env python3
"""Unit tests for InfisicalIntegration."""

from unittest.mock import MagicMock, patch

import pytest

from strata.exceptions import SecretStoreUnavailableError
from strata.integrations.base_integration import BaseIntegration
from strata.integrations.capabilities import ISecretStore, IVariableStore
from strata.integrations.infisical import InfisicalIntegration
from strata.models.integration_model import IntegrationEndpointsSpecModel, IntegrationModel


def _cfg(name="infisical", address=None) -> IntegrationModel:
    endpoints = IntegrationEndpointsSpecModel(address=address) if address else None
    return IntegrationModel(name=name, type="infisical", endpoints=endpoints)


class TestInfisicalIntegrationInit:
    def setup_method(self):
        BaseIntegration._instances.clear()

    def test_command_is_infisical(self):
        i = InfisicalIntegration(_cfg())
        assert i.command == "infisical"

    def test_capabilities(self):
        assert ISecretStore in InfisicalIntegration.CAPABILITIES
        assert IVariableStore in InfisicalIntegration.CAPABILITIES

    def test_version_command(self):
        i = InfisicalIntegration(_cfg())
        assert i.get_version_command() == ["infisical", "--version"]

    def test_address_from_endpoint(self):
        i = InfisicalIntegration(_cfg(address="https://infisical.example.com"))
        assert i.infisical_addr == "https://infisical.example.com"

    def test_address_default_cloud(self):
        i = InfisicalIntegration(_cfg())
        assert i.infisical_addr == "https://app.infisical.com"

    def test_address_trailing_slash_stripped(self):
        i = InfisicalIntegration(_cfg(address="https://infisical.example.com/"))
        assert not i.infisical_addr.endswith("/")


class TestInfisicalParseVersion:
    def setup_method(self):
        BaseIntegration._instances.clear()

    def test_parse_semver(self):
        i = InfisicalIntegration(_cfg())
        assert i.parse_version("infisical v0.28.0") == "0.28.0"

    def test_parse_no_version_fallback(self):
        i = InfisicalIntegration(_cfg())
        result = i.parse_version("no version here")
        assert result == "no version here"


class TestInfisicalSingleton:
    def setup_method(self):
        BaseIntegration._instances.clear()

    def test_same_address_same_instance(self):
        a = InfisicalIntegration(_cfg(address="https://infisical.example.com"))
        b = InfisicalIntegration(_cfg(address="https://infisical.example.com"))
        assert a is b

    def test_different_addresses_different_instances(self):
        a = InfisicalIntegration(_cfg(address="https://infisical1.example.com"))
        BaseIntegration._instances.clear()
        b = InfisicalIntegration(_cfg(address="https://infisical2.example.com"))
        assert a is not b


class TestInfisicalAuthMethod:
    def setup_method(self):
        BaseIntegration._instances.clear()

    def test_auth_method_none_when_no_env(self):
        i = InfisicalIntegration(_cfg())
        assert i._get_auth_method() is None

    def test_auth_method_token_when_env_set(self, monkeypatch):
        monkeypatch.setenv("INFISICAL_TOKEN", "st.test.token")
        BaseIntegration._instances.clear()
        i = InfisicalIntegration(_cfg())
        assert i._get_auth_method() == "token"

    def test_auth_method_universal_when_client_creds(self, monkeypatch):
        monkeypatch.setenv("INFISICAL_CLIENT_ID", "client-id")
        monkeypatch.setenv("INFISICAL_CLIENT_SECRET", "client-secret")
        BaseIntegration._instances.clear()
        i = InfisicalIntegration(_cfg())
        assert i._get_auth_method() == "universal-auth"

    def test_token_takes_priority_over_universal_auth(self, monkeypatch):
        monkeypatch.setenv("INFISICAL_TOKEN", "st.test.token")
        monkeypatch.setenv("INFISICAL_CLIENT_ID", "client-id")
        monkeypatch.setenv("INFISICAL_CLIENT_SECRET", "client-secret")
        BaseIntegration._instances.clear()
        i = InfisicalIntegration(_cfg())
        assert i._get_auth_method() == "token"


class TestInfisicalEnsureAvailable:
    def setup_method(self):
        BaseIntegration._instances.clear()

    def test_fails_without_project_id(self):
        i = InfisicalIntegration(_cfg())
        ok, msg = i.ensure_available()
        assert not ok
        assert "project" in msg.lower()

    def test_fails_without_auth(self, monkeypatch):
        monkeypatch.setenv("INFISICAL_PROJECT_ID", "proj-123")
        BaseIntegration._instances.clear()
        i = InfisicalIntegration(_cfg())
        ok, msg = i.ensure_available()
        assert not ok
        assert "authentication" in msg.lower() or "token" in msg.lower()

    def test_succeeds_with_project_and_token(self, monkeypatch):
        monkeypatch.setenv("INFISICAL_PROJECT_ID", "proj-123")
        monkeypatch.setenv("INFISICAL_TOKEN", "st.test.token")
        BaseIntegration._instances.clear()
        i = InfisicalIntegration(_cfg())
        ok, msg = i.ensure_available()
        assert ok
        assert msg == ""


class TestInfisicalGetSecret:
    def setup_method(self):
        BaseIntegration._instances.clear()

    def test_get_secret_no_project_raises_unavailable(self):
        """Misconfiguration (no project ID) must raise, not silently return None —
        ValueController relies on this to avoid treating "unavailable" as "missing"
        and triggering generate-on-missing secret creation."""
        i = InfisicalIntegration(_cfg())
        with pytest.raises(SecretStoreUnavailableError):
            i.get_secret("DB_PASSWORD")

    def test_get_secret_via_cli(self, monkeypatch):
        monkeypatch.setenv("INFISICAL_PROJECT_ID", "proj-123")
        monkeypatch.setenv("INFISICAL_TOKEN", "st.test.token")
        BaseIntegration._instances.clear()
        i = InfisicalIntegration(_cfg())
        mock_result = MagicMock(returncode=0, stdout="s3cr3t\n", stderr="")
        with patch.object(i, "_run_integration", return_value=mock_result):
            with patch.object(i, "is_available", return_value=True):
                result = i.get_secret("DB_PASSWORD")
        assert result == "s3cr3t"

    def test_get_secret_cli_fail_falls_back_to_api(self, monkeypatch):
        monkeypatch.setenv("INFISICAL_PROJECT_ID", "proj-123")
        monkeypatch.setenv("INFISICAL_TOKEN", "st.test.token")
        BaseIntegration._instances.clear()
        i = InfisicalIntegration(_cfg())
        mock_result = MagicMock(returncode=1, stdout="", stderr="error")
        with patch.object(i, "_run_integration", return_value=mock_result):
            with patch.object(i, "is_available", return_value=True):
                with patch.object(i, "_get_secret_via_api", return_value="api-secret") as mock_api:
                    result = i.get_secret("DB_PASSWORD")
        mock_api.assert_called_once()
        assert result == "api-secret"


class TestInfisicalListSecrets:
    def setup_method(self):
        BaseIntegration._instances.clear()

    def test_list_secrets_no_project_returns_empty(self):
        i = InfisicalIntegration(_cfg())
        assert i.list_secrets() == []

    def test_list_secrets_with_prefix_filter(self, monkeypatch):
        monkeypatch.setenv("INFISICAL_PROJECT_ID", "proj-123")
        monkeypatch.setenv("INFISICAL_TOKEN", "st.test.token")
        BaseIntegration._instances.clear()
        i = InfisicalIntegration(_cfg())
        # _list_secrets_via_api applies the prefix filter internally; return pre-filtered result
        with patch.object(i, "_list_secrets_via_api", return_value=["DB_HOST", "DB_PASS"]):
            result = i.list_secrets(prefix="DB_")
        assert result == ["DB_HOST", "DB_PASS"]


class TestInfisicalVariableStore:
    def setup_method(self):
        BaseIntegration._instances.clear()

    def test_get_variable_delegates_to_get_secret(self, monkeypatch):
        monkeypatch.setenv("INFISICAL_PROJECT_ID", "proj-123")
        monkeypatch.setenv("INFISICAL_TOKEN", "st.test.token")
        BaseIntegration._instances.clear()
        i = InfisicalIntegration(_cfg())
        with patch.object(i, "get_secret", return_value="val") as mock_gs:
            result = i.get_variable("MY_VAR")
        mock_gs.assert_called_once_with("MY_VAR")
        assert result == "val"

    def test_list_variables_delegates_to_list_secrets(self, monkeypatch):
        monkeypatch.setenv("INFISICAL_PROJECT_ID", "proj-123")
        monkeypatch.setenv("INFISICAL_TOKEN", "st.test.token")
        BaseIntegration._instances.clear()
        i = InfisicalIntegration(_cfg())
        with patch.object(i, "list_secrets", return_value=["A", "B"]) as mock_ls:
            result = i.list_variables()
        mock_ls.assert_called_once_with(prefix="")
        assert result == ["A", "B"]


class TestInfisicalGetInfo:
    def setup_method(self):
        BaseIntegration._instances.clear()

    def test_get_info_contains_expected_fields(self, monkeypatch):
        monkeypatch.setenv("INFISICAL_PROJECT_ID", "proj-123")
        BaseIntegration._instances.clear()
        i = InfisicalIntegration(_cfg(address="https://infisical.example.com"))
        info = i.get_info()
        assert info["infisical_addr"] == "https://infisical.example.com"
        assert info["project_id"] == "proj-123"
        assert "auth_method" in info
        assert "environment" in info


class TestInfisicalGetSetupInfo:
    def setup_method(self):
        BaseIntegration._instances.clear()

    def test_setup_info_has_required_fields(self):
        i = InfisicalIntegration(_cfg())
        info = i.get_setup_info()
        assert info["name"] == "infisical"
        assert "env_vars" in info
        assert "auth_methods" in info
        env_names = [e["name"] for e in info["env_vars"]]
        assert "INFISICAL_TOKEN" in env_names
        assert "INFISICAL_PROJECT_ID" in env_names
