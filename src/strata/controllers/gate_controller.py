"""Gate condition evaluation and work-item gate orchestration — ADR-0057."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional

from strata.controllers.workitem_controller import WorkItemController
from strata.integrations.workitem.base_workitem_backend import WorkItem, WorkItemError
from strata.logger import get_logger
from strata.models.gate_model import DeploymentGateModel, GateWhenConditionsModel

logger = get_logger(__name__)

# Sentinel returned when a scheduled gate blocks (auto_resolve=True, outside window).
# Signals "blocked but no human action needed — retry when window opens."
_SCHEDULED_BLOCK_SENTINEL: WorkItem = WorkItem(  # type: ignore[call-arg]
    id="_scheduled_block_",
    type="scheduled",
    status="cancelled",
    deployment="",
    commit="",
    created_by="system",
    created_at="",
)

# ---------------------------------------------------------------------------
# Risk level ordering for ">= high" comparisons
# ---------------------------------------------------------------------------

_RISK_ORDER: Dict[str, int] = {"low": 0, "medium": 1, "high": 2, "critical": 3}


# ---------------------------------------------------------------------------
# Gate evaluation context
# ---------------------------------------------------------------------------


@dataclass
class GateContext:
    """Runtime values used to evaluate gate `when:` conditions."""

    cost_delta_monthly: Optional[float] = None
    cve_critical_count: int = 0
    cve_high_count: int = 0
    ai_risk: Optional[str] = None
    current_time_utc: Optional[datetime] = field(default_factory=lambda: datetime.now(timezone.utc))
    extra: Dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Condition expression evaluator
# ---------------------------------------------------------------------------

_OPERATOR_RE = re.compile(r"^\s*(>=|<=|>|<|==|!=)\s*(.+)$")


def _eval_numeric_expr(expr: str, actual: Optional[float]) -> bool:
    """Evaluate ">= 1000" style expression against a numeric value.
    Returns False (don't trigger) when actual is None (data not available)."""
    if actual is None:
        return False
    m = _OPERATOR_RE.match(expr.strip())
    if not m:
        logger.warning("gate.invalid_numeric_expr", expr=expr)
        return False
    op, rhs = m.group(1), m.group(2).strip()
    try:
        threshold = float(rhs)
    except ValueError:
        logger.warning("gate.invalid_numeric_threshold", expr=expr)
        return False
    ops = {
        ">=": actual >= threshold,
        "<=": actual <= threshold,
        ">": actual > threshold,
        "<": actual < threshold,
        "==": actual == threshold,
        "!=": actual != threshold,
    }
    return ops.get(op, False)


def _eval_risk_expr(expr: str, actual: Optional[str]) -> bool:
    """Evaluate ">= high" style expression against an AI risk string."""
    if actual is None:
        return False
    m = _OPERATOR_RE.match(expr.strip())
    if not m:
        logger.warning("gate.invalid_risk_expr", expr=expr)
        return False
    op, rhs = m.group(1), m.group(2).strip().lower()
    actual_ord = _RISK_ORDER.get(actual.lower(), 0)
    threshold_ord = _RISK_ORDER.get(rhs, -1)
    if threshold_ord == -1:
        logger.warning("gate.unknown_risk_threshold", expr=expr, rhs=rhs, valid=list(_RISK_ORDER))
        return False
    ops = {
        ">=": actual_ord >= threshold_ord,
        "<=": actual_ord <= threshold_ord,
        ">": actual_ord > threshold_ord,
        "<": actual_ord < threshold_ord,
        "==": actual_ord == threshold_ord,
        "!=": actual_ord != threshold_ord,
    }
    return ops.get(op, False)


def _eval_time_window(window: str, now: Optional[datetime]) -> bool:
    """Return True when current time is OUTSIDE the allowed window (gate should trigger).
    Window format: "HH:MM-HH:MM" UTC."""
    if now is None:
        return False
    m = re.match(r"^(\d{1,2}):(\d{2})-(\d{1,2}):(\d{2})$", window.strip())
    if not m:
        logger.warning("gate.invalid_time_window", window=window)
        return False
    sh, sm, eh, em = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
    current_mins = now.hour * 60 + now.minute
    start_mins = sh * 60 + sm
    end_mins = eh * 60 + em
    # Handle overnight windows (e.g. 22:00-02:00)
    if start_mins <= end_mins:
        inside_window = start_mins <= current_mins < end_mins
    else:
        inside_window = current_mins >= start_mins or current_mins < end_mins
    # Gate triggers when OUTSIDE the allowed window
    return not inside_window


# ---------------------------------------------------------------------------
# Gate condition evaluator
# ---------------------------------------------------------------------------


class GateConditionEvaluator:
    """Evaluates whether a gate's `when:` clause should trigger given a GateContext."""

    @staticmethod
    def should_trigger(gate: DeploymentGateModel, context: GateContext) -> bool:
        """Returns True if the gate condition is met and a work item should be created."""
        if gate.when == "always":
            return True

        if not isinstance(gate.when, GateWhenConditionsModel):
            return True  # unknown shape — trigger conservatively

        cond = gate.when
        # All non-None conditions must match (AND logic)
        checks: List[bool] = []

        if cond.cost_delta_monthly is not None:
            checks.append(_eval_numeric_expr(cond.cost_delta_monthly, context.cost_delta_monthly))

        if cond.cve_critical is not None:
            checks.append(_eval_numeric_expr(cond.cve_critical, float(context.cve_critical_count)))

        if cond.cve_high is not None:
            checks.append(_eval_numeric_expr(cond.cve_high, float(context.cve_high_count)))

        if cond.ai_risk is not None:
            checks.append(_eval_risk_expr(cond.ai_risk, context.ai_risk))

        if cond.time_utc is not None:
            checks.append(_eval_time_window(cond.time_utc, context.current_time_utc))

        if not checks:
            # No recognized conditions — don't trigger
            return False

        return all(checks)


# ---------------------------------------------------------------------------
# WorkItemGateController
# ---------------------------------------------------------------------------


class WorkItemGateController:
    """Connects gate configuration to work-item lifecycle."""

    def __init__(self, work_item_controller: WorkItemController) -> None:
        self._wic = work_item_controller

    # ------------------------------------------------------------------
    # Evaluate gates and create work item for first triggered gate
    # ------------------------------------------------------------------

    def evaluate_and_create(
        self,
        gates: List[DeploymentGateModel],
        deployment: str,
        commit: str,
        context: GateContext,
        gate_type_filter: Optional[str] = None,
    ) -> Optional[WorkItem]:
        """Evaluate all gates. Returns a WorkItem for the first triggered gate, else None.

        Args:
            gate_type_filter: when set, only evaluate gates of this type
                              (e.g. "approval" pre-plan, "cost_review" post-plan,
                               "verify" post-apply).
        """
        for gate in gates:
            # Filter by type if requested
            if gate_type_filter is not None and gate.type != gate_type_filter:
                continue

            # --- Scheduled gate with auto_resolve: enforce window, no work item ---
            if gate.type == "scheduled" and gate.auto_resolve:
                if GateConditionEvaluator.should_trigger(gate, context):
                    # We're OUTSIDE the maintenance window
                    window = gate.when.time_utc if isinstance(gate.when, GateWhenConditionsModel) else "unknown"
                    logger.info(
                        "gate.scheduled_window_blocked",
                        deployment=deployment,
                        window=window,
                    )
                    # Create a transient work item to communicate the block reason
                    # It is immediately auto-cancelled (not pending) — no human action needed
                    gate_context: dict = {
                        "description": f"Deployment blocked: outside maintenance window {window}",
                        "window": window,
                        "auto_resolve": True,
                    }
                    try:
                        item = self._wic.request(
                            type=gate.type,
                            deployment=deployment,
                            commit=commit,
                            context=gate_context,
                            expires_minutes=gate.timeout_minutes,
                        )
                        # Auto-cancel: this gate enforces "try again during the window"
                        self._wic.cancel(item.id, reason=f"Outside window {window} — retry when window opens")
                        logger.info("gate.scheduled_auto_cancelled", item_id=item.id, window=window)
                    except WorkItemError:
                        pass
                    # Return a sentinel that signals "blocked, no human needed"
                    return _SCHEDULED_BLOCK_SENTINEL
                else:
                    # Inside window — proceed silently
                    logger.debug("gate.scheduled_window_open", deployment=deployment)
                    continue

            if not GateConditionEvaluator.should_trigger(gate, context):
                logger.debug("gate.condition_not_met", gate_type=gate.type, deployment=deployment)
                continue

            # Gate triggers — build work item context
            gate_context = {}
            if gate.description:
                gate_context["description"] = gate.description
            if gate.approvers:
                gate_context["approvers"] = gate.approvers
            if gate.min_approvals != 1:
                gate_context["min_approvals"] = gate.min_approvals
            # Type-specific context enrichment
            if gate.type in ("approval", "cost_review"):
                if context.cost_delta_monthly is not None:
                    gate_context["cost_delta_monthly"] = context.cost_delta_monthly
            if gate.type in ("approval", "security_review"):
                if context.cve_critical_count:
                    gate_context["cve_critical_count"] = context.cve_critical_count
                if context.cve_high_count:
                    gate_context["cve_high_count"] = context.cve_high_count
            if gate.type == "approval":
                if context.ai_risk:
                    gate_context["ai_risk"] = context.ai_risk
                if context.extra.get("plan_summary"):
                    gate_context["plan_summary"] = context.extra["plan_summary"]

            try:
                item = self._wic.request(
                    type=gate.type,
                    deployment=deployment,
                    commit=commit,
                    context=gate_context,
                    expires_minutes=gate.timeout_minutes,
                )
                logger.info("gate.work_item_created", item_id=item.id, gate_type=gate.type, deployment=deployment)
                return item
            except WorkItemError as exc:
                logger.warning("gate.work_item_create_failed", gate_type=gate.type, error=str(exc))
                continue

        return None

    # ------------------------------------------------------------------
    # Resume verification
    # ------------------------------------------------------------------

    def verify_resume(self, resume_id: str, commit: str) -> WorkItem:
        """Verify a --resume work item is approved for this commit. Raises on any issue."""
        # Determine expected gate type from the work item ID prefix
        parts = resume_id.split("/", 1)
        expected_type = parts[0] if len(parts) == 2 else ""
        return self._wic.verify_resolved(
            item_id=resume_id,
            expected_type=expected_type,
            expected_commit=commit,
        )
