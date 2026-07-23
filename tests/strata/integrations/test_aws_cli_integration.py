"""Tests for AWSCLIIntegration and AWSScript base class and built-in scripts."""

import json
import subprocess
from unittest.mock import MagicMock, patch

import pytest

try:
    from strata.integrations.aws_cli import AWSCLIIntegration
    from strata.models.integration_model import IntegrationModel
    from strata.utils.aws_script_base import AWSScript

    IMPL_MISSING = False
except ImportError:
    AWSCLIIntegration = None  # type: ignore[assignment,misc]
    AWSScript = None  # type: ignore[assignment,misc]
    IMPL_MISSING = True

pytestmark = pytest.mark.skipif(IMPL_MISSING, reason="AWS CLI integration not available")


# ===========================================================================
# Helpers
# ===========================================================================

_IDENTITY = json.dumps({
    "UserId": "AIDAIOSFODNN7EXAMPLE",
    "Account": "123456789012",
    "Arn": "arn:aws:iam::123456789012:user/Alice",
})


def _ok(stdout: str = ""):
    r = MagicMock(); r.returncode = 0; r.stdout = stdout; r.stderr = ""; return r

def _fail(stderr: str = "ERROR"):
    r = MagicMock(); r.returncode = 1; r.stdout = ""; r.stderr = stderr; return r

def _make_integration():
    return AWSCLIIntegration(IntegrationModel(name="aws", type="aws_cli"))

def _cp(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)

class _SimpleAWSScript(AWSScript):
    def __init__(self): self.ran = False
    def run(self): self.ran = True


# ===========================================================================
# AWSCLIIntegration
# ===========================================================================

class TestParseVersion:
    def test_parses_version(self):
        az = _make_integration()
        assert az.parse_version("aws-cli/2.15.0 Python/3.11.0") == "2.15.0"

    def test_fallback(self):
        az = _make_integration()
        assert az.parse_version("  2.15.0  ") == "2.15.0"


class TestEnsureAvailable:
    def test_not_installed(self):
        az = _make_integration()
        with patch.object(az, "is_available", return_value=False):
            ok, msg = az.ensure_available()
        assert not ok
        assert "not installed" in msg.lower()

    def test_installed_not_authenticated(self):
        az = _make_integration()
        with patch.object(az, "is_available", return_value=True), \
             patch.object(az, "_run_integration", return_value=_fail("InvalidClientTokenId")):
            ok, msg = az.ensure_available()
        assert not ok
        assert "aws configure" in msg or "authenticated" in msg.lower()

    def test_authenticated(self):
        az = _make_integration()
        def _side(args, **kwargs):
            if "get-caller-identity" in args:
                return _ok(_IDENTITY)
            return _ok("us-east-1")
        with patch.object(az, "is_available", return_value=True), \
             patch.object(az, "_run_integration", side_effect=_side):
            ok, msg = az.ensure_available()
        assert ok
        assert "123456789012" in az._info

    def test_info_includes_account_and_region(self):
        az = _make_integration()
        with patch.object(az, "is_available", return_value=True), \
             patch.object(az, "_run_integration", return_value=_ok(_IDENTITY)):
            az.ensure_available()
        assert "123456789012" in (az._info or "")


class TestGetIdentity:
    def test_returns_identity_dict(self):
        az = _make_integration()
        with patch.object(az, "_run_integration", return_value=_ok(_IDENTITY)):
            identity = az.get_identity()
        assert identity["Account"] == "123456789012"
        assert identity["Arn"].startswith("arn:")

    def test_returns_none_on_failure(self):
        az = _make_integration()
        with patch.object(az, "_run_integration", return_value=_fail()):
            assert az.get_identity() is None

    def test_returns_none_on_exception(self):
        az = _make_integration()
        with patch.object(az, "_run_integration", side_effect=RuntimeError("boom")):
            assert az.get_identity() is None


