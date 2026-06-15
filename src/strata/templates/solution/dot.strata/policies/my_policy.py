"""
Custom policy template for Strata.

Copy / rename this file, implement the class, then register the type so the
PolicyEngine can instantiate it when your configuration uses your custom type.

Drop-in location: .strata/policies/<your_name>.py
The platform auto-discovers and loads all .py files in that directory at
startup, so no changes to core code are required.

Minimal checklist
-----------------
1. Rename the class and update TYPE_NAME.
2. Implement evaluate() — inspect PolicyContext, return PolicyResult.
3. Call register() at the bottom so PolicyEngine can dispatch to your type.

How it works
------------
Auto-discovery: At startup the platform scans ``.strata/policies/`` and
imports every ``.py`` file it finds.  Each file's ``register()`` function is
called, which inserts the type→class mapping into the PolicyEngine registry.
Afterwards, any YAML entry whose ``type:`` matches the registered string will
be instantiated by ``PolicyEngine._create()``.

Evaluation is stateless — a fresh instance is created per evaluation cycle.
Policies MUST NOT hold mutable state between evaluations.

Available context (from PolicyContext)
--------------------------------------
  phase                 - which phase is running (validate | build | plan | deploy)
  work_path             - workspace root (Path or None)
  deployment_service    - DeploymentService (or None)
  configuration_service - ConfigurationService (or None)
  platform_artifact     - PlatformArtifactModel (build/plan/deploy only)
  plan_data             - terraform show -json dict (plan phase only)
  build_path            - build output directory (build/plan/deploy only)

Example YAML usage (after registration)
----------------------------------------
  policies:
    - name: my_custom_check
      type: my_policy
      phase: plan
      enforcement: warn
      description: "Example custom policy"
      configuration:
        max_resources: 50
"""

from __future__ import annotations

from typing import Any, Dict, List

from strata.models.policy_model import PolicyModel
from strata.validators.policies.base_policy import BasePolicy, PolicyContext, PolicyResult

# --------------------------------------------------------------------------- #
# Policy type name — must match the ``type:`` field in your YAML config        #
# --------------------------------------------------------------------------- #

TYPE_NAME = "my_policy"


class MyPolicy(BasePolicy):
    """Replace this docstring with a description of your custom policy.

    This example checks that a Terraform plan does not create more resources
    than a configurable threshold.
    """

    def __init__(self, policy_model: PolicyModel) -> None:
        super().__init__(policy_model)
        # Read custom configuration from the policy's YAML ``configuration:`` block
        config: Dict[str, Any] = self.policy.configuration or {}
        self._max_resources: int = int(config.get("max_resources", 50))

    # ------------------------------------------------------------------ #
    # Required: evaluate()                                                 #
    # ------------------------------------------------------------------ #

    def evaluate(self, context: PolicyContext) -> PolicyResult:
        """Evaluate the policy against the given context.

        Must return a PolicyResult with:
          - passed: True/False
          - policy_name: self.name (inherited from BasePolicy)
          - enforcement: self.enforcement (inherited)
          - violations: list of human-readable violation messages
          - details: optional dict with structured data for programmatic use
        """
        # --- Guard: graceful skip when required data is absent ---
        if context.plan_data is None:
            return PolicyResult(
                passed=True,
                policy_name=self.name,
                enforcement=self.enforcement,
                details={"skipped": "no plan data available"},
            )

        # --- Core logic: count resources being created ---
        resource_changes: List[Any] = context.plan_data.get("resource_changes") or []
        creates = [rc for rc in resource_changes if "create" in (rc.get("change", {}).get("actions") or [])]

        violations: List[str] = []
        if len(creates) > self._max_resources:
            violations.append(f"Plan creates {len(creates)} resources, exceeding the maximum of {self._max_resources}")

        return PolicyResult(
            passed=len(violations) == 0,
            policy_name=self.name,
            enforcement=self.enforcement,
            violations=violations,
            details={
                "resources_created": len(creates),
                "max_allowed": self._max_resources,
            },
        )


# --------------------------------------------------------------------------- #
# Registration — called automatically when the file is loaded by the platform  #
# --------------------------------------------------------------------------- #


def register() -> None:
    """Register this policy type with PolicyEngine.

    The platform calls register() on every .py file it discovers in
    .strata/policies/.  The type string must match the ``type:``
    field you use in your configuration YAML.
    """
    from strata.validators.policies.policy_engine import PolicyEngine

    PolicyEngine.register_type(TYPE_NAME, MyPolicy)
