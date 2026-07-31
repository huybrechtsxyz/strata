"""Unit tests for gate_controller.py — GateConditionEvaluator and WorkItemGateController.

This module previously had zero dedicated test coverage (confirmed during the
ADR-0059 gate/approval unification — see docs/decisions/0059-...md). Covers
condition evaluation, the new mode: declare vs enforce branch, and scope
filtering added by ADR-0059.
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock

from strata.controllers.gate_controller import (
    _SCHEDULED_BLOCK_SENTINEL,
    GateConditionEvaluator,
    GateContext,
    WorkItemGateController,
)
from strata.integrations.workitem.base_workitem_backend import WorkItem, WorkItemError
from strata.models.gate_model import DeploymentGateModel

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _gate(**kwargs) -> DeploymentGateModel:
    defaults = {"name": "g", "type": "approval"}
    defaults.update(kwargs)
    return DeploymentGateModel(**defaults)


def _work_item(gate_type: str = "approval") -> WorkItem:
    return WorkItem(
        id=f"{gate_type}/deploy-abc123-20260729T1000",
        type=gate_type,
        status="pending",
        deployment="deploy.yaml",
        commit="abc123",
        created_by="tester",
        created_at=datetime.now(timezone.utc).isoformat(),
    )


# ---------------------------------------------------------------------------
# GateConditionEvaluator.should_trigger
# ---------------------------------------------------------------------------


class TestGateConditionEvaluatorShouldTrigger:
    def test_when_always_triggers(self):
        gate = _gate(when="always")
        assert GateConditionEvaluator.should_trigger(gate, GateContext()) is True

    def test_cost_delta_condition_met(self):
        gate = _gate(type="cost_review", when={"cost_delta_monthly": ">= 1000"})
        context = GateContext(cost_delta_monthly=1500.0)
        assert GateConditionEvaluator.should_trigger(gate, context) is True

    def test_cost_delta_condition_not_met(self):
        gate = _gate(type="cost_review", when={"cost_delta_monthly": ">= 1000"})
        context = GateContext(cost_delta_monthly=500.0)
        assert GateConditionEvaluator.should_trigger(gate, context) is False

    def test_cost_delta_condition_data_unavailable(self):
        """None actual value means the condition can't be evaluated — don't trigger."""
        gate = _gate(type="cost_review", when={"cost_delta_monthly": ">= 1000"})
        assert GateConditionEvaluator.should_trigger(gate, GateContext()) is False

    def test_cve_critical_condition(self):
        gate = _gate(type="security_review", when={"cve_critical": ">= 1"})
        assert GateConditionEvaluator.should_trigger(gate, GateContext(cve_critical_count=2)) is True
        assert GateConditionEvaluator.should_trigger(gate, GateContext(cve_critical_count=0)) is False

    def test_cve_high_condition(self):
        gate = _gate(type="security_review", when={"cve_high": ">= 5"})
        assert GateConditionEvaluator.should_trigger(gate, GateContext(cve_high_count=5)) is True
        assert GateConditionEvaluator.should_trigger(gate, GateContext(cve_high_count=4)) is False

    def test_ai_risk_condition(self):
        gate = _gate(when={"ai_risk": ">= high"})
        assert GateConditionEvaluator.should_trigger(gate, GateContext(ai_risk="critical")) is True
        assert GateConditionEvaluator.should_trigger(gate, GateContext(ai_risk="medium")) is False

    def test_time_utc_outside_window_triggers(self):
        gate = _gate(type="scheduled", when={"time_utc": "02:00-04:00"})
        outside = datetime(2026, 7, 29, 10, 0, tzinfo=timezone.utc)
        assert GateConditionEvaluator.should_trigger(gate, GateContext(current_time_utc=outside)) is True

    def test_time_utc_inside_window_does_not_trigger(self):
        gate = _gate(type="scheduled", when={"time_utc": "02:00-04:00"})
        inside = datetime(2026, 7, 29, 3, 0, tzinfo=timezone.utc)
        assert GateConditionEvaluator.should_trigger(gate, GateContext(current_time_utc=inside)) is False

    def test_multiple_conditions_all_must_match(self):
        gate = _gate(
            type="cost_review",
            when={"cost_delta_monthly": ">= 1000", "cve_critical": ">= 1"},
        )
        # Only one of two conditions met -> should not trigger (AND logic)
        context = GateContext(cost_delta_monthly=1500.0, cve_critical_count=0)
        assert GateConditionEvaluator.should_trigger(gate, context) is False
        context_both = GateContext(cost_delta_monthly=1500.0, cve_critical_count=1)
        assert GateConditionEvaluator.should_trigger(gate, context_both) is True

    def test_invalid_numeric_expr_does_not_raise(self):
        gate = _gate(type="cost_review", when={"cost_delta_monthly": "not-an-expr"})
        context = GateContext(cost_delta_monthly=1500.0)
        assert GateConditionEvaluator.should_trigger(gate, context) is False


# ---------------------------------------------------------------------------
# WorkItemGateController.evaluate_and_create
# ---------------------------------------------------------------------------


class TestEvaluateAndCreate:
    def test_gate_triggers_and_creates_work_item(self):
        wic = MagicMock()
        wic.request.return_value = _work_item()
        controller = WorkItemGateController(wic)

        gate = _gate(mode="enforce")
        result = controller.evaluate_and_create([gate], "deploy.yaml", "abc123", GateContext())

        assert result is not None
        assert result.type == "approval"
        wic.request.assert_called_once()

    def test_no_gates_returns_none(self):
        wic = MagicMock()
        controller = WorkItemGateController(wic)
        result = controller.evaluate_and_create([], "deploy.yaml", "abc123", GateContext())
        assert result is None
        wic.request.assert_not_called()

    def test_condition_not_met_returns_none(self):
        wic = MagicMock()
        controller = WorkItemGateController(wic)
        gate = _gate(type="cost_review", when={"cost_delta_monthly": ">= 1000"})
        result = controller.evaluate_and_create([gate], "deploy.yaml", "abc123", GateContext(cost_delta_monthly=100.0))
        assert result is None
        wic.request.assert_not_called()

    def test_mode_declare_never_creates_work_item(self):
        """The core ADR-0059 behavior: declare-mode gates log, never block, never pause."""
        wic = MagicMock()
        controller = WorkItemGateController(wic)
        gate = _gate(mode="declare", when="always")
        result = controller.evaluate_and_create([gate], "deploy.yaml", "abc123", GateContext())
        assert result is None
        wic.request.assert_not_called()

    def test_mode_declare_with_approvers_does_not_raise(self):
        """Declare-mode logging serializes ApproverRef via model_dump — must not crash."""
        wic = MagicMock()
        controller = WorkItemGateController(wic)
        gate = _gate(
            mode="declare",
            when="always",
            approvers={"platform-team": {"type": "github-team", "value": "org/platform-team"}},
        )
        result = controller.evaluate_and_create([gate], "deploy.yaml", "abc123", GateContext())
        assert result is None
        wic.request.assert_not_called()

    def test_mode_enforce_continues_after_declare_gate(self):
        """A declare gate before an enforce gate shouldn't stop evaluation."""
        wic = MagicMock()
        wic.request.return_value = _work_item("cost_review")
        controller = WorkItemGateController(wic)
        declare_gate = _gate(name="d1", mode="declare", when="always")
        enforce_gate = _gate(name="e1", type="cost_review", mode="enforce", when="always")
        result = controller.evaluate_and_create([declare_gate, enforce_gate], "deploy.yaml", "abc123", GateContext())
        assert result is not None
        assert result.type == "cost_review"
        wic.request.assert_called_once()

    def test_gate_type_filter_skips_non_matching(self):
        wic = MagicMock()
        controller = WorkItemGateController(wic)
        gate = _gate(type="verify", when="always")
        result = controller.evaluate_and_create(
            [gate], "deploy.yaml", "abc123", GateContext(), gate_type_filter="approval"
        )
        assert result is None
        wic.request.assert_not_called()

    def test_gate_type_filter_matches(self):
        wic = MagicMock()
        wic.request.return_value = _work_item("verify")
        controller = WorkItemGateController(wic)
        gate = _gate(type="verify", when="always")
        result = controller.evaluate_and_create(
            [gate], "deploy.yaml", "abc123", GateContext(), gate_type_filter="verify"
        )
        assert result is not None

    def test_scope_all_always_included(self):
        wic = MagicMock()
        wic.request.return_value = _work_item()
        controller = WorkItemGateController(wic)
        gate = _gate(scope="all", when="always")
        result = controller.evaluate_and_create(
            [gate], "deploy.yaml", "abc123", GateContext(), scope_stages=["staging"]
        )
        assert result is not None

    def test_scope_mismatch_skips_gate(self):
        wic = MagicMock()
        controller = WorkItemGateController(wic)
        gate = _gate(scope=["production"], when="always")
        result = controller.evaluate_and_create(
            [gate], "deploy.yaml", "abc123", GateContext(), scope_stages=["staging"]
        )
        assert result is None
        wic.request.assert_not_called()

    def test_scope_overlap_matches(self):
        wic = MagicMock()
        wic.request.return_value = _work_item()
        controller = WorkItemGateController(wic)
        gate = _gate(scope=["staging", "production"], when="always")
        result = controller.evaluate_and_create(
            [gate], "deploy.yaml", "abc123", GateContext(), scope_stages=["staging"]
        )
        assert result is not None

    def test_scope_stages_none_means_no_filtering(self):
        """scope_stages=None (the default) means callers already pre-selected gates — don't filter."""
        wic = MagicMock()
        wic.request.return_value = _work_item()
        controller = WorkItemGateController(wic)
        gate = _gate(scope=["production"], when="always")
        result = controller.evaluate_and_create([gate], "deploy.yaml", "abc123", GateContext())
        assert result is not None

    def test_first_triggered_gate_wins(self):
        wic = MagicMock()
        wic.request.return_value = _work_item()
        controller = WorkItemGateController(wic)
        gate1 = _gate(name="first", when="always")
        gate2 = _gate(name="second", when="always")
        controller.evaluate_and_create([gate1, gate2], "deploy.yaml", "abc123", GateContext())
        assert wic.request.call_count == 1

    def test_work_item_error_continues_to_next_gate(self):
        wic = MagicMock()
        wic.request.side_effect = [WorkItemError("backend down"), _work_item()]
        controller = WorkItemGateController(wic)
        gate1 = _gate(name="first", when="always")
        gate2 = _gate(name="second", when="always")
        result = controller.evaluate_and_create([gate1, gate2], "deploy.yaml", "abc123", GateContext())
        assert result is not None
        assert wic.request.call_count == 2

    def test_scheduled_auto_resolve_outside_window_returns_sentinel(self):
        wic = MagicMock()
        wic.request.return_value = _work_item("scheduled")
        controller = WorkItemGateController(wic)
        gate = _gate(type="scheduled", auto_resolve=True, when={"time_utc": "02:00-04:00"})
        outside = datetime(2026, 7, 29, 10, 0, tzinfo=timezone.utc)
        result = controller.evaluate_and_create([gate], "deploy.yaml", "abc123", GateContext(current_time_utc=outside))
        assert result is _SCHEDULED_BLOCK_SENTINEL
        wic.request.assert_called_once()
        wic.cancel.assert_called_once()

    def test_scheduled_auto_resolve_inside_window_proceeds(self):
        wic = MagicMock()
        controller = WorkItemGateController(wic)
        gate = _gate(type="scheduled", auto_resolve=True, when={"time_utc": "02:00-04:00"})
        inside = datetime(2026, 7, 29, 3, 0, tzinfo=timezone.utc)
        result = controller.evaluate_and_create([gate], "deploy.yaml", "abc123", GateContext(current_time_utc=inside))
        assert result is None
        wic.request.assert_not_called()

    def test_min_approvals_and_description_added_to_context(self):
        wic = MagicMock()
        wic.request.return_value = _work_item()
        controller = WorkItemGateController(wic)
        gate = _gate(when="always", min_approvals=2, description="needs two sign-offs")
        controller.evaluate_and_create([gate], "deploy.yaml", "abc123", GateContext())
        _, kwargs = wic.request.call_args
        assert kwargs["context"]["min_approvals"] == 2
        assert kwargs["context"]["description"] == "needs two sign-offs"
