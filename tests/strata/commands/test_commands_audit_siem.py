"""Tests for --siem flag on strata audit export."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from strata.commands.cli_audit import audit_group
from strata.utils.config import SOLUTION_DEPLOY_LOG_DIR, SOLUTION_DIR

if TYPE_CHECKING:
    from strata.commands.audit.export_audit_command import ExportAuditCommand

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
    def setup_method(self):
        from strata.services.configuration_service import ConfigurationService

        # ConfigurationService.load() is a process-wide singleton that only loads once
        # (ConfigurationService.load() docstring: "populated once per process") — reset
        # it so each test's own .strata/configuration.yaml is actually read, rather than
        # an earlier test's stale, already-loaded config.
        ConfigurationService.reset()

    def teardown_method(self):
        from strata.services.configuration_service import ConfigurationService

        ConfigurationService.reset()

    def test_integration_not_found_returns_false(self, tmp_path: Path) -> None:
        """Sink declared in spec.audit.sinks but the integration itself isn't registered
        (e.g. resolution failed, or the referenced integration was removed)."""
        from strata.commands.audit.export_audit_command import ExportAuditCommand

        cfg_dir = tmp_path / ".strata"
        cfg_dir.mkdir()
        (cfg_dir / "configuration.yaml").write_text(
            "apiVersion: strata.huybrechts.xyz/v1\nkind: configuration\nmeta:\n  name: cfg\nspec:\n"
            "  audit:\n    sinks:\n      - name: missing_integration\n        integration: missing_integration\n"
        )

        mock_svc = MagicMock()
        mock_svc.is_initialized.return_value = True
        mock_svc.get_integration.return_value = None

        with patch("strata.services.integration_service.IntegrationService.get_instance", return_value=mock_svc):
            cmd = ExportAuditCommand(out_file=None, siem_name="missing_integration", work_path=str(tmp_path))
            cmd._initialize(show_header=False)
            cmd._siem_name = "missing_integration"
            result = cmd._forward_to_siem()

        assert result is False
        assert any("not found in configuration" in e for e in cmd._errors)

    def test_non_siem_integration_returns_false(self, tmp_path: Path) -> None:
        """Integration that doesn't implement ISiemSink should fail gracefully."""
        from strata.commands.audit.export_audit_command import ExportAuditCommand

        non_siem_instance = MagicMock(spec=[])  # no ISiemSink attributes
        mock_svc = MagicMock()
        mock_svc.is_initialized.return_value = True
        mock_svc.get_integration.return_value = non_siem_instance

        cfg_dir = tmp_path / ".strata"
        cfg_dir.mkdir()
        (cfg_dir / "configuration.yaml").write_text(
            "apiVersion: strata.huybrechts.xyz/v1\nkind: configuration\nmeta:\n  name: cfg\nspec:\n"
            "  integrations:\n    - name: git_tool\n      type: git\n"
            "  audit:\n    sinks:\n      - name: git_tool\n        integration: git_tool\n"
        )

        with patch("strata.services.integration_service.IntegrationService.get_instance", return_value=mock_svc):
            cmd = ExportAuditCommand(out_file=None, siem_name="git_tool", work_path=str(tmp_path))
            cmd._initialize(show_header=False)
            cmd._siem_name = "git_tool"
            result = cmd._forward_to_siem()

        assert result is False

    def test_integration_not_declared_as_sink_returns_false(self, tmp_path: Path) -> None:
        """ADR-0066 gap 1 fix: an integration not referenced by spec.audit.sinks is rejected."""
        from strata.commands.audit.export_audit_command import ExportAuditCommand

        cfg_dir = tmp_path / ".strata"
        cfg_dir.mkdir()
        (cfg_dir / "configuration.yaml").write_text(
            "apiVersion: strata.huybrechts.xyz/v1\nkind: configuration\nmeta:\n  name: cfg\nspec:\n"
            "  integrations:\n    - name: splunk_hec\n      type: splunk\n"
            "      endpoints:\n        address: https://splunk:8088\n"
        )

        cmd = ExportAuditCommand(out_file=None, siem_name="splunk_hec", work_path=str(tmp_path))
        cmd._initialize(show_header=False)
        cmd._siem_name = "splunk_hec"
        result = cmd._forward_to_siem()

        assert result is False
        assert any("not declared as a sink" in e for e in cmd._errors)

    def test_disabled_sink_returns_false(self, tmp_path: Path) -> None:
        from strata.commands.audit.export_audit_command import ExportAuditCommand

        cfg_dir = tmp_path / ".strata"
        cfg_dir.mkdir()
        (cfg_dir / "configuration.yaml").write_text(
            "apiVersion: strata.huybrechts.xyz/v1\nkind: configuration\nmeta:\n  name: cfg\nspec:\n"
            "  integrations:\n    - name: splunk_hec\n      type: splunk\n"
            "      endpoints:\n        address: https://splunk:8088\n"
            "  audit:\n    sinks:\n      - name: splunk_hec\n        integration: splunk_hec\n        enabled: false\n"
        )

        cmd = ExportAuditCommand(out_file=None, siem_name="splunk_hec", work_path=str(tmp_path))
        cmd._initialize(show_header=False)
        cmd._siem_name = "splunk_hec"
        result = cmd._forward_to_siem()

        assert result is False
        assert any("disabled" in e for e in cmd._errors)

    def test_gate_disabled_skips_without_failing(self, tmp_path: Path) -> None:
        """ADR-0066 gap 1 fix: the policy gate is now consulted — a disabled event type is
        a deliberate skip, not a failure."""
        from strata.commands.audit.export_audit_command import ExportAuditCommand

        cfg_dir = tmp_path / ".strata"
        cfg_dir.mkdir()
        (cfg_dir / "configuration.yaml").write_text(
            "apiVersion: strata.huybrechts.xyz/v1\nkind: configuration\nmeta:\n  name: cfg\nspec:\n"
            "  integrations:\n    - name: splunk_hec\n      type: splunk\n"
            "      endpoints:\n        address: https://splunk:8088\n"
            "  audit:\n    policy:\n      events:\n        deployment.completed: false\n"
            "    sinks:\n      - name: splunk_hec\n        integration: splunk_hec\n"
        )

        cmd = ExportAuditCommand(out_file=None, siem_name="splunk_hec", work_path=str(tmp_path))
        cmd._initialize(show_header=False)
        cmd._siem_name = "splunk_hec"
        result = cmd._forward_to_siem()

        assert result is True
        assert cmd._errors == []

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
        (cfg_dir / "configuration.yaml").write_text(
            "apiVersion: strata.huybrechts.xyz/v1\nkind: configuration\nmeta:\n  name: cfg\nspec:\n"
            "  integrations:\n    - name: splunk_hec\n      type: splunk\n"
            "      endpoints:\n        address: https://splunk:8088\n"
            "  audit:\n    sinks:\n      - name: splunk_hec\n        integration: splunk_hec\n"
        )

        mock_svc = MagicMock()
        mock_svc.is_initialized.return_value = True
        mock_svc.get_integration.return_value = splunk_instance

        with (
            patch("strata.services.integration_service.IntegrationService.get_instance", return_value=mock_svc),
            patch.object(splunk_instance, "_post_raw", return_value=True),
            patch.object(splunk_instance, "_get_hec_token", return_value="test-token"),
            patch("strata.controllers.actor_controller.resolve_actor", return_value="test-user"),
        ):
            cmd = ExportAuditCommand(out_file=None, siem_name="splunk_hec", work_path=str(tmp_path))
            cmd._initialize(show_header=False)
            cmd._siem_name = "splunk_hec"
            cmd._entries = entries
            result = cmd._forward_to_siem()

        assert result is True


