#!/usr/bin/env python3
"""Built-in policy: limit SBOM component count (complexity budget).

Evaluates at the ``build`` phase.  Enforces an upper bound on the total
number of SBOM components and optionally per-collector limits.

Graceful degradation
--------------------
- No SBOM components in context → pass (skip)
- Neither ``max_count`` nor ``per_collector`` configured → pass (skip)
"""

from collections import Counter
from typing import Any, Dict, List

from strata.models.policy_model import PolicyModel
from strata.validators.policies.base_policy import BasePolicy, PolicyContext, PolicyResult


class SbomMaxComponentsPolicy(BasePolicy):
    """Deny builds where SBOM component count exceeds configured limits."""

    def __init__(self, policy_model: PolicyModel) -> None:
        super().__init__(policy_model)

    def evaluate(self, context: PolicyContext) -> PolicyResult:
        # --- Guard: sbom components required ---
        if not context.sbom_components:
            return PolicyResult(
                passed=True,
                policy_name=self.name,
                enforcement=self.enforcement,
                details={"skipped": "no SBOM components available"},
            )

        # --- Read configuration ---
        configuration: Dict[str, Any] = self.policy.configuration or {}
        max_count: int = configuration.get("max_count") or 0
        per_collector: Dict[str, int] = configuration.get("per_collector") or {}

        if not max_count and not per_collector:
            return PolicyResult(
                passed=True,
                policy_name=self.name,
                enforcement=self.enforcement,
                details={"skipped": "no component limits configured"},
            )

        # --- Check total ---
        violations: List[str] = []
        total = len(context.sbom_components)

        if max_count and total > max_count:
            violations.append(f"Total component count ({total}) exceeds maximum ({max_count})")

        # --- Check per-collector ---
        if per_collector:
            counts: Counter[str] = Counter()
            for component in context.sbom_components:
                counts[component.source_collector] += 1

            for collector_name, limit in per_collector.items():
                actual = counts.get(collector_name, 0)
                if actual > limit:
                    violations.append(
                        f"Collector '{collector_name}' has {actual} components, exceeding limit of {limit}"
                    )

        return PolicyResult(
            passed=len(violations) == 0,
            policy_name=self.name,
            enforcement=self.enforcement,
            violations=violations,
        )
