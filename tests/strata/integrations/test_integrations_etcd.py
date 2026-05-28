#!/usr/bin/env python3
"""Unit tests for EtcdIntegration."""

from unittest.mock import MagicMock, patch

from strata.integrations.base_integration import BaseIntegration
from strata.integrations.capabilities import IKVStore, IVariableStore
from strata.integrations.etcd import EtcdIntegration
from strata.models.integration_model import IntegrationEndpointsSpecModel, IntegrationModel


def _cfg(name="etcd", address=None) -> IntegrationModel:
    endpoints = IntegrationEndpointsSpecModel(address=address) if address else None
    return IntegrationModel(name=name, type="etcd", endpoints=endpoints)


class TestEtcdIntegrationInit:
    def setup_method(self):
        BaseIntegration._instances.clear()

    def test_command_is_etcdctl(self):
        i = EtcdIntegration(_cfg())
        assert i.command == "etcdctl"

    def test_capabilities(self):
        assert IVariableStore in EtcdIntegration.CAPABILITIES
        assert IKVStore in EtcdIntegration.CAPABILITIES

    def test_version_command(self):
        i = EtcdIntegration(_cfg())
        assert i.get_version_command() == ["etcdctl", "version"]

    def test_endpoint_from_config(self):
        i = EtcdIntegration(_cfg(address="http://etcd.example.com:2379"))
        assert i.etcd_endpoints == "http://etcd.example.com:2379"

    def test_endpoint_default_localhost(self):
        i = EtcdIntegration(_cfg())
        assert i.etcd_endpoints == "http://127.0.0.1:2379"

    def test_endpoint_trailing_slash_stripped(self):
        i = EtcdIntegration(_cfg(address="http://etcd.example.com:2379/"))
        assert not i.etcd_endpoints.endswith("/")

    def test_endpoint_from_env_var(self, monkeypatch):
        monkeypatch.setenv("ETCD_ENDPOINTS", "http://etcd-env.example.com:2379")
        BaseIntegration._instances.clear()
        i = EtcdIntegration(_cfg())
        assert i.etcd_endpoints == "http://etcd-env.example.com:2379"


class TestEtcdParseVersion:
    def setup_method(self):
        BaseIntegration._instances.clear()

    def test_parse_etcdctl_version(self):
        i = EtcdIntegration(_cfg())
        assert i.parse_version("etcdctl version: 3.5.9\nAPI version: 3.5") == "3.5.9"

    def test_parse_no_version_fallback(self):
        i = EtcdIntegration(_cfg())
        result = i.parse_version("no version here")
        assert result == "no version here"


class TestEtcdSingleton:
    def setup_method(self):
        BaseIntegration._instances.clear()

    def test_same_address_same_instance(self):
        a = EtcdIntegration(_cfg(address="http://etcd.example.com:2379"))
        b = EtcdIntegration(_cfg(address="http://etcd.example.com:2379"))
        assert a is b

    def test_different_addresses_different_instances(self):
        a = EtcdIntegration(_cfg(address="http://etcd1.example.com:2379"))
        BaseIntegration._instances.clear()
        b = EtcdIntegration(_cfg(address="http://etcd2.example.com:2379"))
        assert a is not b


class TestEtcdEnsureAvailable:
    def setup_method(self):
        BaseIntegration._instances.clear()

    def test_fails_when_cli_not_available(self):
        i = EtcdIntegration(_cfg())
        with patch.object(i, "is_available", return_value=False):
            ok, msg = i.ensure_available()
        assert not ok
        assert "etcdctl" in msg

    def test_succeeds_when_cli_available_and_version_valid(self):
        i = EtcdIntegration(_cfg())
        with patch.object(i, "is_available", return_value=True):
            with patch.object(i, "validate_version", return_value=(True, "")):
                with patch.object(i, "get_version", return_value="3.5.9"):
                    ok, msg = i.ensure_available()
        assert ok
        assert msg == ""


class TestEtcdBaseArgs:
    def setup_method(self):
        BaseIntegration._instances.clear()

    def test_base_args_contains_endpoints(self):
        i = EtcdIntegration(_cfg(address="http://etcd.example.com:2379"))
        args = i._base_args()
        assert any("etcd.example.com" in a for a in args)

    def test_base_args_adds_user_when_auth_set(self, monkeypatch):
        monkeypatch.setenv("ETCD_USERNAME", "alice")
        monkeypatch.setenv("ETCD_PASSWORD", "secret")
        BaseIntegration._instances.clear()
        i = EtcdIntegration(_cfg())
        args = i._base_args()
        assert any("alice" in a for a in args)

    def test_base_args_adds_cacert_when_tls_set(self, monkeypatch):
        monkeypatch.setenv("ETCD_CA_FILE", "/etc/ssl/ca.crt")
        BaseIntegration._instances.clear()
        i = EtcdIntegration(_cfg())
        args = i._base_args()
        assert any("ca.crt" in a for a in args)


