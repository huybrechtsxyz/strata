#!/usr/bin/env python3
"""Built-in policy: block specific packages by purl glob pattern.

Evaluates at the ``build`` phase.  Checks that no SBOM component matches a
denied purl pattern (fnmatch-style glob).

Graceful degradation
--------------------
- No SBOM components in context → pass (skip)
- Empty ``denied`` list in configuration → pass (skip, nothing to enforce)
"""

from fnmatch import fnmatch
from typing import Any, Dict, List

from strata.models.policy_model import PolicyModel
from strata.validators.policies.base_policy import BasePolicy, PolicyContext, PolicyResult


class SbomDeniedPackagesPolicy(BasePolicy):
    """Deny builds containing packages that match a blocklist pattern."""

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
        denied: List[str] = configuration.get("denied") or []
        reason: str = configuration.get("reason", "Package is on the deny list")

        if not denied:
            return PolicyResult(
                passed=True,
                policy_name=self.name,
                enforcement=self.enforcement,
                details={"skipped": "no denied patterns configured"},
            )

        # --- Check components against deny patterns ---
        violations: List[str] = []
        for component in context.sbom_components:
            for pattern in denied:
                if fnmatch(component.purl, pattern) or fnmatch(component.name, pattern):
                    violations.append(
                        f"Component '{component.name}' (purl: {component.purl}) "
                        f"matches denied pattern '{pattern}': {reason}"
                    )
                    break  # One match is enough per component

        return PolicyResult(
            passed=len(violations) == 0,
            policy_name=self.name,
            enforcement=self.enforcement,
            violations=violations,
        )
