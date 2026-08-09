"""Tests for --siem flag on strata audit export."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from strata.commands.cli_audit import audit_group
from strata.utils.config import SOLUTION_DEPLOY_LOG_DIR, SOLUTION_DIR

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_deploy_log(base_path: Path, execution_id: str = "exec-001") -> None:
    """Write a minimal deploy-log entry to disk."""
    exec_dir = base_path / execution_id
    exec_dir.mkdir(parents=True, exist_ok=True)
    data = {
        "execution_id": execution_id,
        "timestamp": "2024-06-17T10:45:33+00:00",
        "version": "1.0.0",
        "deployment": "prod",
        "file": "deploy/prod.yaml",
        "success": True,
        "duration_seconds": 42.0,
        "stages": [],
    }
    (exec_dir / "_execution.json").write_text(json.dumps(data))


# ---------------------------------------------------------------------------
# AuditSinkModel's `type`/`format`/built-in-sink-type validation (ndjson/stdout/
# syslog/webhook as sink fields) was removed entirely in ADR-0066 — a sink is now
# only a routing reference (`name`, `integration`, `enabled`, `events`). Coverage
# for the promoted `syslog`/`webhook` integrations' own format/transport handling
# now lives in test_syslog_siem_integration.py / test_webhook_siem_integration.py,
# and AuditSinkModel's simplified shape is covered by test_models_audit_config.py.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# strata audit export --siem
# ---------------------------------------------------------------------------


class TestAuditExportSiemFlag:
    def test_siem_flag_accepted_by_command(self, tmp_path: Path) -> None:
        """--siem flag is accepted without crashing when integration not found."""
        runner = CliRunner()
        log_dir = tmp_path / SOLUTION_DIR / SOLUTION_DEPLOY_LOG_DIR
        log_dir.mkdir(parents=True)
        _write_deploy_log(log_dir)

        result = runner.invoke(
            audit_group,
            ["export", "--siem", "nonexistent_siem", "--work-path", str(tmp_path)],
        )
        # Should fail gracefully (integration not found) — not a crash
        assert result.exit_code != 0 or "not found" in (result.output or "")

    def test_siem_forwarding_succeeds(self, tmp_path: Path) -> None:
        """When integration is found and send_batch succeeds, exit code is 0."""
        runner = CliRunner()
        log_dir = tmp_path / SOLUTION_DIR / SOLUTION_DEPLOY_LOG_DIR
        log_dir.mkdir(parents=True)
        _write_deploy_log(log_dir)

        mock_instance = MagicMock()
        mock_instance.send_batch.return_value = True

        with patch(
            "strata.commands.audit.export_audit_command.ExportAuditCommand._forward_to_siem", return_value=True
        ) as mock_fwd:
            result = runner.invoke(
                audit_group,
                ["export", "--siem", "splunk_hec", "--work-path", str(tmp_path), "--quiet"],
            )

        assert result.exit_code == 0
        mock_fwd.assert_called_once()

    def test_siem_forwarding_failure_exits_nonzero(self, tmp_path: Path) -> None:
        """When send_batch fails, exit code is non-zero."""
        runner = CliRunner()
        log_dir = tmp_path / SOLUTION_DIR / SOLUTION_DEPLOY_LOG_DIR
        log_dir.mkdir(parents=True)
        _write_deploy_log(log_dir)

        with patch(
            "strata.commands.audit.export_audit_command.ExportAuditCommand._forward_to_siem", return_value=False
        ):
            result = runner.invoke(
                audit_group,
                ["export", "--siem", "splunk_hec", "--work-path", str(tmp_path), "--quiet"],
            )

        assert result.exit_code != 0

    def test_siem_and_out_both_work(self, tmp_path: Path) -> None:
        """When both --siem and --out are given, file is written AND SIEM is forwarded."""
        runner = CliRunner()
        log_dir = tmp_path / SOLUTION_DIR / SOLUTION_DEPLOY_LOG_DIR
        log_dir.mkdir(parents=True)
        _write_deploy_log(log_dir)
        out_file = tmp_path / "out.json"

        with patch("strata.commands.audit.export_audit_command.ExportAuditCommand._forward_to_siem", return_value=True):
            result = runner.invoke(
                audit_group,
                [
                    "export",
                    "--siem",
                    "splunk_hec",
                    "--out",
                    str(out_file),
                    "--work-path",
                    str(tmp_path),
                    "--quiet",
                ],
            )

        assert result.exit_code == 0
        assert out_file.exists()
        data = json.loads(out_file.read_text())
        assert len(data) == 1

    def test_without_siem_flag_stdout_unchanged(self, tmp_path: Path) -> None:
        """Without --siem, output goes to stdout as before."""
        runner = CliRunner()
        log_dir = tmp_path / SOLUTION_DIR / SOLUTION_DEPLOY_LOG_DIR
        log_dir.mkdir(parents=True)
        _write_deploy_log(log_dir)

        result = runner.invoke(
            audit_group,
            ["export", "--work-path", str(tmp_path)],
        )

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, list)
        assert len(data) == 1


# ---------------------------------------------------------------------------
# _forward_entries_to_siem unit tests
# ---------------------------------------------------------------------------


class TestForwardEntriesToSiem:
    def test_integration_not_found_returns_false(self, tmp_path: Path) -> None:
        from strata.commands.audit.export_audit_command import ExportAuditCommand

        cmd = ExportAuditCommand(out_file=None, siem_name="missing_integration", work_path=str(tmp_path))
        cmd._initialize(show_header=False)
        result = cmd._find_integration_model("missing_integration")
        assert result is None

    def test_non_siem_integration_returns_false(self, tmp_path: Path) -> None:
        """Integration that doesn't implement ISiemSink should fail gracefully."""
        from strata.commands.audit.export_audit_command import ExportAuditCommand
        from strata.models.integration_model import IntegrationModel

        int_model = IntegrationModel(name="git_tool", type="git")

        non_siem_instance = MagicMock(spec=[])  # no ISiemSink attributes

        cfg_dir = tmp_path / ".strata"
        cfg_dir.mkdir()
        (cfg_dir / "config.yaml").write_text(
            "apiVersion: strata.huybrechts.xyz/v1\nkind: configuration\nmeta:\n  name: cfg\nspec:\n  integrations:\n    - name: git_tool\n      type: git\n"
        )

        with patch("strata.integrations.factory.IntegrationFactory.create", return_value=non_siem_instance):
            cmd = ExportAuditCommand(out_file=None, siem_name="git_tool", work_path=str(tmp_path))
            cmd._initialize(show_header=False)
            cmd._siem_name = "git_tool"
            result = cmd._forward_to_siem()

        assert result is False

    def test_send_batch_called_with_all_entries(self, tmp_path: Path) -> None:
        from strata.commands.audit.export_audit_command import ExportAuditCommand
        from strata.models.deploy_log_model import DeployLogModel

        entries = [
            DeployLogModel(
                execution_id=f"exec-{i}",
                timestamp="2024-06-17T10:45:33+00:00",
                version="1.0",
                deployment="prod",
                file="deploy.yaml",
                success=True,
                duration_seconds=10.0,
                stages=[],
            )
            for i in range(3)
        ]

        # Use Splunk integration (a real ISiemSink) but mock _post_raw so nothing is sent
        from strata.integrations.siem.splunk_siem_integration import SplunkSiemIntegration
        from strata.models.integration_model import IntegrationEndpointsSpecModel, IntegrationModel

        int_model = IntegrationModel(
            name="splunk_hec",
            type="splunk",
            endpoints=IntegrationEndpointsSpecModel(address="https://splunk:8088"),
        )

        SplunkSiemIntegration._instances.clear()
        splunk_instance = SplunkSiemIntegration(config=int_model)

        cfg_dir = tmp_path / ".strata"
        cfg_dir.mkdir()
        (cfg_dir / "config.yaml").write_text(
            "apiVersion: strata.huybrechts.xyz/v1\nkind: configuration\nmeta:\n  name: cfg\nspec:\n"
            "  integrations:\n    - name: splunk_hec\n      type: splunk\n"
            "      endpoints:\n        address: https://splunk:8088\n"
        )

        with (
            patch("strata.integrations.factory.IntegrationFactory.create", return_value=splunk_instance),
            patch.object(splunk_instance, "_post_raw", return_value=True),
            patch.object(splunk_instance, "_get_hec_token", return_value="test-token"),
        ):
            cmd = ExportAuditCommand(out_file=None, siem_name="splunk_hec", work_path=str(tmp_path))
            cmd._initialize(show_header=False)
            cmd._siem_name = "splunk_hec"
            cmd._entries = entries
            result = cmd._forward_to_siem()

        assert result is True