class TestSbomIgnoreRulesForwarding:
    """ADR-0066 gap-1 follow-up: sbom-ignore-rules forwarding stays in sync with the
    deploy-log batch — same resolved integration instance, same envelope shape, and
    failures now surface into self._errors instead of being swallowed."""

    def setup_method(self):
        from strata.services.configuration_service import ConfigurationService

        ConfigurationService.reset()

    def teardown_method(self):
        from strata.services.configuration_service import ConfigurationService

        ConfigurationService.reset()

    def _make_command(self, tmp_path: Path, splunk_instance) -> "ExportAuditCommand":
        from strata.commands.audit.export_audit_command import ExportAuditCommand

        (tmp_path / ".strata").mkdir(exist_ok=True)
        (tmp_path / ".strata" / "configuration.yaml").write_text(
            "apiVersion: strata.huybrechts.xyz/v1\nkind: configuration\nmeta:\n  name: cfg\nspec:\n"
            "  integrations:\n    - name: splunk_hec\n      type: splunk\n"
            "      endpoints:\n        address: https://splunk:8088\n"
            "  audit:\n    sinks:\n      - name: splunk_hec\n        integration: splunk_hec\n"
        )
        cmd = ExportAuditCommand(out_file=None, siem_name="splunk_hec", work_path=str(tmp_path))
        cmd._initialize(show_header=False)
        cmd._siem_name = "splunk_hec"
        return cmd

    def test_no_forward_when_ignore_config_absent(self, tmp_path: Path) -> None:
        """No .strata/sbom-ignore.yaml — no second batch, no extra calls."""
        from strata.integrations.siem.splunk_siem_integration import SplunkSiemIntegration
        from strata.models.integration_model import IntegrationEndpointsSpecModel, IntegrationModel

        SplunkSiemIntegration._instances.clear()
        splunk_instance = SplunkSiemIntegration(
            config=IntegrationModel(
                name="splunk_hec", type="splunk", endpoints=IntegrationEndpointsSpecModel(address="https://splunk:8088")
            )
        )
        mock_svc = MagicMock()
        mock_svc.is_initialized.return_value = True
        mock_svc.get_integration.return_value = splunk_instance

        cmd = self._make_command(tmp_path, splunk_instance)

        with (
            patch("strata.services.integration_service.IntegrationService.get_instance", return_value=mock_svc),
            patch.object(splunk_instance, "send_batch", return_value=True) as mock_send_batch,
            patch("strata.controllers.actor_controller.resolve_actor", return_value="test-user"),
        ):
            result = cmd._forward_to_siem()

        assert result is True
        # Only the deploy-log batch — no sbom_ignore_rules batch when there's nothing to ignore
        mock_send_batch.assert_called_once()
        assert mock_send_batch.call_args[0][0] == "deployment.completed"

    def test_reuses_already_resolved_instance_no_second_lookup(self, tmp_path: Path) -> None:
        """The sbom-ignore batch goes through the SAME resolved instance — no redundant
        .strata/*.yaml re-scan or second IntegrationService/IntegrationFactory lookup."""
        from strata.integrations.siem.splunk_siem_integration import SplunkSiemIntegration
        from strata.models.integration_model import IntegrationEndpointsSpecModel, IntegrationModel

        (tmp_path / ".strata").mkdir(exist_ok=True)
        (tmp_path / ".strata" / "sbom-ignore.yaml").write_text("ignore_packages:\n  - pattern: 'dev-*'\n")

        SplunkSiemIntegration._instances.clear()
        splunk_instance = SplunkSiemIntegration(
            config=IntegrationModel(
                name="splunk_hec", type="splunk", endpoints=IntegrationEndpointsSpecModel(address="https://splunk:8088")
            )
        )
        mock_svc = MagicMock()
        mock_svc.is_initialized.return_value = True
        mock_svc.get_integration.return_value = splunk_instance

        cmd = self._make_command(tmp_path, splunk_instance)

        with (
            patch("strata.services.integration_service.IntegrationService.get_instance", return_value=mock_svc),
            patch.object(splunk_instance, "send_batch", return_value=True) as mock_send_batch,
            patch("strata.controllers.actor_controller.resolve_actor", return_value="test-user"),
        ):
            result = cmd._forward_to_siem()

        assert result is True
        # get_integration called exactly once — the sbom batch reuses the same instance
        mock_svc.get_integration.assert_called_once_with("splunk_hec")
        assert mock_send_batch.call_count == 2
        deploy_call, sbom_call = mock_send_batch.call_args_list
        assert deploy_call[0][0] == "deployment.completed"
        assert sbom_call[0][0] == "sbom_ignore_rules"

    def test_sbom_batch_is_enveloped(self, tmp_path: Path) -> None:
        """The sbom-ignore payload is wrapped in the same CloudEvents/ECS envelope shape
        as the deploy-log batch, not sent raw."""
        from strata.integrations.siem.splunk_siem_integration import SplunkSiemIntegration
        from strata.models.integration_model import IntegrationEndpointsSpecModel, IntegrationModel

        (tmp_path / ".strata").mkdir(exist_ok=True)
        (tmp_path / ".strata" / "sbom-ignore.yaml").write_text("ignore_packages:\n  - pattern: 'dev-*'\n")

        SplunkSiemIntegration._instances.clear()
        splunk_instance = SplunkSiemIntegration(
            config=IntegrationModel(
                name="splunk_hec", type="splunk", endpoints=IntegrationEndpointsSpecModel(address="https://splunk:8088")
            )
        )
        mock_svc = MagicMock()
        mock_svc.is_initialized.return_value = True
        mock_svc.get_integration.return_value = splunk_instance

        cmd = self._make_command(tmp_path, splunk_instance)

        with (
            patch("strata.services.integration_service.IntegrationService.get_instance", return_value=mock_svc),
            patch.object(splunk_instance, "send_batch", return_value=True) as mock_send_batch,
            patch("strata.controllers.actor_controller.resolve_actor", return_value="test-user"),
        ):
            cmd._forward_to_siem()

        _, sbom_call = mock_send_batch.call_args_list
        sbom_payloads = sbom_call[0][1]
        assert len(sbom_payloads) == 1
        envelope = sbom_payloads[0]
        assert envelope["specversion"] == "1.0"
        assert envelope["type"] == "xyz.huybrechts.strata.sbom_ignore_rules"
        assert envelope["data"]["strata"]["ignore_packages"][0]["pattern"] == "dev-*"

    def test_sbom_forward_failure_surfaces_as_error_and_fails_command(self, tmp_path: Path) -> None:
        """A failed sbom-ignore forward is no longer silently best-effort — it now
        surfaces in self._errors and fails the overall result, same as the main batch."""
        from strata.integrations.siem.splunk_siem_integration import SplunkSiemIntegration
        from strata.models.integration_model import IntegrationEndpointsSpecModel, IntegrationModel

        (tmp_path / ".strata").mkdir(exist_ok=True)
        (tmp_path / ".strata" / "sbom-ignore.yaml").write_text("ignore_packages:\n  - pattern: 'dev-*'\n")

        SplunkSiemIntegration._instances.clear()
        splunk_instance = SplunkSiemIntegration(
            config=IntegrationModel(
                name="splunk_hec", type="splunk", endpoints=IntegrationEndpointsSpecModel(address="https://splunk:8088")
            )
        )
        mock_svc = MagicMock()
        mock_svc.is_initialized.return_value = True
        mock_svc.get_integration.return_value = splunk_instance

        cmd = self._make_command(tmp_path, splunk_instance)

        def _send_batch_side_effect(log_type, payloads, **kwargs):
            return log_type != "sbom_ignore_rules"

        with (
            patch("strata.services.integration_service.IntegrationService.get_instance", return_value=mock_svc),
            patch.object(splunk_instance, "send_batch", side_effect=_send_batch_side_effect),
            patch("strata.controllers.actor_controller.resolve_actor", return_value="test-user"),
        ):
            result = cmd._forward_to_siem()

        assert result is False
        assert any("sbom-ignore rules" in e for e in cmd._errors)
