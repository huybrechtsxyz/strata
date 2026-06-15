#!/usr/bin/env python3
"""Built-in policy: name pattern enforcement.

Evaluates at the ``validate`` phase.  Checks that the configuration's
``meta.name`` matches a user-supplied regular expression pattern.

Graceful degradation
--------------------
- No ConfigurationService in context → pass (skip)
- No loaded model on the service → pass (skip)
- ``pattern`` missing from policy configuration → pass (skip)
"""

import re
from typing import Any, Dict, List

from strata.models.policy_model import PolicyModel
from strata.validators.policies.base_policy import BasePolicy, PolicyContext, PolicyResult


class NamingPolicy(BasePolicy):
    """Deny configurations whose name does not match the required pattern."""

    def __init__(self, policy_model: PolicyModel) -> None:
        super().__init__(policy_model)

    def evaluate(self, context: PolicyContext) -> PolicyResult:
        # --- Guards ---
        if context.configuration_service is None:
            return PolicyResult(
                passed=True,
                policy_name=self.name,
                enforcement=self.enforcement,
                details={"skipped": "no configuration service available"},
            )

        config_model = getattr(context.configuration_service, "model", None)
        if config_model is None:
            return PolicyResult(
                passed=True,
                policy_name=self.name,
                enforcement=self.enforcement,
                details={"skipped": "configuration service has no loaded model"},
            )

        configuration: Dict[str, Any] = self.policy.configuration or {}
        pattern: str = configuration.get("pattern", "")
        if not pattern:
            return PolicyResult(
                passed=True,
                policy_name=self.name,
                enforcement=self.enforcement,
                details={"skipped": "no pattern configured"},
            )

        # --- Evaluate ---
        name = str(config_model.meta.name)
        violations: List[str] = []
        if not re.fullmatch(pattern, name):
            violations.append(f"Name '{name}' does not match required pattern '{pattern}'")

        return PolicyResult(
            passed=len(violations) == 0,
            policy_name=self.name,
            enforcement=self.enforcement,
            violations=violations,
        )
