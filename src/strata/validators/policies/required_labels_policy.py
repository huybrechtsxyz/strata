#!/usr/bin/env python3
"""Built-in policy: required labels on namespaces.

Evaluates at the ``build`` phase.  Checks that every namespace in the built
``PlatformArtifactModel`` carries all labels declared in the policy's
``required_labels`` list.

Graceful degradation
--------------------
- No platform artifact in context → pass (skip)
- ``required_labels`` missing or empty in policy configuration → pass (skip)
- Namespace has no labels dict → treated as empty (all keys missing)
"""

from typing import Any, Dict, List

from strata.models.policy_model import PolicyModel
from strata.validators.policies.base_policy import BasePolicy, PolicyContext, PolicyResult


class RequiredTagsPolicy(BasePolicy):
    """Deny builds where namespaces are missing required labels."""

    def __init__(self, policy_model: PolicyModel) -> None:
        super().__init__(policy_model)

    def evaluate(self, context: PolicyContext) -> PolicyResult:
        # --- Guard: platform artifact required ---
        if context.platform_artifact is None:
            return PolicyResult(
                passed=True,
                policy_name=self.name,
                enforcement=self.enforcement,
                details={"skipped": "no platform artifact available"},
            )

        # --- Read required_labels from policy configuration ---
        configuration: Dict[str, Any] = self.policy.configuration or {}
        required_labels: List[str] = configuration.get("required_labels") or []
        if not required_labels:
            return PolicyResult(
                passed=True,
                policy_name=self.name,
                enforcement=self.enforcement,
                details={"skipped": "no required_labels configured"},
            )

        # --- Check every namespace ---
        violations: List[str] = []
        namespaces = getattr(getattr(context.platform_artifact, "spec", None), "namespaces", None) or []
        for namespace in namespaces:
            labels: Dict[str, Any] = getattr(namespace, "labels", None) or {}
            for key in required_labels:
                if key not in labels:
                    violations.append(f"Namespace '{namespace.name}' is missing required label '{key}'")

        return PolicyResult(
            passed=len(violations) == 0,
            policy_name=self.name,
            enforcement=self.enforcement,
            violations=violations,
        )
