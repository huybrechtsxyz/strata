"""Tests for AzureScript base class and built-in Azure lifecycle scripts."""

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from strata.utils.azure_script_base import AzureScript

# ===========================================================================
# Helpers
# ===========================================================================


def _ok(stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=0, stdout=stdout, stderr=stderr)


def _fail(returncode: int = 1, stderr: str = "ERROR") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout="", stderr=stderr)


class _SimpleScript(AzureScript):
    def __init__(self, result):
        self._result = result
        self.ran = False

    def run(self):
        self.ran = True


# ===========================================================================
# AzureScript base
# ===========================================================================


class TestAzureScriptBase:
    def test_execute_calls_run(self):
        script = _SimpleScript(_ok())
        with pytest.raises(SystemExit):
            script.execute()
        assert script.ran

    def test_execute_exits_0_on_success(self):
        script = _SimpleScript(_ok())
        with pytest.raises(SystemExit) as exc:
            script.execute()
        assert exc.value.code == 0

    def test_execute_exits_1_on_exception(self):
        class BadScript(AzureScript):
            def run(self):
                raise RuntimeError("boom")

        with pytest.raises(SystemExit) as exc:
            BadScript().execute()
        assert exc.value.code == 1

    def test_run_az_builds_correct_command(self):
        script = _SimpleScript(_ok())
        with patch("subprocess.run", return_value=_ok()) as mock:
            script.run_az(["account", "show"])
        args = mock.call_args[0][0]
        assert args == ["az", "account", "show"]

    def test_exit_on_failure_exits_when_returncode_nonzero(self):
        script = _SimpleScript(_ok())
        with pytest.raises(SystemExit) as exc:
            script.exit_on_failure(_fail(returncode=2), "test cmd")
        assert exc.value.code == 1

    def test_exit_on_failure_does_not_exit_on_success(self):
        script = _SimpleScript(_ok())
        # Should not raise
        script.exit_on_failure(_ok("output"), "test cmd")

    def test_env_returns_value(self, monkeypatch):
        script = _SimpleScript(_ok())
        monkeypatch.setenv("MY_VAR", "hello")
        assert script.env("MY_VAR") == "hello"

    def test_env_returns_default_when_absent(self, monkeypatch):
        script = _SimpleScript(_ok())
        monkeypatch.delenv("MISSING_VAR", raising=False)
        assert script.env("MISSING_VAR", "fallback") == "fallback"

    def test_require_env_returns_value(self, monkeypatch):
        script = _SimpleScript(_ok())
        monkeypatch.setenv("REQUIRED_VAR", "value")
        assert script.require_env("REQUIRED_VAR") == "value"

    def test_require_env_exits_when_absent(self, monkeypatch):
        script = _SimpleScript(_ok())
        monkeypatch.delenv("MISSING_REQUIRED", raising=False)
        with pytest.raises(SystemExit) as exc:
            script.require_env("MISSING_REQUIRED")
        assert exc.value.code == 1

    def test_workspace_path_from_env(self, monkeypatch):
        script = _SimpleScript(_ok())
        monkeypatch.setenv("STRATA_WORKSPACE_PATH", "/workspace")
        assert script.workspace_path() == Path("/workspace")

    def test_stage_name_from_env(self, monkeypatch):
        script = _SimpleScript(_ok())
        monkeypatch.setenv("STRATA_STAGE_NAME", "infra")
        assert script.stage_name() == "infra"

    def test_builtin_scripts_dir_exists(self):
        d = AzureScript.builtin_scripts_dir()
        assert d.exists()
        assert (d / "azure_aks_credentials.py").exists()
        assert (d / "azure_acr_login.py").exists()
        assert (d / "azure_resource_group_ensure.py").exists()

    def test_get_token_returns_token(self):
        import json

        token_response = json.dumps({"accessToken": "eyJfake"})
        script = _SimpleScript(_ok())
        with patch.object(script, "run_az", return_value=_ok(token_response)):
            token = script.get_token()
        assert token == "eyJfake"

    def test_get_token_returns_none_on_failure(self):
        script = _SimpleScript(_ok())
        with patch.object(script, "run_az", return_value=_fail()):
            token = script.get_token()
        assert token is None


# ===========================================================================
# Built-in scripts — import and test run() logic
# ===========================================================================


