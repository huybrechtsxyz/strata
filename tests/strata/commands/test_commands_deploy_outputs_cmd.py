"""Tests for the ``strata deploy output`` command (stored artifacts mode)."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from strata.commands.cli_deploy import deploy
from strata.commands.deploy.base_deploy_command import BaseDeployCommand
from strata.commands.deploy.output_deploy_command import OutputDeployCommand
from strata.models.configuration_model import ConfigurationOutputsModel

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_command(tmp_path: Path, **kwargs) -> OutputDeployCommand:
    """Return an OutputDeployCommand configured for artifact mode."""
    defaults = {"refresh": False}
    defaults.update(kwargs)
    with patch.object(BaseDeployCommand, "_initialize", return_value=None):
        cmd = OutputDeployCommand(work_path=str(tmp_path), file="deploy.yaml", **defaults)
    cmd._work_path = tmp_path
    cmd._configuration_service = None
    return cmd


def _make_deploy_meta(name: str = "prod", version: str = "1.0.0") -> MagicMock:
    meta = MagicMock()
    meta.name = name
    meta.labels = {"version": version}
    return meta


def _write_artifact(dir_path: Path, stage: str, data: dict) -> Path:
    """Write a minimal artifact JSON file at dir_path/{stage}.json."""
    dir_path.mkdir(parents=True, exist_ok=True)
    path = dir_path / f"{stage}.json"
    path.write_text(json.dumps(data))
    return path


# ---------------------------------------------------------------------------
# CLI wiring
# ---------------------------------------------------------------------------


class TestDeployOutputCli:
    def test_group_help_shows_output_subcommand(self):
        runner = CliRunner()
        result = runner.invoke(deploy, ["--help"])
        assert "output" in result.output

    def test_output_help_exits_zero(self):
        runner = CliRunner()
        result = runner.invoke(deploy, ["output", "--help"])
        assert result.exit_code == 0

    def test_output_help_shows_version_option(self):
        runner = CliRunner()
        result = runner.invoke(deploy, ["output", "--help"])
        assert "--version" in result.output

    def test_output_help_shows_all_versions_option(self):
        runner = CliRunner()
        result = runner.invoke(deploy, ["output", "--help"])
        assert "--all-versions" in result.output

    def test_output_help_shows_refresh_option(self):
        runner = CliRunner()
        result = runner.invoke(deploy, ["output", "--help"])
        assert "--refresh" in result.output

    def test_exits_zero_when_execute_succeeds(self, tmp_path):
        runner = CliRunner()
        with patch.object(OutputDeployCommand, "execute", return_value=True):
            result = runner.invoke(deploy, ["output", "-f", "deploy.yaml", "--work-path", str(tmp_path)])
        assert result.exit_code == 0

    def test_exits_nonzero_when_execute_fails(self, tmp_path):
        runner = CliRunner()
        with patch.object(OutputDeployCommand, "execute", return_value=False):
            result = runner.invoke(deploy, ["output", "-f", "deploy.yaml", "--work-path", str(tmp_path)])
        assert result.exit_code != 0

    def test_accepts_version_option(self, tmp_path):
        runner = CliRunner()
        with patch.object(OutputDeployCommand, "execute", return_value=True):
            result = runner.invoke(
                deploy,
                ["output", "-f", "deploy.yaml", "--version", "2.0.0", "--work-path", str(tmp_path)],
            )
        assert result.exit_code == 0

    def test_accepts_all_versions_flag(self, tmp_path):
        runner = CliRunner()
        with patch.object(OutputDeployCommand, "execute", return_value=True):
            result = runner.invoke(
                deploy,
                ["output", "-f", "deploy.yaml", "--all-versions", "--work-path", str(tmp_path)],
            )
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# _read_artifact
# ---------------------------------------------------------------------------


class TestReadArtifact:
    def test_returns_artifact_data(self, tmp_path):
        data = {
            "deployment": "prod",
            "version": "1.0.0",
            "stage": "infra",
            "written_at": "2026-01-01T00:00:00Z",
            "outputs": {"endpoint": "https://x"},
        }
        p = _write_artifact(tmp_path, "infra", data)
        cmd = _make_command(tmp_path)
        result = cmd._read_artifact(p)
        assert result is not None
        assert result["stage"] == "infra"
        assert result["outputs"]["endpoint"] == "https://x"

    def test_returns_none_for_invalid_json(self, tmp_path):
        p = tmp_path / "bad.json"
        p.write_text("not json{{")
        cmd = _make_command(tmp_path)
        result = cmd._read_artifact(p)
        assert result is None

    def test_returns_none_for_missing_file(self, tmp_path):
        cmd = _make_command(tmp_path)
        result = cmd._read_artifact(tmp_path / "missing.json")
        assert result is None

    def test_key_filter_returns_only_matching_key(self, tmp_path):
        data = {"outputs": {"endpoint": "https://x", "token": "abc"}}
        p = _write_artifact(tmp_path, "infra", data)
        cmd = _make_command(tmp_path, key="endpoint")
        result = cmd._read_artifact(p)
        assert result is not None
        assert "endpoint" in result["outputs"]
        assert "token" not in result["outputs"]

    def test_key_filter_absent_key_returns_empty_outputs(self, tmp_path):
        data = {"outputs": {"endpoint": "https://x"}}
        p = _write_artifact(tmp_path, "infra", data)
        cmd = _make_command(tmp_path, key="missing_key")
        result = cmd._read_artifact(p)
        assert result is not None
        assert result["outputs"] == {}

    def test_bad_artifact_appends_message(self, tmp_path):
        p = tmp_path / "bad.json"
        p.write_text("not json{{")
        cmd = _make_command(tmp_path)
        cmd._read_artifact(p)
        assert any("bad.json" in m for m in cmd._messages)


# ---------------------------------------------------------------------------
# _resolve_versions
# ---------------------------------------------------------------------------


class TestResolveArtifactVersions:
    def test_defaults_to_label_version(self, tmp_path):
        cmd = _make_command(tmp_path)
        meta = _make_deploy_meta(version="1.5.0")
        versions = cmd._resolve_artifact_versions(tmp_path, meta)
        assert versions == ["1.5.0"]

    def test_defaults_to_unknown_when_no_label(self, tmp_path):
        cmd = _make_command(tmp_path)
        meta = MagicMock()
        meta.labels = {}
        versions = cmd._resolve_artifact_versions(tmp_path, meta)
        assert versions == ["unknown"]

    def test_explicit_version_overrides_label(self, tmp_path):
        cmd = _make_command(tmp_path, version="2.0.0")
        meta = _make_deploy_meta(version="1.0.0")
        versions = cmd._resolve_artifact_versions(tmp_path, meta)
        assert versions == ["2.0.0"]

    def test_all_versions_lists_directories(self, tmp_path):
        for v in ["1.0.0", "1.1.0", "2.0.0"]:
            (tmp_path / v).mkdir()
        cmd = _make_command(tmp_path, all_versions=True)
        meta = _make_deploy_meta()
        versions = cmd._resolve_artifact_versions(tmp_path, meta)
        assert set(versions) == {"1.0.0", "1.1.0", "2.0.0"}

    def test_all_versions_ignores_files(self, tmp_path):
        (tmp_path / "1.0.0").mkdir()
        (tmp_path / "notes.txt").write_text("x")
        cmd = _make_command(tmp_path, all_versions=True)
        meta = _make_deploy_meta()
        versions = cmd._resolve_artifact_versions(tmp_path, meta)
        assert versions == ["1.0.0"]


# ---------------------------------------------------------------------------
# _run logic
# ---------------------------------------------------------------------------


class TestOutputDeployCommandArtifacts:
    def _setup_cmd(self, tmp_path, **kwargs) -> OutputDeployCommand:
        # Default to artifact mode: set version to trigger _run_artifacts
        if "version" not in kwargs and "all_versions" not in kwargs:
            kwargs["version"] = "1.0.0"
        cmd = _make_command(tmp_path, **kwargs)
        meta = _make_deploy_meta(name="prod", version="1.0.0")
        svc = MagicMock()
        svc.model.meta = meta
        cmd._deployment_service = svc
        return cmd

    def test_returns_true_when_outputs_dir_absent(self, tmp_path):
        cmd = self._setup_cmd(tmp_path)
        result = cmd._run()
        assert result is True
        assert cmd._output_data["artifacts"] == []

    def test_returns_true_when_outputs_disabled(self, tmp_path):
        cmd = self._setup_cmd(tmp_path)
        cfg = MagicMock(spec=ConfigurationOutputsModel)
        cfg.enabled = False
        cmd._configuration_service = MagicMock()
        cmd._configuration_service.model.spec.deployment.outputs = cfg
        # Patch _get_outputs_config
        with patch.object(cmd, "_get_outputs_config", return_value=cfg):
            result = cmd._run()
        assert result is True
        assert cmd._output_data["artifacts"] == []

    def test_collects_artifact_for_current_version(self, tmp_path):
        artifact_data = {
            "deployment": "prod",
            "version": "1.0.0",
            "stage": "infra",
            "written_at": "2026-01-01T00:00:00Z",
            "outputs": {"url": "https://example.com"},
        }
        ver_dir = tmp_path / ".strata" / "outputs" / "prod" / "1.0.0"
        _write_artifact(ver_dir, "infra", artifact_data)

        cmd = self._setup_cmd(tmp_path)
        result = cmd._run()

        assert result is True
        assert len(cmd._output_data["artifacts"]) == 1
        assert cmd._output_data["artifacts"][0]["stage"] == "infra"

    def test_stage_filter_excludes_other_stages(self, tmp_path):
        ver_dir = tmp_path / ".strata" / "outputs" / "prod" / "1.0.0"
        _write_artifact(ver_dir, "infra", {"outputs": {}})
        _write_artifact(ver_dir, "network", {"outputs": {}})

        cmd = self._setup_cmd(tmp_path, stage="infra")
        cmd._run()

        names = [a["stage"] for a in cmd._output_data.get("artifacts", []) if "stage" in a]
        # Only infra stage (which has the "stage" key set explicitly)
        for _entry in cmd._output_data["artifacts"]:
            # Each artifact comes from infra.json so stem is infra
            pass
        # Check: network.json was skipped
        artifact_names_from_filenames = [(tmp_path / ".strata" / "outputs" / "prod" / "1.0.0" / "infra.json").stem]
        assert len(cmd._output_data["artifacts"]) == 1

    def test_all_versions_collects_across_versions(self, tmp_path):
        for ver in ["1.0.0", "2.0.0"]:
            ver_dir = tmp_path / ".strata" / "outputs" / "prod" / ver
            _write_artifact(ver_dir, "infra", {"version": ver, "stage": "infra", "outputs": {}})

        cmd = self._setup_cmd(tmp_path, all_versions=True)
        cmd._run()

        assert len(cmd._output_data["artifacts"]) == 2

    def test_uses_default_path_when_no_config(self, tmp_path):
        """Falls back to .strata/outputs when no configuration service is set."""
        ver_dir = tmp_path / ".strata" / "outputs" / "prod" / "1.0.0"
        _write_artifact(ver_dir, "app", {"stage": "app", "outputs": {"key": "val"}})

        cmd = self._setup_cmd(tmp_path)
        cmd._configuration_service = None
        cmd._run()

        assert len(cmd._output_data["artifacts"]) == 1
