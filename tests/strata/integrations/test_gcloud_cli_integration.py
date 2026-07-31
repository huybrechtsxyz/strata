"""Tests for GCloudCLIIntegration and GCloudScript base class and built-in scripts."""

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from strata.integrations.gcloud_cli import GCloudCLIIntegration
from strata.models.integration_model import IntegrationModel
from strata.utils.gcloud_script_base import GCloudScript

# ===========================================================================
# Helpers
# ===========================================================================


def _ok(stdout: str = ""):
    r = MagicMock()
    r.returncode = 0
    r.stdout = stdout
    r.stderr = ""
    return r


def _fail(stderr: str = "ERROR"):
    r = MagicMock()
    r.returncode = 1
    r.stdout = ""
    r.stderr = stderr
    return r


def _cp(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


def _make():
    return GCloudCLIIntegration(IntegrationModel(name="gcloud", type="gcloud_cli"))


class _SimpleScript(GCloudScript):
    def __init__(self):
        self.ran = False

    def run(self):
        self.ran = True


# ===========================================================================
# GCloudCLIIntegration
# ===========================================================================


class TestParseVersion:
    def test_full_sdk_output(self):
        gz = _make()
        result = gz.parse_version("Google Cloud SDK 498.0.0\nbq 2.0\n")
        assert result == "498.0.0"

    def test_fallback_regex(self):
        gz = _make()
        assert gz.parse_version("version 450.0.1") == "450.0.1"

    def test_returns_first_line_on_no_version(self):
        gz = _make()
        assert gz.parse_version("unknown output") == "unknown output"


class TestEnsureAvailable:
    def test_not_installed(self):
        gz = _make()
        with patch.object(gz, "is_available", return_value=False):
            ok, msg = gz.ensure_available()
        assert not ok
        assert "not installed" in msg.lower()

    def test_no_account(self):
        gz = _make()
        with patch.object(gz, "is_available", return_value=True), patch.object(gz, "get_account", return_value=None):
            ok, msg = gz.ensure_available()
        assert not ok
        assert "gcloud auth login" in msg

    def test_no_project(self):
        gz = _make()
        with (
            patch.object(gz, "is_available", return_value=True),
            patch.object(gz, "get_account", return_value="user@example.com"),
            patch.object(gz, "get_project", return_value=None),
        ):
            ok, msg = gz.ensure_available()
        assert not ok
        assert "project" in msg.lower()

    def test_fully_authenticated(self):
        gz = _make()
        with (
            patch.object(gz, "is_available", return_value=True),
            patch.object(gz, "get_account", return_value="user@example.com"),
            patch.object(gz, "get_project", return_value="my-project"),
        ):
            ok, msg = gz.ensure_available()
        assert ok
        assert msg == ""
        assert "user@example.com" in gz._info
        assert "my-project" in gz._info


class TestGetProject:
    def test_from_env_var(self, monkeypatch):
        gz = _make()
        monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "env-project")
        assert gz.get_project() == "env-project"

    def test_from_cloudsdk_env(self, monkeypatch):
        gz = _make()
        monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
        monkeypatch.setenv("CLOUDSDK_CORE_PROJECT", "sdk-project")
        assert gz.get_project() == "sdk-project"

    def test_from_gcloud_config(self, monkeypatch):
        gz = _make()
        monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
        monkeypatch.delenv("CLOUDSDK_CORE_PROJECT", raising=False)
        monkeypatch.delenv("GCLOUD_PROJECT", raising=False)
        with patch.object(gz, "_run_integration", return_value=_ok("my-project\n")):
            assert gz.get_project() == "my-project"

    def test_returns_none_when_not_set(self, monkeypatch):
        gz = _make()
        for v in ["GOOGLE_CLOUD_PROJECT", "CLOUDSDK_CORE_PROJECT", "GCLOUD_PROJECT"]:
            monkeypatch.delenv(v, raising=False)
        with patch.object(gz, "_run_integration", return_value=_fail()):
            assert gz.get_project() is None


