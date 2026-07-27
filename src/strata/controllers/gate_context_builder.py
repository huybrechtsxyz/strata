"""Builds a GateContext from plan output, cost data, and CVE artifacts — ADR-0057 Phase 4."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from strata.controllers.gate_controller import GateContext
from strata.logger import get_logger

logger = get_logger(__name__)

# CVE artifact filename written by the build command
CVE_AUDIT_ARTIFACT = "cve-audit.json"


class GateContextBuilder:
    """Builds a GateContext from available runtime artifacts.

    Sources:
    - ``deployer.show_plan()`` — plan JSON (resource creates/updates/deletes + cost hint)
    - ``cost.json`` in build path — Infracost monthly delta
    - ``cve-audit.json`` in build path — CVE counts from SBOM audit
    - AI risk from ``self._output_data["ai_analysis"]`` on the deploy command
    """

    def __init__(self, build_path: Optional[Path] = None, deployment_service: Any = None) -> None:
        self._build_path = build_path
        self._deployment_service = deployment_service

    # ------------------------------------------------------------------
    # Public factory
    # ------------------------------------------------------------------

    def build(
        self,
        stage: Any = None,
        deployer: Any = None,
        ai_analysis: Optional[Dict[str, Any]] = None,
    ) -> GateContext:
        """Build a fully-populated GateContext for gate condition evaluation."""
        ctx = GateContext(current_time_utc=datetime.now(timezone.utc))

        # Cost delta from Infracost (prefer cost.json artifact over live run)
        ctx.cost_delta_monthly = self._read_cost_delta(stage)

        # CVE counts from cve-audit.json artifact
        cve = self._read_cve_artifact(stage)
        if cve:
            ctx.cve_critical_count = cve.get("critical", 0)
            ctx.cve_high_count = cve.get("high", 0)

        # AI risk from plan analysis results
        if ai_analysis:
            plan_analysis = ai_analysis.get("plan_analysis", {})
            ctx.ai_risk = plan_analysis.get("risk")

        # Plan summary in extra (informational — not used for condition eval)
        if deployer and hasattr(deployer, "show_plan"):
            try:
                ok, plan_data, _ = deployer.show_plan()
                if ok and plan_data:
                    ctx.extra["plan_summary"] = self._summarise_plan(plan_data)
            except Exception:
                pass

        logger.debug(
            "gate_context_built",
            cost_delta_monthly=ctx.cost_delta_monthly,
            cve_critical=ctx.cve_critical_count,
            cve_high=ctx.cve_high_count,
            ai_risk=ctx.ai_risk,
        )
        return ctx

    # ------------------------------------------------------------------
    # Cost delta
    # ------------------------------------------------------------------

    def _read_cost_delta(self, stage: Any = None) -> Optional[float]:
        """Read monthly cost delta from cost.json in the build path."""
        if not self._build_path or not self._deployment_service:
            return None
        try:
            if stage and self._deployment_service:
                deploy_build = self._deployment_service.get_build_path(self._build_path)
            else:
                deploy_build = self._build_path

            cost_path = deploy_build / "cost.json"
            if not cost_path.exists():
                return None

            data = json.loads(cost_path.read_text(encoding="utf-8"))
            # cost.json top-level keys OR nested under "diff"
            diff = data.get("diff", data)
            total = diff.get("totalMonthlyCost", "0.00")
            past_total = diff.get("pastTotalMonthlyCost", "0.00")
            return round(float(total or 0) - float(past_total or 0), 2)
        except (OSError, json.JSONDecodeError, ValueError, TypeError) as exc:
            logger.debug("gate_context.cost_read_error", error=str(exc))
            return None

    # ------------------------------------------------------------------
    # CVE counts
    # ------------------------------------------------------------------

    def _read_cve_artifact(self, stage: Any = None) -> Optional[Dict[str, Any]]:
        """Read cve-audit.json from the build path if it exists."""
        if not self._build_path or not self._deployment_service:
            return None
        try:
            if stage and self._deployment_service:
                deploy_build = self._deployment_service.get_build_path(self._build_path)
            else:
                deploy_build = self._build_path

            cve_path = deploy_build / CVE_AUDIT_ARTIFACT
            if not cve_path.exists():
                # Also try work_path root
                return None
            return json.loads(cve_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.debug("gate_context.cve_read_error", error=str(exc))
            return None

    # ------------------------------------------------------------------
    # Plan summary (for approval context enrichment)
    # ------------------------------------------------------------------

    @staticmethod
    def _summarise_plan(plan_data: Dict[str, Any]) -> Dict[str, Any]:
        """Extract a human-readable summary from terraform plan JSON."""
        summary: Dict[str, Any] = {}
        try:
            # Terraform plan JSON — resource_changes gives creates/updates/deletes
            resource_changes = plan_data.get("resource_changes", [])
            creates = updates = deletes = replaces = 0
            for rc in resource_changes:
                actions = rc.get("change", {}).get("actions", [])
                if "create" in actions and "delete" not in actions:
                    creates += 1
                elif "update" in actions:
                    updates += 1
                elif "delete" in actions and "create" not in actions:
                    deletes += 1
                elif "delete" in actions and "create" in actions:
                    replaces += 1
            total = creates + updates + deletes + replaces
            summary = {
                "creates": creates,
                "updates": updates,
                "deletes": deletes,
                "replaces": replaces,
                "total_changes": total,
                "summary": f"{creates} add, {updates} change, {deletes} destroy, {replaces} replace",
            }
        except (TypeError, AttributeError):
            pass
        return summary
