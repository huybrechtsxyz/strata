"""Unit tests for CostController's cost.threshold_exceeded audit forwarding (ADR-0066 follow-up)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from strata.controllers.cost_controller import CostController
from strata.models.audit_config_model import AuditConfigModel, AuditPolicyModel


def _make_store(latest: dict | None) -> MagicMock:
    store = MagicMock()
    store.latest.return_value = latest
    return store


def _make_config_model(alert_kwargs: dict | None) -> MagicMock:
    config_model = MagicMock()
    config_model.spec.audit = AuditConfigModel(policy=AuditPolicyModel(events={"cost.threshold_exceeded": True}))
    if alert_kwargs is None:
        config_model.spec.cost.history.alert = None
    else:
        alert_cfg = MagicMock()
        alert_cfg.max_monthly = alert_kwargs.get("max_monthly")
        alert_cfg.delta_percent = alert_kwargs.get("delta_percent")
        config_model.spec.cost.history.alert = alert_cfg
    return config_model


class TestForwardCostAuditEvent:
    def test_no_alert_config_skips_entirely(self, tmp_path: Path) -> None:
        controller = CostController(work_path=tmp_path)
        store = _make_store({"total_monthly": 500.0, "delta_from_previous": 100.0})
        config_model = _make_config_model(None)

        with (
            patch(
                "strata.services.configuration_service.ConfigurationService.get_instance",
                return_value=MagicMock(model=config_model),
            ),
            patch("strata.controllers.audit_controller.AuditController.forward") as mock_forward,
        ):
            controller._forward_cost_audit_event(store, MagicMock(), tmp_path)

        mock_forward.assert_not_called()

    def test_no_snapshot_yet_skips(self, tmp_path: Path) -> None:
        controller = CostController(work_path=tmp_path)
        store = _make_store(None)
        config_model = _make_config_model({"max_monthly": 100.0})

        with (
            patch(
                "strata.services.configuration_service.ConfigurationService.get_instance",
                return_value=MagicMock(model=config_model),
            ),
            patch("strata.controllers.audit_controller.AuditController.forward") as mock_forward,
        ):
            controller._forward_cost_audit_event(store, MagicMock(), tmp_path)

        mock_forward.assert_not_called()

    def test_snapshot_missing_total_monthly_skips(self, tmp_path: Path) -> None:
        controller = CostController(work_path=tmp_path)
        store = _make_store({"delta_from_previous": None})
        config_model = _make_config_model({"max_monthly": 100.0})

        with (
            patch(
                "strata.services.configuration_service.ConfigurationService.get_instance",
                return_value=MagicMock(model=config_model),
            ),
            patch("strata.controllers.audit_controller.AuditController.forward") as mock_forward,
        ):
            controller._forward_cost_audit_event(store, MagicMock(), tmp_path)

        mock_forward.assert_not_called()

    def test_version_included_in_payload_when_present(self, tmp_path: Path) -> None:
        controller = CostController(work_path=tmp_path)
        store = _make_store(
            {
                "total_monthly": 500.0,
                "delta_from_previous": None,
                "currency": "USD",
                "recorded_at": "2026-01-01T00:00:00Z",
                "provisioners": {"infra": 500.0},
                "version": "1.2.3",
            }
        )
        config_model = _make_config_model({"max_monthly": 100.0})

        with (
            patch(
                "strata.services.configuration_service.ConfigurationService.get_instance",
                return_value=MagicMock(model=config_model),
            ),
            patch("strata.controllers.audit_controller.AuditController.forward") as mock_forward,
        ):
            controller._forward_cost_audit_event(store, MagicMock(), tmp_path)

        mock_forward.assert_called_once()
        args, _kwargs = mock_forward.call_args
        assert args[1]["version"] == "1.2.3"

    def test_ceiling_breach_fires(self, tmp_path: Path) -> None:
        controller = CostController(work_path=tmp_path)
        store = _make_store(
            {
                "total_monthly": 500.0,
                "delta_from_previous": None,
                "currency": "USD",
                "recorded_at": "2026-01-01T00:00:00Z",
                "provisioners": {"infra": 500.0},
            }
        )
        config_model = _make_config_model({"max_monthly": 100.0})
        deployment_service = MagicMock()
        deployment_service.get_name.return_value = "my-deploy"

        with (
            patch(
                "strata.services.configuration_service.ConfigurationService.get_instance",
                return_value=MagicMock(model=config_model),
            ),
            patch("strata.controllers.audit_controller.AuditController.forward") as mock_forward,
        ):
            controller._forward_cost_audit_event(store, deployment_service, tmp_path)

        mock_forward.assert_called_once()
        args, _kwargs = mock_forward.call_args
        assert args[0] == "cost.threshold_exceeded"
        assert args[1]["deployment"] == "my-deploy"
        assert args[1]["alert_reason"] == "ceiling"
        assert args[1]["total_monthly"] == 500.0

    def test_delta_breach_fires(self, tmp_path: Path) -> None:
        controller = CostController(work_path=tmp_path)
        # previous_total = 100.0 (total - delta), delta_pct = 100% >= 10% threshold
        store = _make_store(
            {
                "total_monthly": 200.0,
                "delta_from_previous": 100.0,
                "currency": "USD",
                "recorded_at": "2026-01-01T00:00:00Z",
                "provisioners": {"infra": 200.0},
            }
        )
        config_model = _make_config_model({"delta_percent": 10.0})

        with (
            patch(
                "strata.services.configuration_service.ConfigurationService.get_instance",
                return_value=MagicMock(model=config_model),
            ),
            patch("strata.controllers.audit_controller.AuditController.forward") as mock_forward,
        ):
            controller._forward_cost_audit_event(store, MagicMock(), tmp_path)

        mock_forward.assert_called_once()
        args, _kwargs = mock_forward.call_args
        assert args[1]["alert_reason"] == "delta"

    def test_cost_decrease_never_fires_delta(self, tmp_path: Path) -> None:
        controller = CostController(work_path=tmp_path)
        store = _make_store(
            {
                "total_monthly": 50.0,
                "delta_from_previous": -50.0,
                "currency": "USD",
                "recorded_at": "2026-01-01T00:00:00Z",
                "provisioners": {"infra": 50.0},
            }
        )
        config_model = _make_config_model({"delta_percent": 10.0})

        with (
            patch(
                "strata.services.configuration_service.ConfigurationService.get_instance",
                return_value=MagicMock(model=config_model),
            ),
            patch("strata.controllers.audit_controller.AuditController.forward") as mock_forward,
        ):
            controller._forward_cost_audit_event(store, MagicMock(), tmp_path)

        mock_forward.assert_not_called()

    def test_below_thresholds_does_not_fire(self, tmp_path: Path) -> None:
        controller = CostController(work_path=tmp_path)
        store = _make_store(
            {
                "total_monthly": 50.0,
                "delta_from_previous": 1.0,
                "currency": "USD",
                "recorded_at": "2026-01-01T00:00:00Z",
                "provisioners": {"infra": 50.0},
            }
        )
        config_model = _make_config_model({"max_monthly": 1000.0, "delta_percent": 90.0})

        with (
            patch(
                "strata.services.configuration_service.ConfigurationService.get_instance",
                return_value=MagicMock(model=config_model),
            ),
            patch("strata.controllers.audit_controller.AuditController.forward") as mock_forward,
        ):
            controller._forward_cost_audit_event(store, MagicMock(), tmp_path)

        mock_forward.assert_not_called()

    def test_forward_failure_does_not_raise(self, tmp_path: Path) -> None:
        controller = CostController(work_path=tmp_path)
        store = _make_store({"total_monthly": 500.0, "delta_from_previous": None})
        config_model = _make_config_model({"max_monthly": 100.0})

        with (
            patch(
                "strata.services.configuration_service.ConfigurationService.get_instance",
                return_value=MagicMock(model=config_model),
            ),
            patch(
                "strata.controllers.audit_controller.AuditController.forward",
                side_effect=RuntimeError("boom"),
            ),
        ):
            # Must not raise
            controller._forward_cost_audit_event(store, MagicMock(), tmp_path)

    def test_config_resolution_failure_does_not_raise(self, tmp_path: Path) -> None:
        controller = CostController(work_path=tmp_path)
        store = _make_store({"total_monthly": 500.0, "delta_from_previous": None})

        with patch(
            "strata.services.configuration_service.ConfigurationService.get_instance",
            side_effect=RuntimeError("boom"),
        ):
            # Must not raise
            controller._forward_cost_audit_event(store, MagicMock(), tmp_path)


class TestRecordHistorySnapshotWiring:
    def test_record_history_snapshot_calls_push_and_both_forwards(self, tmp_path: Path) -> None:
        """_record_history_snapshot() calls _push_cost_history(), _forward_cost_audit_event()
        (alert, conditional), and _forward_cost_recorded_event() (unconditional history
        record) after writing the snapshot, mirroring drift's own three-call wiring."""
        controller = CostController(work_path=tmp_path)
        deployment_service = MagicMock()
        deployment_service.get_name.return_value = "my-deploy"
        deployment_service.get_version.return_value = None

        with (
            patch.object(controller, "_push_cost_history") as mock_push,
            patch.object(controller, "_forward_cost_audit_event") as mock_forward_helper,
            patch.object(controller, "_forward_cost_recorded_event") as mock_recorded_helper,
        ):
            controller._record_history_snapshot({"total_monthly_cost": 100.0}, deployment_service)

        mock_push.assert_called_once()
        mock_forward_helper.assert_called_once()
        mock_recorded_helper.assert_called_once()