class TestGetAccount:
    def test_returns_account(self):
        gz = _make()
        with patch.object(gz, "_run_integration", return_value=_ok("user@example.com\n")):
            assert gz.get_account() == "user@example.com"

    def test_returns_none_when_not_set(self):
        gz = _make()
        with patch.object(gz, "_run_integration", return_value=_fail()):
            assert gz.get_account() is None


class TestGetAccessToken:
    def setup_method(self):
        GCloudCLIIntegration._token_cache = None

    def test_returns_token(self):
        gz = _make()
        with patch.object(gz, "_run_integration", return_value=_ok("ya29.token\n")):
            token = gz.get_access_token()
        assert token == "ya29.token"

    def test_cached_second_call(self):
        gz = _make()
        with patch.object(gz, "_run_integration", return_value=_ok("ya29.token")) as mock:
            gz.get_access_token()
            gz.get_access_token()
        assert mock.call_count == 1

    def test_clear_cache(self):
        gz = _make()
        with patch.object(gz, "_run_integration", return_value=_ok("ya29.token")) as mock:
            gz.get_access_token()
            gz.clear_token_cache()
            gz.get_access_token()
        assert mock.call_count == 2

    def test_returns_none_on_failure(self):
        gz = _make()
        with patch.object(gz, "_run_integration", return_value=_fail()):
            assert gz.get_access_token() is None


class TestRunGcloud:
    def test_passes_args(self):
        gz = _make()
        with patch.object(gz, "_run_integration", return_value=_ok("ok")) as mock:
            gz.run_gcloud(["config", "list"])
        assert mock.call_args[0][0] == ["config", "list"]


# ===========================================================================
# GCloudScript base class
# ===========================================================================


class TestGCloudScriptBase:
    def test_execute_calls_run(self):
        script = _SimpleScript()
        with pytest.raises(SystemExit) as exc:
            script.execute()
        assert script.ran
        assert exc.value.code == 0

    def test_execute_exits_1_on_exception(self):
        class Bad(GCloudScript):
            def run(self):
                raise RuntimeError("boom")

        with pytest.raises(SystemExit) as exc:
            Bad().execute()
        assert exc.value.code == 1

    def test_run_gcloud_calls_gcloud(self):
        script = _SimpleScript()
        with patch("subprocess.run", return_value=_cp(0)) as mock:
            script.run_gcloud(["config", "list"])
        assert mock.call_args[0][0] == ["gcloud", "config", "list"]

    def test_exit_on_failure_exits(self):
        script = _SimpleScript()
        with pytest.raises(SystemExit) as exc:
            script.exit_on_failure(_cp(1, stderr="err"), "test")
        assert exc.value.code == 1

    def test_project_from_env(self, monkeypatch):
        script = _SimpleScript()
        monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "my-proj")
        assert script.project() == "my-proj"

    def test_project_exits_when_not_set(self, monkeypatch):
        script = _SimpleScript()
        for v in ["GOOGLE_CLOUD_PROJECT", "CLOUDSDK_CORE_PROJECT", "GCLOUD_PROJECT"]:
            monkeypatch.delenv(v, raising=False)
        with patch("subprocess.run", return_value=_cp(1)):
            with pytest.raises(SystemExit):
                script.project()

    def test_builtin_scripts_dir_has_gcp_scripts(self):
        d = GCloudScript.builtin_scripts_dir()
        assert (d / "gcloud_gke_credentials.py").exists()
        assert (d / "gcloud_artifact_registry_login.py").exists()
        assert (d / "gcloud_gcs_bucket_ensure.py").exists()


# ===========================================================================
# Built-in scripts
# ===========================================================================