class TestGetRegion:
    def test_returns_env_var(self, monkeypatch):
        az = _make_integration()
        monkeypatch.setenv("AWS_DEFAULT_REGION", "eu-west-1")
        assert az.get_region() == "eu-west-1"

    def test_returns_aws_region_env(self, monkeypatch):
        az = _make_integration()
        monkeypatch.delenv("AWS_DEFAULT_REGION", raising=False)
        monkeypatch.setenv("AWS_REGION", "ap-southeast-1")
        assert az.get_region() == "ap-southeast-1"

    def test_falls_back_to_configure(self, monkeypatch):
        az = _make_integration()
        monkeypatch.delenv("AWS_DEFAULT_REGION", raising=False)
        monkeypatch.delenv("AWS_REGION", raising=False)
        with patch.object(az, "_run_integration", return_value=_ok("us-west-2\n")):
            assert az.get_region() == "us-west-2"

    def test_returns_none_when_not_configured(self, monkeypatch):
        az = _make_integration()
        monkeypatch.delenv("AWS_DEFAULT_REGION", raising=False)
        monkeypatch.delenv("AWS_REGION", raising=False)
        with patch.object(az, "_run_integration", return_value=_fail()):
            assert az.get_region() is None


class TestRunAws:
    def test_passthrough(self):
        az = _make_integration()
        with patch.object(az, "_run_integration", return_value=_ok("ok")) as mock:
            result = az.run_aws(["s3", "ls"])
        assert mock.call_args[0][0] == ["s3", "ls"]


# ===========================================================================
# AWSScript base class
# ===========================================================================

class TestAWSScriptBase:
    def test_execute_calls_run(self):
        script = _SimpleAWSScript()
        with pytest.raises(SystemExit) as exc:
            script.execute()
        assert script.ran
        assert exc.value.code == 0

    def test_execute_exits_1_on_exception(self):
        class Bad(AWSScript):
            def run(self): raise RuntimeError("boom")
        with pytest.raises(SystemExit) as exc:
            Bad().execute()
        assert exc.value.code == 1

    def test_run_aws_calls_aws(self):
        script = _SimpleAWSScript()
        with patch("subprocess.run", return_value=_cp(0)) as mock:
            script.run_aws(["s3", "ls"])
        assert mock.call_args[0][0] == ["aws", "s3", "ls"]

    def test_exit_on_failure_exits(self):
        script = _SimpleAWSScript()
        with pytest.raises(SystemExit) as exc:
            script.exit_on_failure(_cp(1, stderr="error"), "test")
        assert exc.value.code == 1

    def test_exit_on_failure_passes_on_success(self):
        script = _SimpleAWSScript()
        script.exit_on_failure(_cp(0, stdout="ok"), "test")  # no raise

    def test_require_env_exits_when_absent(self, monkeypatch):
        script = _SimpleAWSScript()
        monkeypatch.delenv("MISSING", raising=False)
        with pytest.raises(SystemExit):
            script.require_env("MISSING")

    def test_region_returns_env_var(self, monkeypatch):
        script = _SimpleAWSScript()
        monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
        assert script.region() == "us-east-1"

    def test_region_exits_when_not_set(self, monkeypatch):
        script = _SimpleAWSScript()
        monkeypatch.delenv("AWS_DEFAULT_REGION", raising=False)
        monkeypatch.delenv("AWS_REGION", raising=False)
        with patch("subprocess.run", return_value=_cp(1)):
            with pytest.raises(SystemExit):
                script.region()

    def test_builtin_scripts_dir_has_aws_scripts(self):
        d = AWSScript.builtin_scripts_dir()
        assert (d / "aws_eks_credentials.py").exists()
        assert (d / "aws_ecr_login.py").exists()
        assert (d / "aws_s3_bucket_ensure.py").exists()


# ===========================================================================
# Built-in scripts
# ===========================================================================

