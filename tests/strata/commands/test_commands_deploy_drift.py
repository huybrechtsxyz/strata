"""Unit tests for DriftDeployCommand's drift.detected audit forwarding (ADR-0066 follow-up)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from strata.commands.deploy.drift_deploy_command import DriftDeployCommand
from strata.models.audit_config_model import AuditConfigModel, AuditPolicyModel
from strata.models.drift_model import DriftEntry, DriftReport, DriftSeverity


def _make_report(with_drift: bool = True) -> DriftReport:
    entries = (
        [
            DriftEntry(
                address="azurerm_network_security_rule.allow_ssh",
                resource_type="azurerm_network_security_rule",
                action="update",
                severity=DriftSeverity.CRITICAL,
                stage="network",
                changed_attributes=["source_address_prefix"],
            )
        ]
        if with_drift
        else []
    )
    return DriftReport(
        deployment="my-deploy",
        checked_at="2026-08-09T00:00:00Z",
        stages_checked=["network"],
        entries=entries,
    )


class TestForwardDriftAuditEvent:
    def test_forwards_when_gate_enabled(self, tmp_path: Path) -> None:
        cmd = DriftDeployCommand(work_path=str(tmp_path))
        report = _make_report(with_drift=True)

        audit_cfg = AuditConfigModel(policy=AuditPolicyModel(events={"drift.detected": True}))
        mock_config_service = MagicMock()
        mock_config_service.model.spec.audit = audit_cfg

        with (
            patch(
                "strata.services.configuration_service.ConfigurationService.get_instance",
                return_value=mock_config_service,
            ),
            patch("strata.controllers.audit_controller.AuditController.forward") as mock_forward,
        ):
            cmd._forward_drift_audit_event(report)

        mock_forward.assert_called_once()
        args, kwargs = mock_forward.call_args
        assert args[0] == "drift.detected"
        assert args[1]["deployment"] == "my-deploy"
        assert args[1]["has_drift"] is True
        assert args[1]["max_severity"] == "critical"

    def test_no_journal_write_when_gate_disabled_by_default(self, tmp_path: Path) -> None:
        """drift.detected defaults to enabled per the ADR's class table, but this
        confirms the gate is genuinely consulted (not bypassed) when it is off."""
        cmd = DriftDeployCommand(work_path=str(tmp_path))
        report = _make_report(with_drift=True)

        audit_cfg = AuditConfigModel(policy=AuditPolicyModel(events={"drift.detected": False}))
        mock_config_service = MagicMock()
        mock_config_service.model.spec.audit = audit_cfg

        with (
            patch(
                "strata.services.configuration_service.ConfigurationService.get_instance",
                return_value=mock_config_service,
            ),
            patch("strata.logger.audit") as mock_journal,
        ):
            cmd._forward_drift_audit_event(report)

        mock_journal.assert_not_called()

    def test_forward_failure_does_not_raise(self, tmp_path: Path) -> None:
        cmd = DriftDeployCommand(work_path=str(tmp_path))
        report = _make_report(with_drift=True)

        with patch("strata.controllers.audit_controller.AuditController.forward", side_effect=RuntimeError("boom")):
            # Must not raise
            cmd._forward_drift_audit_event(report)

    def test_config_resolution_failure_does_not_raise(self, tmp_path: Path) -> None:
        cmd = DriftDeployCommand(work_path=str(tmp_path))
        report = _make_report(with_drift=True)

        with patch(
            "strata.services.configuration_service.ConfigurationService.get_instance",
            side_effect=RuntimeError("boom"),
        ):
            # Must not raise; falls back to forward()'s own default AuditConfigModel
            cmd._forward_drift_audit_event(report)

    def test_run_drift_detection_forwards_only_when_drift_present(self, tmp_path: Path) -> None:
        """_run_drift_detection() only calls _forward_drift_audit_event when has_drift."""
        cmd = DriftDeployCommand(work_path=str(tmp_path))
        cmd._deployment_service = MagicMock()
        cmd._deployment_service.model.spec.stages = []
        cmd._deployment_service.model.meta.name = "my-deploy"

        no_drift_report = _make_report(with_drift=False)

        with (
            patch("strata.commands.deploy.drift_deploy_command.DriftController") as mock_controller_cls,
            patch.object(cmd, "_forward_drift_audit_event") as mock_forward_helper,
        ):
            mock_controller_cls.return_value.detect_drift.return_value = no_drift_report
            mock_controller_cls.return_value.get_errors.return_value = []
            mock_controller_cls.return_value.get_messages.return_value = []
            cmd._run_drift_detection()

        mock_forward_helper.assert_not_called()
