#!/usr/bin/env python3
"""Built-in policy: enforce pinned versions on SBOM components.

Evaluates at the ``build`` phase.  Checks that components from specified
collectors have explicit, non-floating version tags.

Graceful degradation
--------------------
- No SBOM components in context → pass (skip)
- Empty ``collectors`` list in configuration → checks all collectors
"""

from typing import Any, Dict, List

from strata.models.policy_model import PolicyModel
from strata.validators.policies.base_policy import BasePolicy, PolicyContext, PolicyResult


class SbomPinnedVersionsPolicy(BasePolicy):
    """Deny builds where SBOM components have unpinned or floating versions."""

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
        collectors: List[str] = configuration.get("collectors") or []
        allow_latest: bool = configuration.get("allow_latest", False)
        require_digest: bool = configuration.get("require_digest", False)

        # --- Check components ---
        violations: List[str] = []
        for component in context.sbom_components:
            # Filter by collector if specified
            if collectors and component.source_collector not in collectors:
                continue

            # Missing version
            if not component.version:
                violations.append(f"Component '{component.name}' ({component.source_collector}) has no version pinned")
                continue

            # Floating tag stability
            tag_stability = component.properties.get("strata:tag-stability", "")
            if tag_stability == "floating":
                violations.append(
                    f"Component '{component.name}' ({component.source_collector}) "
                    f"uses floating tag '{component.version}'"
                )
                continue

            # Reject :latest
            if not allow_latest and component.version.lower() == "latest":
                violations.append(f"Component '{component.name}' ({component.source_collector}) uses 'latest' tag")
                continue

            # Require digest
            if require_digest and "@sha256:" not in component.purl:
                violations.append(
                    f"Component '{component.name}' ({component.source_collector}) "
                    f"missing digest (require_digest is enabled)"
                )

        return PolicyResult(
            passed=len(violations) == 0,
            policy_name=self.name,
            enforcement=self.enforcement,
            violations=violations,
        )
