"""CLI tests for new ``strata promote <ring> <file>`` interface (ADR-0011)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from strata.commands.cli_promote import promote_group

_API_VERSION = "strata.huybrechts.xyz/v1"

_CONFIG_YAML = """\
apiVersion: strata.huybrechts.xyz/v1
kind: configuration
meta:
  name: test-config
spec:
  promotions:
    progressions:
      - name: standard
        rings:
          - name: dev
            environments: [dev1]
          - name: prd
            environments: [prd1]
    strategies:
      - name: app-wave
        type: image
        progression: standard
        versions_path: versions/app/
"""

_VERSION_YAML = """\
apiVersion: strata.huybrechts.xyz/v1
kind: version
meta:
  name: v2-1-0
spec:
  ring: dev
  pins:
    images:
      app: v2.1.0
"""


def _make_workspace(tmp_path: Path) -> tuple[Path, Path]:
    (tmp_path / ".strata").mkdir()
    (tmp_path / ".strata" / "configuration.yaml").write_text(_CONFIG_YAML)
    (tmp_path / "versions" / "app").mkdir(parents=True)
    vf = tmp_path / "versions" / "app" / "v2.1.0.yaml"
    vf.write_text(_VERSION_YAML)
    return tmp_path, vf


# ── new promote <ring> <file> --promotion ─────────────────────────────────────


def _patch_run_promote(result: dict):
    """Return a context manager that patches PromoteController.run_promote."""
    return patch(
        "strata.commands.promote.run_promote_command.PromoteController",
        autospec=True,
    )


class TestPromoteNewInterface:
    def _mock_success(self, ring="dev"):
        """Return a mock PromoteController that succeeds."""
        mock = MagicMock()
        mock.return_value.has_errors.return_value = False
        mock.return_value.get_errors.return_value = []
        mock.return_value.run_promote.return_value = {
            "dry_run": False,
            "branch": f"promote/app-wave-{ring}-v2-1-0",
            "commit_sha": "abc123",
            "promotion": "app-wave",
            "ring": ring,
            "version_file": "versions/app/v2.1.0.yaml",
            "versions_path": "versions/app/",
            "files_written": [f"versions/app/{ring}.lock.yaml"],
            "files_deleted": [],
            "pr_suggestion": f"gh pr create --head promote/app-wave-{ring}-v2-1-0",
        }
        return mock

    def _mock_dry_run(self, ring="dev", wave=None, files_to_delete=None):
        mock = MagicMock()
        mock.return_value.has_errors.return_value = False
        mock.return_value.get_errors.return_value = []
        files_to_write = [f"versions/app/{ring}.wave.{wave}.lock.yaml" if wave else f"versions/app/{ring}.lock.yaml"]
        mock.return_value.run_promote.return_value = {
            "dry_run": True,
            "branch": f"promote/app-wave-{ring}",
            "promotion": "app-wave",
            "ring": ring,
            "version_file": "versions/app/v2.1.0.yaml",
            "files_to_write": files_to_write,
            "files_to_delete": files_to_delete or [],
            "versions_path": "versions/app/",
        }
        return mock

    def _mock_failure(self, error: str):
        mock = MagicMock()
        mock.return_value.has_errors.return_value = True
        mock.return_value.get_errors.return_value = [error]
        mock.return_value.run_promote.return_value = {}
        return mock

    def test_no_ring_and_file_shows_help(self, tmp_path):
        runner = CliRunner()
        result = runner.invoke(promote_group, ["--work-path", str(tmp_path)])
        assert result.exit_code == 0
        assert "--ring" in result.output or "promote" in result.output.lower()

    def test_missing_promotion_flag_exits_with_usage_error(self, tmp_path):
        _, vf = _make_workspace(tmp_path)
        runner = CliRunner()
        result = runner.invoke(promote_group, ["--ring", "dev", "--file", str(vf), "--work-path", str(tmp_path)])
        assert result.exit_code == 2
        assert "--promotion" in result.output

    def test_successful_promote_exits_0(self, tmp_path):
        _, vf = _make_workspace(tmp_path)
        runner = CliRunner()
        with patch("strata.commands.promote.run_promote_command.PromoteController", self._mock_success("dev")):
            result = runner.invoke(
                promote_group,
                ["--ring", "dev", "--file", str(vf), "--promotion", "app-wave", "--work-path", str(tmp_path)],
            )
        assert result.exit_code == 0, result.output

    def test_successful_promote_json_output(self, tmp_path):
        _, vf = _make_workspace(tmp_path)
        runner = CliRunner()
        with patch("strata.commands.promote.run_promote_command.PromoteController", self._mock_success("dev")):
            result = runner.invoke(
                promote_group,
                [
                    "--ring",
                    "dev",
                    "--file",
                    str(vf),
                    "--promotion",
                    "app-wave",
                    "--output",
                    "json",
                    "--work-path",
                    str(tmp_path),
                ],
            )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["success"] is True

    def test_dry_run_exits_0(self, tmp_path):
        _, vf = _make_workspace(tmp_path)
        runner = CliRunner()
        with patch("strata.commands.promote.run_promote_command.PromoteController", self._mock_dry_run("dev")):
            result = runner.invoke(
                promote_group,
                [
                    "--ring",
                    "dev",
                    "--file",
                    str(vf),
                    "--promotion",
                    "app-wave",
                    "--dry-run",
                    "--work-path",
                    str(tmp_path),
                ],
            )
        assert result.exit_code == 0, result.output

    def test_wave_option_passes_to_controller(self, tmp_path):
        _, vf = _make_workspace(tmp_path)
        mock = self._mock_dry_run("dev", wave=2)
        runner = CliRunner()
        with patch("strata.commands.promote.run_promote_command.PromoteController", mock):
            result = runner.invoke(
                promote_group,
                [
                    "--ring",
                    "dev",
                    "--file",
                    str(vf),
                    "--promotion",
                    "app-wave",
                    "--wave",
                    "2",
                    "--dry-run",
                    "--work-path",
                    str(tmp_path),
                ],
            )
        assert result.exit_code == 0, result.output
        mock.return_value.run_promote.assert_called_once()
        call_kwargs = mock.return_value.run_promote.call_args[1]
        assert call_kwargs["wave"] == 2

    def test_complete_option_passes_to_controller(self, tmp_path):
        _, vf = _make_workspace(tmp_path)
        mock = self._mock_success("dev")
        runner = CliRunner()
        with patch("strata.commands.promote.run_promote_command.PromoteController", mock):
            runner.invoke(
                promote_group,
                [
                    "--ring",
                    "dev",
                    "--file",
                    str(vf),
                    "--promotion",
                    "app-wave",
                    "--complete",
                    "--work-path",
                    str(tmp_path),
                ],
            )
        call_kwargs = mock.return_value.run_promote.call_args[1]
        assert call_kwargs["complete"] is True

    def test_force_option_passes_to_controller(self, tmp_path):
        _, vf = _make_workspace(tmp_path)
        mock = self._mock_success("dev")
        runner = CliRunner()
        with patch("strata.commands.promote.run_promote_command.PromoteController", mock):
            runner.invoke(
                promote_group,
                [
                    "--ring",
                    "dev",
                    "--file",
                    str(vf),
                    "--promotion",
                    "app-wave",
                    "--force",
                    "--work-path",
                    str(tmp_path),
                ],
            )
        call_kwargs = mock.return_value.run_promote.call_args[1]
        assert call_kwargs["force"] is True

    def test_controller_error_exits_1(self, tmp_path):
        _, vf = _make_workspace(tmp_path)
        runner = CliRunner()
        with patch(
            "strata.commands.promote.run_promote_command.PromoteController",
            self._mock_failure("Ring 'staging' not found"),
        ):
            result = runner.invoke(
                promote_group,
                ["--ring", "staging", "--file", str(vf), "--promotion", "app-wave", "--work-path", str(tmp_path)],
            )
        assert result.exit_code == 1