class TestForwardCostRecordedEvent:
    def test_no_snapshot_yet_skips(self, tmp_path: Path) -> None:
        controller = CostController(work_path=tmp_path)
        store = _make_store(None)
        config_model = MagicMock()
        config_model.spec.audit = AuditConfigModel(policy=AuditPolicyModel(events={"cost.recorded": True}))

        with (
            patch(
                "strata.services.configuration_service.ConfigurationService.get_instance",
                return_value=MagicMock(model=config_model),
            ),
            patch("strata.controllers.audit_controller.AuditController.forward") as mock_forward,
        ):
            controller._forward_cost_recorded_event(store, MagicMock(), tmp_path)

        mock_forward.assert_not_called()

    def test_fires_unconditionally_when_snapshot_present(self, tmp_path: Path) -> None:
        controller = CostController(work_path=tmp_path)
        store = _make_store(
            {
                "total_monthly": 50.0,
                "delta_from_previous": 1.0,
                "currency": "USD",
                "recorded_at": "2026-01-01T00:00:00Z",
                "provisioners": {"infra": 50.0},
            }
        )
        config_model = MagicMock()
        config_model.spec.audit = AuditConfigModel(policy=AuditPolicyModel(events={"cost.recorded": True}))
        deployment_service = MagicMock()
        deployment_service.get_name.return_value = "my-deploy"

        with (
            patch(
                "strata.services.configuration_service.ConfigurationService.get_instance",
                return_value=MagicMock(model=config_model),
            ),
            patch("strata.controllers.audit_controller.AuditController.forward") as mock_forward,
        ):
            controller._forward_cost_recorded_event(store, deployment_service, tmp_path)

        mock_forward.assert_called_once()
        args, _kwargs = mock_forward.call_args
        assert args[0] == "cost.recorded"
        assert args[1]["deployment"] == "my-deploy"
        assert args[1]["total_monthly"] == 50.0
        assert args[1]["execution_id"]

    def test_execution_id_is_deterministic_for_same_snapshot(self, tmp_path: Path) -> None:
        """Same deployment+recorded_at must produce the same execution_id — idempotency
        (Step 2.3's primary key) depends on a resend of the same snapshot being a no-op."""
        controller = CostController(work_path=tmp_path)
        snapshot: dict = {
            "total_monthly": 50.0,
            "delta_from_previous": None,
            "currency": "USD",
            "recorded_at": "2026-01-01T00:00:00Z",
            "provisioners": {},
        }
        config_model = MagicMock()
        config_model.spec.audit = AuditConfigModel(policy=AuditPolicyModel(events={"cost.recorded": True}))
        deployment_service = MagicMock()
        deployment_service.get_name.return_value = "my-deploy"

        execution_ids = []
        for _ in range(2):
            store = _make_store(dict(snapshot))
            with (
                patch(
                    "strata.services.configuration_service.ConfigurationService.get_instance",
                    return_value=MagicMock(model=config_model),
                ),
                patch("strata.controllers.audit_controller.AuditController.forward") as mock_forward,
            ):
                controller._forward_cost_recorded_event(store, deployment_service, tmp_path)
            execution_ids.append(mock_forward.call_args[0][1]["execution_id"])

        assert execution_ids[0] == execution_ids[1]

    def test_forward_failure_does_not_raise(self, tmp_path: Path) -> None:
        controller = CostController(work_path=tmp_path)
        store = _make_store({"total_monthly": 50.0, "delta_from_previous": None, "recorded_at": "t"})
        config_model = MagicMock()
        config_model.spec.audit = AuditConfigModel(policy=AuditPolicyModel(events={"cost.recorded": True}))

        with (
            patch(
                "strata.services.configuration_service.ConfigurationService.get_instance",
                return_value=MagicMock(model=config_model),
            ),
            patch(
                "strata.controllers.audit_controller.AuditController.forward",
                side_effect=RuntimeError("boom"),
            ),
        ):
            # Must not raise
            controller._forward_cost_recorded_event(store, MagicMock(), tmp_path)

    def test_config_resolution_failure_does_not_raise(self, tmp_path: Path) -> None:
        controller = CostController(work_path=tmp_path)
        store = _make_store({"total_monthly": 50.0, "delta_from_previous": None, "recorded_at": "t"})

        with patch(
            "strata.services.configuration_service.ConfigurationService.get_instance",
            side_effect=RuntimeError("boom"),
        ):
            # Must not raise
            controller._forward_cost_recorded_event(store, MagicMock(), tmp_path)