class TestEtcdGetKeyvalue:
    def setup_method(self):
        BaseIntegration._instances.clear()

    def test_get_keyvalue_cli_not_available_returns_none(self):
        i = EtcdIntegration(_cfg())
        with patch.object(i, "is_available", return_value=False):
            assert i.get_keyvalue("/my/key") is None

    def test_get_keyvalue_via_cli(self):
        i = EtcdIntegration(_cfg(address="http://etcd.example.com:2379"))
        mock_result = MagicMock(returncode=0, stdout="myvalue\n", stderr="")
        with patch.object(i, "is_available", return_value=True):
            with patch.object(i, "validate_version", return_value=(True, "")):
                with patch.object(i, "get_version", return_value="3.5.9"):
                    with patch.object(i, "_run_integration", return_value=mock_result):
                        result = i.get_keyvalue("/my/key")
        assert result == "myvalue"

    def test_get_keyvalue_cli_fail_falls_back_to_api(self):
        i = EtcdIntegration(_cfg(address="http://etcd.example.com:2379"))
        mock_result = MagicMock(returncode=1, stdout="", stderr="error")
        with patch.object(i, "is_available", return_value=True):
            with patch.object(i, "validate_version", return_value=(True, "")):
                with patch.object(i, "get_version", return_value="3.5.9"):
                    with patch.object(i, "_run_integration", return_value=mock_result):
                        with patch.object(i, "_get_kv_via_api", return_value="api-value") as mock_api:
                            result = i.get_keyvalue("/my/key")
        mock_api.assert_called_once()
        assert result == "api-value"


class TestEtcdPutKeyvalue:
    def setup_method(self):
        BaseIntegration._instances.clear()

    def test_put_keyvalue_cli_not_available_returns_false(self):
        i = EtcdIntegration(_cfg())
        with patch.object(i, "is_available", return_value=False):
            assert i.put_keyvalue("/my/key", "value") is False

    def test_put_keyvalue_via_cli(self):
        i = EtcdIntegration(_cfg(address="http://etcd.example.com:2379"))
        mock_result = MagicMock(returncode=0, stdout="OK", stderr="")
        with patch.object(i, "is_available", return_value=True):
            with patch.object(i, "validate_version", return_value=(True, "")):
                with patch.object(i, "get_version", return_value="3.5.9"):
                    with patch.object(i, "_run_integration", return_value=mock_result):
                        result = i.put_keyvalue("/my/key", "myvalue")
        assert result is True


class TestEtcdListKeys:
    def setup_method(self):
        BaseIntegration._instances.clear()

    def test_list_keys_cli_not_available_returns_empty(self):
        i = EtcdIntegration(_cfg())
        with patch.object(i, "is_available", return_value=False):
            assert i.list_keys("/prefix/") == []

    def test_list_keys_via_cli(self):
        i = EtcdIntegration(_cfg(address="http://etcd.example.com:2379"))
        mock_result = MagicMock(returncode=0, stdout="/prefix/a\n/prefix/b\n", stderr="")
        with patch.object(i, "is_available", return_value=True):
            with patch.object(i, "validate_version", return_value=(True, "")):
                with patch.object(i, "get_version", return_value="3.5.9"):
                    with patch.object(i, "_run_integration", return_value=mock_result):
                        result = i.list_keys("/prefix/")
        assert "/prefix/a" in result
        assert "/prefix/b" in result


class TestEtcdVariableStore:
    def setup_method(self):
        BaseIntegration._instances.clear()

    def test_get_variable_delegates_to_get_keyvalue(self):
        i = EtcdIntegration(_cfg())
        with patch.object(i, "get_keyvalue", return_value="val") as mock_gk:
            result = i.get_variable("/my/key")
        mock_gk.assert_called_once()
        assert result == "val"

    def test_set_variable_delegates_to_put_keyvalue(self):
        i = EtcdIntegration(_cfg())
        with patch.object(i, "put_keyvalue", return_value=True) as mock_pk:
            result = i.set_variable("/my/key", "value")
        mock_pk.assert_called_once()
        assert result is True

    def test_list_variables_delegates_to_list_keys(self):
        i = EtcdIntegration(_cfg())
        with patch.object(i, "list_keys", return_value=["/k1", "/k2"]) as mock_lk:
            result = i.list_variables("/prefix/")
        mock_lk.assert_called_once()
        assert result == ["/k1", "/k2"]


class TestEtcdGetInfo:
    def setup_method(self):
        BaseIntegration._instances.clear()

    def test_get_info_contains_expected_fields(self):
        i = EtcdIntegration(_cfg(address="http://etcd.example.com:2379"))
        info = i.get_info()
        assert info["endpoints"] == "http://etcd.example.com:2379"
        assert "has_auth" in info
        assert "has_tls" in info

    def test_has_auth_true_when_username_set(self, monkeypatch):
        monkeypatch.setenv("ETCD_USERNAME", "alice")
        monkeypatch.setenv("ETCD_PASSWORD", "secret")
        BaseIntegration._instances.clear()
        i = EtcdIntegration(_cfg())
        assert i.get_info()["has_auth"] is True

    def test_has_tls_true_when_cacert_set(self, monkeypatch):
        monkeypatch.setenv("ETCD_CA_FILE", "/etc/ssl/ca.crt")
        BaseIntegration._instances.clear()
        i = EtcdIntegration(_cfg())
        assert i.get_info()["has_tls"] is True


class TestEtcdGetSetupInfo:
    def setup_method(self):
        BaseIntegration._instances.clear()

    def test_setup_info_has_required_fields(self):
        i = EtcdIntegration(_cfg())
        info = i.get_setup_info()
        assert info["name"] == "etcd"
        assert info["command"] == "etcdctl"
        assert "env_vars" in info
        assert "auth_methods" in info
