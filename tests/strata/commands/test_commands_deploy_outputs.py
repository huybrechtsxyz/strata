"""Unit tests for BaseDeployCommand._write_outputs_artifact and _record_stage_result."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from strata.commands.deploy.base_deploy_command import BaseDeployCommand
from strata.models.configuration_model import ConfigurationOutputsModel, SensitiveOutputHandling
from strata.models.deployment_manifest_model import ManifestOutputsReferenceModel

# ---------------------------------------------------------------------------
# Concrete subclass for testing the abstract base
# ---------------------------------------------------------------------------


class _ConcreteDeployCommand(BaseDeployCommand):
    """Minimal concrete subclass for testing BaseDeployCommand methods."""

    def execute(self) -> bool:
        return True

    def _run_execution(self) -> bool:
        return True


def _make_command(tmp_path: Path) -> _ConcreteDeployCommand:
    """Return a command instance with work_path set and services stubbed."""
    with patch.object(BaseDeployCommand, "_initialize", return_value=None):
        cmd = _ConcreteDeployCommand(work_path=str(tmp_path))
    cmd._work_path = tmp_path
    cmd._configuration_service = None
    cmd._deployment_service = None
    return cmd


def _stub_deployment_service(name: str = "my_deploy", version: str = "1.0.0") -> MagicMock:
    svc = MagicMock()
    svc.model.meta.name = name
    svc.model.meta.labels = {"version": version}
    return svc


def _stub_configuration_service(outputs_model: ConfigurationOutputsModel | None) -> MagicMock:
    svc = MagicMock()
    svc.model.spec.deployment.outputs = outputs_model
    return svc


# ---------------------------------------------------------------------------
# Tests: skipping / no-op conditions
# ---------------------------------------------------------------------------


class TestWriteOutputsArtifactSkip:
    def test_no_config_returns_none(self, tmp_path):
        cmd = _make_command(tmp_path)
        cmd._configuration_service = None
        result = cmd._write_outputs_artifact("infra", {"ip": "1.2.3.4"}, {})
        assert result is None

    def test_config_absent_on_deployment_spec_returns_none(self, tmp_path):
        cmd = _make_command(tmp_path)
        svc = MagicMock()
        svc.model.spec.deployment = None
        cmd._configuration_service = svc
        result = cmd._write_outputs_artifact("infra", {"ip": "1.2.3.4"}, {})
        assert result is None

    def test_outputs_field_none_returns_none(self, tmp_path):
        cmd = _make_command(tmp_path)
        cmd._configuration_service = _stub_configuration_service(None)
        result = cmd._write_outputs_artifact("infra", {"ip": "1.2.3.4"}, {})
        assert result is None

    def test_enabled_false_returns_none(self, tmp_path):
        cmd = _make_command(tmp_path)
        cfg = ConfigurationOutputsModel(enabled=False)
        cmd._configuration_service = _stub_configuration_service(cfg)
        cmd._deployment_service = _stub_deployment_service()
        result = cmd._write_outputs_artifact("infra", {"ip": "1.2.3.4"}, {})
        assert result is None
        # No file written
        assert not list(tmp_path.rglob("*.json"))

    def test_no_deployment_service_returns_none(self, tmp_path):
        cmd = _make_command(tmp_path)
        cmd._configuration_service = _stub_configuration_service(ConfigurationOutputsModel())
        cmd._deployment_service = None
        result = cmd._write_outputs_artifact("infra", {"ip": "1.2.3.4"}, {})
        assert result is None


# ---------------------------------------------------------------------------
# Tests: file content — REDACT mode
# ---------------------------------------------------------------------------


class TestWriteOutputsArtifactRedact:
    def test_writes_file_at_expected_path(self, tmp_path):
        cmd = _make_command(tmp_path)
        cmd._configuration_service = _stub_configuration_service(ConfigurationOutputsModel())
        cmd._deployment_service = _stub_deployment_service("prod_deploy", "2.0.0")

        result = cmd._write_outputs_artifact("network", {"cidr": "10.0.0.0/16"}, {})

        assert result is not None
        expected = tmp_path / ".strata" / "outputs" / "prod_deploy" / "2.0.0" / "network.json"
        assert result == expected
        assert expected.exists()

    def test_file_structure(self, tmp_path):
        cmd = _make_command(tmp_path)
        cmd._configuration_service = _stub_configuration_service(ConfigurationOutputsModel())
        cmd._deployment_service = _stub_deployment_service("my_deploy", "1.0.0")

        cmd._write_outputs_artifact("infra", {"server_ip": "192.168.1.1"}, {"db_pass": "secret"})

        artifact = tmp_path / ".strata" / "outputs" / "my_deploy" / "1.0.0" / "infra.json"
        data = json.loads(artifact.read_text())
        assert data["deployment"] == "my_deploy"
        assert data["version"] == "1.0.0"
        assert data["stage"] == "infra"
        assert "written_at" in data
        assert "outputs" in data

    def test_non_sensitive_stored_as_is(self, tmp_path):
        cmd = _make_command(tmp_path)
        cmd._configuration_service = _stub_configuration_service(ConfigurationOutputsModel())
        cmd._deployment_service = _stub_deployment_service()

        cmd._write_outputs_artifact("infra", {"server_ip": "10.0.0.5", "port": 443}, {})

        artifact = tmp_path / ".strata" / "outputs" / "my_deploy" / "1.0.0" / "infra.json"
        data = json.loads(artifact.read_text())
        assert data["outputs"]["server_ip"] == "10.0.0.5"
        assert data["outputs"]["port"] == 443

    def test_sensitive_keys_redacted_with_placeholder(self, tmp_path):
        cmd = _make_command(tmp_path)
        cmd._configuration_service = _stub_configuration_service(
            ConfigurationOutputsModel(sensitive=SensitiveOutputHandling.REDACT)
        )
        cmd._deployment_service = _stub_deployment_service()

        cmd._write_outputs_artifact(
            "infra",
            {"server_ip": "10.0.0.5"},
            {"db_password": "super_secret", "api_key": "tok_123"},
        )

        artifact = tmp_path / ".strata" / "outputs" / "my_deploy" / "1.0.0" / "infra.json"
        data = json.loads(artifact.read_text())
        assert data["outputs"]["server_ip"] == "10.0.0.5"
        assert data["outputs"]["db_password"] == "(sensitive)"
        assert data["outputs"]["api_key"] == "(sensitive)"

    def test_sensitive_keys_visible_in_redact_mode(self, tmp_path):
        """Keys are present in output — just the value is replaced."""
        cmd = _make_command(tmp_path)
        cmd._configuration_service = _stub_configuration_service(ConfigurationOutputsModel())
        cmd._deployment_service = _stub_deployment_service()

        cmd._write_outputs_artifact("infra", {}, {"hidden_token": "abc"})

        artifact = tmp_path / ".strata" / "outputs" / "my_deploy" / "1.0.0" / "infra.json"
        data = json.loads(artifact.read_text())
        assert "hidden_token" in data["outputs"]
        assert data["outputs"]["hidden_token"] == "(sensitive)"

    def test_no_sensitive_outputs(self, tmp_path):
        cmd = _make_command(tmp_path)
        cmd._configuration_service = _stub_configuration_service(ConfigurationOutputsModel())
        cmd._deployment_service = _stub_deployment_service()

        cmd._write_outputs_artifact("infra", {"server_ip": "1.2.3.4"}, {})

        artifact = tmp_path / ".strata" / "outputs" / "my_deploy" / "1.0.0" / "infra.json"
        data = json.loads(artifact.read_text())
        assert list(data["outputs"].keys()) == ["server_ip"]

    def test_empty_outputs_writes_empty_dict(self, tmp_path):
        cmd = _make_command(tmp_path)
        cmd._configuration_service = _stub_configuration_service(ConfigurationOutputsModel())
        cmd._deployment_service = _stub_deployment_service()

        result = cmd._write_outputs_artifact("infra", {}, {})

        assert result is not None
        data = json.loads(result.read_text())
        assert data["outputs"] == {}


# ---------------------------------------------------------------------------
# Tests: file content — OMIT mode
# ---------------------------------------------------------------------------


class TestWriteOutputsArtifactOmit:
    def test_sensitive_keys_omitted(self, tmp_path):
        cmd = _make_command(tmp_path)
        cmd._configuration_service = _stub_configuration_service(
            ConfigurationOutputsModel(sensitive=SensitiveOutputHandling.OMIT)
        )
        cmd._deployment_service = _stub_deployment_service()

        cmd._write_outputs_artifact(
            "infra",
            {"server_ip": "10.0.0.5"},
            {"db_password": "secret"},
        )

        artifact = tmp_path / ".strata" / "outputs" / "my_deploy" / "1.0.0" / "infra.json"
        data = json.loads(artifact.read_text())
        assert "db_password" not in data["outputs"]
        assert data["outputs"]["server_ip"] == "10.0.0.5"

    def test_only_sensitive_outputs_gives_empty_dict(self, tmp_path):
        cmd = _make_command(tmp_path)
        cmd._configuration_service = _stub_configuration_service(
            ConfigurationOutputsModel(sensitive=SensitiveOutputHandling.OMIT)
        )
        cmd._deployment_service = _stub_deployment_service()

        cmd._write_outputs_artifact("infra", {}, {"secret": "val"})

        artifact = tmp_path / ".strata" / "outputs" / "my_deploy" / "1.0.0" / "infra.json"
        data = json.loads(artifact.read_text())
        assert data["outputs"] == {}


# ---------------------------------------------------------------------------
# Tests: path and version behaviour
# ---------------------------------------------------------------------------


class TestWriteOutputsArtifactPaths:
    def test_custom_base_path(self, tmp_path):
        cmd = _make_command(tmp_path)
        cmd._configuration_service = _stub_configuration_service(ConfigurationOutputsModel(path="deploy/outputs"))
        cmd._deployment_service = _stub_deployment_service("alpha", "3.0.0")

        result = cmd._write_outputs_artifact("stage_a", {}, {})

        expected = tmp_path / "deploy" / "outputs" / "alpha" / "3.0.0" / "stage_a.json"
        assert result == expected
        assert expected.exists()

    def test_version_unknown_when_label_missing(self, tmp_path):
        cmd = _make_command(tmp_path)
        cmd._configuration_service = _stub_configuration_service(ConfigurationOutputsModel())
        svc = MagicMock()
        svc.model.meta.name = "no_version_deploy"
        svc.model.meta.labels = {}  # no version label
        cmd._deployment_service = svc

        result = cmd._write_outputs_artifact("infra", {}, {})

        assert result is not None
        assert "unknown" in str(result)

    def test_creates_parent_directories(self, tmp_path):
        cmd = _make_command(tmp_path)
        cmd._configuration_service = _stub_configuration_service(ConfigurationOutputsModel(path="deep/nested/path"))
        cmd._deployment_service = _stub_deployment_service("my_deploy", "1.0.0")

        result = cmd._write_outputs_artifact("infra", {}, {})

        assert result is not None
        assert result.exists()

    def test_multiple_stages_write_separate_files(self, tmp_path):
        cmd = _make_command(tmp_path)
        cmd._configuration_service = _stub_configuration_service(ConfigurationOutputsModel())
        cmd._deployment_service = _stub_deployment_service("prod", "1.0.0")

        result_a = cmd._write_outputs_artifact("network", {"cidr": "10.0.0.0/16"}, {})
        result_b = cmd._write_outputs_artifact("compute", {"server_ip": "10.0.0.5"}, {})

        assert result_a != result_b
        assert result_a is not None and result_a.exists()
        assert result_b is not None and result_b.exists()


# ---------------------------------------------------------------------------
# Tests: non-fatal on write error
# ---------------------------------------------------------------------------


class TestWriteOutputsArtifactNonFatal:
    def test_returns_none_on_os_error(self, tmp_path):
        cmd = _make_command(tmp_path)
        cmd._configuration_service = _stub_configuration_service(ConfigurationOutputsModel())
        cmd._deployment_service = _stub_deployment_service()

        with patch("builtins.open", side_effect=OSError("disk full")):
            result = cmd._write_outputs_artifact("infra", {"ip": "1.2.3.4"}, {})

        assert result is None

    def test_no_exception_propagated_on_error(self, tmp_path):
        cmd = _make_command(tmp_path)
        cmd._configuration_service = _stub_configuration_service(ConfigurationOutputsModel())
        cmd._deployment_service = _stub_deployment_service()

        with patch("builtins.open", side_effect=PermissionError("read-only")):
            # Must not raise
            cmd._write_outputs_artifact("infra", {"ip": "1.2.3.4"}, {})


# ---------------------------------------------------------------------------
# Tests: _record_stage_result with outputs_artifact
# ---------------------------------------------------------------------------


class TestRecordStageResultOutputsArtifact:
    def test_outputs_artifact_none_by_default(self, tmp_path):
        cmd = _make_command(tmp_path)
        cmd._record_stage_result(
            stage_name="infra",
            provisioner="tf_x",
            topology=None,
            status="success",
            started_at="2026-01-01T00:00:00+00:00",
            completed_at="2026-01-01T00:01:00+00:00",
        )
        assert len(cmd._stage_results) == 1
        assert cmd._stage_results[0].outputs_artifact is None

    def test_outputs_artifact_recorded(self, tmp_path):
        cmd = _make_command(tmp_path)
        ref = ManifestOutputsReferenceModel(
            path=".strata/outputs/prod/1.0.0/infra.json",
            stage="infra",
            version="1.0.0",
            written_at="2026-01-01T00:00:00+00:00",
        )
        cmd._record_stage_result(
            stage_name="infra",
            provisioner="tf_x",
            topology=None,
            status="success",
            started_at="2026-01-01T00:00:00+00:00",
            completed_at="2026-01-01T00:01:00+00:00",
            outputs_artifact=ref,
        )
        assert cmd._stage_results[0].outputs_artifact is not None
        assert cmd._stage_results[0].outputs_artifact.path == ".strata/outputs/prod/1.0.0/infra.json"
        assert cmd._stage_results[0].outputs_artifact.version == "1.0.0"

    def test_multiple_stages_each_get_own_artifact(self, tmp_path):
        cmd = _make_command(tmp_path)
        for stage in ("infra", "network"):
            ref = ManifestOutputsReferenceModel(
                path=f".strata/outputs/prod/1.0.0/{stage}.json",
                stage=stage,
                version="1.0.0",
                written_at="2026-01-01T00:00:00+00:00",
            )
            cmd._record_stage_result(
                stage_name=stage,
                provisioner="tf_x",
                topology=None,
                status="success",
                started_at="2026-01-01T00:00:00+00:00",
                completed_at="2026-01-01T00:01:00+00:00",
                outputs_artifact=ref,
            )
        assert cmd._stage_results[0].outputs_artifact.stage == "infra"
        assert cmd._stage_results[1].outputs_artifact.stage == "network"