class TestAksCredentials:
    def _load(self):
        from strata.data.scripts.azure_aks_credentials import AksCredentials

        return AksCredentials()

    def test_run_calls_aks_get_credentials(self, monkeypatch):
        monkeypatch.setenv("AKS_CLUSTER", "my-cluster")
        monkeypatch.setenv("AKS_RESOURCE_GROUP", "my-rg")

        script = self._load()
        with patch.object(script, "run_az", return_value=_ok()) as mock:
            script.run()

        args = mock.call_args[0][0]
        assert "aks" in args
        assert "get-credentials" in args
        assert "my-cluster" in args
        assert "my-rg" in args

    def test_exits_when_cluster_missing(self, monkeypatch):
        monkeypatch.delenv("AKS_CLUSTER", raising=False)
        monkeypatch.delenv("AKS_RESOURCE_GROUP", raising=False)
        script = self._load()
        with pytest.raises(SystemExit):
            script.run()

    def test_admin_flag_included(self, monkeypatch):
        monkeypatch.setenv("AKS_CLUSTER", "c")
        monkeypatch.setenv("AKS_RESOURCE_GROUP", "rg")
        monkeypatch.setenv("AKS_ADMIN_CREDENTIALS", "true")

        script = self._load()
        with patch.object(script, "run_az", return_value=_ok()) as mock:
            script.run()

        args = mock.call_args[0][0]
        assert "--admin" in args

    def test_subscription_included_when_set(self, monkeypatch):
        monkeypatch.setenv("AKS_CLUSTER", "c")
        monkeypatch.setenv("AKS_RESOURCE_GROUP", "rg")
        monkeypatch.setenv("AKS_SUBSCRIPTION", "sub-123")

        script = self._load()
        with patch.object(script, "run_az", return_value=_ok()) as mock:
            script.run()

        args = mock.call_args[0][0]
        assert "--subscription" in args
        assert "sub-123" in args


class TestAcrLogin:
    def _load(self):
        from strata.data.scripts.azure_acr_login import AcrLogin

        return AcrLogin()

    def test_run_calls_acr_login(self, monkeypatch):
        monkeypatch.setenv("ACR_NAME", "myregistry")

        script = self._load()
        with patch.object(script, "run_az", return_value=_ok()) as mock:
            script.run()

        args = mock.call_args[0][0]
        assert "acr" in args
        assert "login" in args
        assert "myregistry" in args

    def test_exits_when_acr_name_missing(self, monkeypatch):
        monkeypatch.delenv("ACR_NAME", raising=False)
        script = self._load()
        with pytest.raises(SystemExit):
            script.run()

    def test_expose_token_flag(self, monkeypatch):
        monkeypatch.setenv("ACR_NAME", "reg")
        monkeypatch.setenv("ACR_EXPOSE_TOKEN", "true")

        script = self._load()
        with patch.object(script, "run_az", return_value=_ok()) as mock:
            script.run()

        args = mock.call_args[0][0]
        assert "--expose-token" in args


class TestResourceGroupEnsure:
    def _load(self):
        from strata.data.scripts.azure_resource_group_ensure import ResourceGroupEnsure

        return ResourceGroupEnsure()

    def test_run_calls_group_create(self, monkeypatch):
        monkeypatch.setenv("AZURE_RESOURCE_GROUP", "my-rg")
        monkeypatch.setenv("AZURE_LOCATION", "westeurope")

        script = self._load()
        with patch.object(
            script, "run_az", return_value=_ok('{"properties": {"provisioningState": "Succeeded"}}')
        ) as mock:
            script.run()

        args = mock.call_args[0][0]
        assert "group" in args
        assert "create" in args
        assert "my-rg" in args
        assert "westeurope" in args

    def test_exits_when_rg_missing(self, monkeypatch):
        monkeypatch.delenv("AZURE_RESOURCE_GROUP", raising=False)
        monkeypatch.delenv("AZURE_LOCATION", raising=False)
        script = self._load()
        with pytest.raises(SystemExit):
            script.run()

    def test_tags_parsed_and_included(self, monkeypatch):
        monkeypatch.setenv("AZURE_RESOURCE_GROUP", "rg")
        monkeypatch.setenv("AZURE_LOCATION", "eastus")
        monkeypatch.setenv("AZURE_RG_TAGS", "env=prod, team=platform")

        script = self._load()
        with patch.object(script, "run_az", return_value=_ok("{}")) as mock:
            script.run()

        args = mock.call_args[0][0]
        assert "--tags" in args
        assert "env=prod" in args
        assert "team=platform" in args

    def test_exits_on_az_failure(self, monkeypatch):
        monkeypatch.setenv("AZURE_RESOURCE_GROUP", "rg")
        monkeypatch.setenv("AZURE_LOCATION", "eastus")

        script = self._load()
        with patch.object(script, "run_az", return_value=_fail()), pytest.raises(SystemExit):
            script.run()