class TestEksCredentials:
    def _load(self):
        from strata.data.scripts.aws_eks_credentials import EksCredentials
        return EksCredentials()

    def test_calls_update_kubeconfig(self, monkeypatch):
        monkeypatch.setenv("EKS_CLUSTER", "my-cluster")
        monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
        script = self._load()
        with patch.object(script, "run_aws", return_value=_cp(0)) as mock:
            script.run()
        args = mock.call_args[0][0]
        assert "update-kubeconfig" in args
        assert "my-cluster" in args

    def test_role_arn_included(self, monkeypatch):
        monkeypatch.setenv("EKS_CLUSTER", "c")
        monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
        monkeypatch.setenv("EKS_ROLE_ARN", "arn:aws:iam::123:role/EksRole")
        script = self._load()
        with patch.object(script, "run_aws", return_value=_cp(0)) as mock:
            script.run()
        args = mock.call_args[0][0]
        assert "--role-arn" in args

    def test_exits_when_cluster_missing(self, monkeypatch):
        monkeypatch.delenv("EKS_CLUSTER", raising=False)
        script = self._load()
        with pytest.raises(SystemExit):
            script.run()


class TestEcrLogin:
    def _load(self):
        from strata.data.scripts.aws_ecr_login import EcrLogin
        return EcrLogin()

    def test_calls_get_login_password_and_docker_login(self, monkeypatch):
        monkeypatch.setenv("ECR_REGISTRY", "123.dkr.ecr.us-east-1.amazonaws.com")
        monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
        script = self._load()
        with patch.object(script, "run_aws", return_value=_cp(0, stdout="password")) as mock_aws, \
             patch("subprocess.run", return_value=_cp(0, stdout="Login Succeeded")) as mock_docker:
            script.run()
        mock_aws.assert_called_once()
        assert mock_docker.call_args[0][0][0] == "docker"

    def test_constructs_registry_from_account_and_region(self, monkeypatch):
        monkeypatch.delenv("ECR_REGISTRY", raising=False)
        monkeypatch.setenv("ECR_ACCOUNT_ID", "999000111")
        monkeypatch.setenv("AWS_DEFAULT_REGION", "eu-west-1")
        script = self._load()
        with patch.object(script, "run_aws", return_value=_cp(0, stdout="pw")), \
             patch("subprocess.run", return_value=_cp(0)) as mock_docker:
            script.run()
        # docker login should have been called with the constructed registry URL
        docker_args = mock_docker.call_args[0][0]
        assert "docker" in docker_args
        assert "999000111.dkr.ecr.eu-west-1.amazonaws.com" in docker_args


class TestS3BucketEnsure:
    def _load(self):
        from strata.data.scripts.aws_s3_bucket_ensure import S3BucketEnsure
        return S3BucketEnsure()

    def test_calls_create_bucket(self, monkeypatch):
        monkeypatch.setenv("S3_BUCKET", "my-bucket")
        monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
        script = self._load()
        with patch.object(script, "run_aws", return_value=_cp(0, stdout="{}")) as mock:
            script.run()
        first_call_args = mock.call_args_list[0][0][0]
        assert "create-bucket" in first_call_args
        assert "my-bucket" in first_call_args

    def test_bucket_already_exists_is_ok(self, monkeypatch):
        monkeypatch.setenv("S3_BUCKET", "existing-bucket")
        monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
        script = self._load()
        with patch.object(script, "run_aws", return_value=_cp(1, stderr="BucketAlreadyOwnedByYou")):
            script.run()  # should not raise

    def test_versioning_called_when_enabled(self, monkeypatch):
        monkeypatch.setenv("S3_BUCKET", "b")
        monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
        monkeypatch.setenv("S3_VERSIONING", "true")
        script = self._load()
        calls = []
        with patch.object(script, "run_aws", side_effect=lambda a, **k: (calls.append(a), _cp(0, "{}"))[1]):
            script.run()
        assert any("put-bucket-versioning" in " ".join(c) for c in calls)

    def test_tags_applied(self, monkeypatch):
        monkeypatch.setenv("S3_BUCKET", "b")
        monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
        monkeypatch.setenv("S3_TAGS", "env=prod,team=platform")
        script = self._load()
        calls = []
        with patch.object(script, "run_aws", side_effect=lambda a, **k: (calls.append(a), _cp(0, "{}"))[1]):
            script.run()
        assert any("put-bucket-tagging" in " ".join(c) for c in calls)

    def test_exits_when_bucket_missing(self, monkeypatch):
        monkeypatch.delenv("S3_BUCKET", raising=False)
        script = self._load()
        with pytest.raises(SystemExit):
            script.run()
