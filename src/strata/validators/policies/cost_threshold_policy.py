#!/usr/bin/env python3
"""Built-in policy: enforce a maximum monthly cost threshold.

Evaluates at the ``plan`` phase. Reads ``cost.json`` from the build directory
and compares the total monthly cost against a configured maximum. ``cost.json``
is written automatically by the deploy pipeline's own cost-diff step whenever a
cost estimator (e.g. Infracost) is declared — dry-run or real deploy, not gated
by ``--dry-run`` (ADR-0031 section 3a) — and is also written by the on-demand
``strata cost show`` command.

Graceful degradation
--------------------
- ``cost.json`` not found or unparseable → governed by ``on_missing_data``
  (default: ``skip`` — pass; ``warn`` — pass with a visible warning; ``block`` —
  fail per ``enforcement``). See ADR-0082.
- ``max_monthly`` not configured → always skip (pass) — not applicable, not a
  missing-data case, unaffected by ``on_missing_data``.
- Cost data is exactly zero → always skip (pass) — a real computed value, not
  an absence, unaffected by ``on_missing_data``.
- ``environment_pattern`` configured → only evaluate for matching environment
  names; non-matching environments always skip regardless of ``on_missing_data``.

Example configuration YAML::

    policies:
      - name: prod_cost_gate
        type: cost_threshold
        phase: plan
        enforcement: deny
        description: "Block deployments over €10,000/month"
        configuration:
          max_monthly: 10000
          currency: EUR              # informational only (not enforced)

      - name: dev_cost_cap
        type: cost_threshold
        phase: plan
        enforcement: warn
        description: "Warn when dev exceeds €500/month"
        configuration:
          max_monthly: 500
          environment_pattern: "dev*"
"""

import fnmatch
from typing import Any, Dict, Optional

from strata.logger import get_logger
from strata.models.policy_model import PolicyModel
from strata.utils.cost_json import extract_total_monthly
from strata.validators.policies.base_policy import BasePolicy, PolicyContext, PolicyResult


class CostThresholdPolicy(BasePolicy):
    """Deny (or warn/audit) deployments whose estimated monthly cost exceeds the threshold."""

    def __init__(self, policy_model: PolicyModel) -> None:
        super().__init__(policy_model)
        self.logger = get_logger(__name__)

    def evaluate(self, context: PolicyContext) -> PolicyResult:
        configuration: Dict[str, Any] = self.policy.configuration or {}
        max_monthly: Optional[float] = configuration.get("max_monthly")
        currency: str = configuration.get("currency", "USD")
        env_pattern: Optional[str] = configuration.get("environment_pattern")

        # --- skip if not configured ---
        if max_monthly is None:
            return self._skip("max_monthly not configured")

        try:
            max_monthly = float(max_monthly)
        except (ValueError, TypeError):
            return self._skip("max_monthly is not a valid number")

        # --- skip if environment_pattern doesn't match ---
        if env_pattern:
            env_name = self._get_environment_name(context)
            if env_name and not fnmatch.fnmatchcase(env_name, env_pattern):
                return self._skip(f"environment '{env_name}' does not match pattern '{env_pattern}'")

        # --- missing data if no cost data available (ADR-0082: on_missing_data) ---
        if context.cost_data is None:
            reason = (
                "cost.json not found — declare a cost estimator integration "
                "(e.g. type: infracost) in spec.integrations, or run 'strata cost show'"
            )
            # A cost gate that has never once evaluated is worth more than debug-level
            # visibility: warn so the skip surfaces in command output, not just logs.
            self.logger.warning("cost_threshold_policy_skipped_no_data", policy=self.name, reason=reason)
            return self._missing_data(reason)

        # --- resolve total monthly cost from cost.json ---
        total = self._extract_total_monthly(context.cost_data)
        if total is None:
            return self._missing_data("cost.json contains no parseable cost data")

        if total == 0.0:
            return self._skip("cost.json total is zero — skipping threshold check")

        # --- evaluate threshold ---
        passed = total <= max_monthly
        violations = []
        if not passed:
            violations.append(
                f"Estimated monthly cost {total:.2f} {currency} exceeds maximum {max_monthly:.2f} {currency}"
            )

        return PolicyResult(
            passed=passed,
            policy_name=self.name,
            enforcement=self.enforcement,
            policy_type="cost_threshold",
            violations=violations,
            details={
                "total_monthly": total,
                "max_monthly": max_monthly,
                "currency": currency,
            },
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _skip(self, reason: str) -> PolicyResult:
        self.logger.debug("cost_threshold_policy_skipped", policy=self.name, reason=reason)
        return PolicyResult(
            passed=True,
            policy_name=self.name,
            enforcement=self.enforcement,
            policy_type="cost_threshold",
            details={"skipped": reason},
        )

    def _get_environment_name(self, context: PolicyContext) -> Optional[str]:
        """Return the environment name from the deployment service, or None."""
        try:
            if context.deployment_service is None:
                return None
            env_service = context.deployment_service.get_environment_service()
            if env_service is None:
                return None
            return env_service.get_name()
        except Exception:
            return None

    def _extract_total_monthly(self, cost_data: Dict[str, Any]) -> Optional[float]:
        """Sum total monthly cost across all provisioners in cost.json.

        cost.json structure:
            {"provisioners": {"terraform": {"breakdown": {"totalMonthlyCost": "1202.40"}}}}

        Also handles top-level totalMonthlyCost for simpler structures.

        Delegates to ``strata.utils.cost_json`` — the single shared parser also
        used by ``GateContextBuilder`` and ``CostHistoryStore`` (ADR-0031 section 3a).
        """
        return extract_total_monthly(cost_data)