class TestGkeCredentials:
    def _load(self):
        from strata.data.scripts.gcloud_gke_credentials import GkeCredentials

        return GkeCredentials()

    def test_calls_get_credentials(self, monkeypatch):
        monkeypatch.setenv("GKE_CLUSTER", "my-cluster")
        monkeypatch.setenv("GKE_REGION", "us-central1")
        monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "my-proj")
        script = self._load()
        with patch.object(script, "run_gcloud", return_value=_cp(0)) as mock:
            script.run()
        args = mock.call_args[0][0]
        assert "get-credentials" in args
        assert "my-cluster" in args
        assert "--region" in args

    def test_zone_used_when_set(self, monkeypatch):
        monkeypatch.setenv("GKE_CLUSTER", "c")
        monkeypatch.setenv("GKE_ZONE", "us-central1-a")
        monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "p")
        script = self._load()
        with patch.object(script, "run_gcloud", return_value=_cp(0)) as mock:
            script.run()
        args = mock.call_args[0][0]
        assert "--zone" in args
        assert "--region" not in args

    def test_exits_when_cluster_missing(self, monkeypatch):
        monkeypatch.delenv("GKE_CLUSTER", raising=False)
        script = self._load()
        with pytest.raises(SystemExit):
            script.run()


class TestArtifactRegistryLogin:
    def _load(self):
        from strata.data.scripts.gcloud_artifact_registry_login import ArtifactRegistryLogin

        return ArtifactRegistryLogin()

    def test_configures_gar_host(self, monkeypatch):
        monkeypatch.setenv("GAR_LOCATION", "europe-west1")
        monkeypatch.delenv("GCR_ENABLE", raising=False)
        script = self._load()
        with patch.object(script, "run_gcloud", return_value=_cp(0)) as mock:
            script.run()
        args = mock.call_args[0][0]
        assert "configure-docker" in args
        assert "europe-west1-docker.pkg.dev" in " ".join(args)

    def test_gcr_included_when_enabled(self, monkeypatch):
        monkeypatch.setenv("GAR_LOCATION", "us")
        monkeypatch.setenv("GCR_ENABLE", "true")
        script = self._load()
        with patch.object(script, "run_gcloud", return_value=_cp(0)) as mock:
            script.run()
        args_str = " ".join(mock.call_args[0][0])
        assert "gcr.io" in args_str

    def test_exits_when_neither_set(self, monkeypatch):
        monkeypatch.delenv("GAR_LOCATION", raising=False)
        monkeypatch.delenv("GCR_ENABLE", raising=False)
        script = self._load()
        with pytest.raises(SystemExit):
            script.run()


class TestGcsBucketEnsure:
    def _load(self):
        from strata.data.scripts.gcloud_gcs_bucket_ensure import GcsBucketEnsure

        return GcsBucketEnsure()

    def test_calls_create_bucket(self, monkeypatch):
        monkeypatch.setenv("GCS_BUCKET", "my-bucket")
        monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "my-proj")
        script = self._load()
        calls = []
        with patch.object(script, "run_gcloud", side_effect=lambda a, **k: (calls.append(a), _cp(0))[1]):
            script.run()
        assert any("create" in " ".join(c) for c in calls)
        assert any("my-bucket" in " ".join(c) for c in calls)

    def test_no_fail_on_existing_flag(self, monkeypatch):
        monkeypatch.setenv("GCS_BUCKET", "existing")
        monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "p")
        script = self._load()
        with patch.object(script, "run_gcloud", return_value=_cp(0)) as mock:
            script.run()
        args = mock.call_args_list[0][0][0]
        assert "--no-fail-on-existing-bucket" in args

    def test_versioning_command_called(self, monkeypatch):
        monkeypatch.setenv("GCS_BUCKET", "b")
        monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "p")
        monkeypatch.setenv("GCS_VERSIONING", "true")
        script = self._load()
        calls = []
        with patch.object(script, "run_gcloud", side_effect=lambda a, **k: (calls.append(a), _cp(0))[1]):
            script.run()
        assert any("update" in " ".join(c) and "--versioning" in c for c in calls)

    def test_exits_when_bucket_missing(self, monkeypatch):
        monkeypatch.delenv("GCS_BUCKET", raising=False)
        script = self._load()
        with pytest.raises(SystemExit):
            script.run()
