#!/usr/bin/env python3
"""Built-in policy: OPA (Open Policy Agent) Rego policy evaluation.

Evaluates a Rego rule against strata's deployment context.  Supports two
modes (tried in order):

1. **HTTP** — POST to a running OPA server at ``configuration.endpoint``
   or the ``OPA_ENDPOINT`` environment variable.
2. **CLI** — Run ``opa eval`` as a stateless subprocess (no server required).

Graceful degradation
--------------------
- Neither OPA server reachable nor ``opa`` binary installed → pass (skip)
- ``rule`` not configured → pass (skip)
- ``policy_dir`` missing (CLI mode) → pass (skip)
- OPA rule returns empty set / false → pass
- Any network / subprocess failure → pass (skip, non-fatal)

Example configuration YAML::

    policies:
      - name: zone_enforcement
        type: opa
        phase: build
        enforcement: deny
        description: "Enforce tenant zone restrictions via OPA"
        configuration:
          rule: "data.strata.zones.deny"
          policy_dir: ".strata/policies/"   # directory with .rego files
          endpoint: "http://localhost:8181" # optional: OPA server URL
          timeout: 30

OPA rule convention (must return a set of violation strings)::

    package strata.zones

    deny contains msg if {
        resource := input.platform.spec.resources[_]
        not resource.properties.region in input.configuration.spec.allowed_regions
        msg := sprintf("Resource '%s' in disallowed region '%s'",
                       [resource.meta.name, resource.properties.region])
    }

Input document provided to OPA::

    {
      "phase": "build",
      "platform": { ... },        // platform artifact model (if available)
      "configuration": { ... },   // configuration model spec (if available)
      "deployment": { ... },      // deployment model spec (if available)
      "plan_data": { ... },       // terraform plan JSON (if available)
      "work_path": "...",
      "build_path": "..."
    }
"""

from pathlib import Path
from typing import Any, Dict, List, Optional

from strata.logger import get_logger
from strata.models.policy_model import PolicyModel
from strata.validators.policies.base_policy import BasePolicy, PolicyContext, PolicyResult

logger = get_logger(__name__)


class OPAPolicy(BasePolicy):
    """Evaluate a Rego policy via OPA HTTP server or ``opa eval`` CLI."""

    def __init__(self, policy_model: PolicyModel) -> None:
        super().__init__(policy_model)
        self.logger = get_logger(__name__)

    def evaluate(self, context: PolicyContext) -> PolicyResult:
        configuration: Dict[str, Any] = self.policy.configuration or {}
        rule: str = configuration.get("rule") or ""
        policy_dir: Optional[str] = configuration.get("policy_dir")
        endpoint: Optional[str] = configuration.get("endpoint")
        timeout: int = int(configuration.get("timeout") or 30)

        if not rule:
            return PolicyResult(
                passed=True,
                policy_name=self.name,
                enforcement=self.enforcement,
                details={"skipped": "no rule configured"},
            )

        # Resolve policy_dir relative to work_path
        if policy_dir and context.work_path:
            resolved = Path(context.work_path) / policy_dir
            if not resolved.exists():
                return PolicyResult(
                    passed=True,
                    policy_name=self.name,
                    enforcement=self.enforcement,
                    details={"skipped": f"policy_dir not found: {resolved}"},
                )
            policy_dir = str(resolved)

        # Build OPA input document
        input_data = self._build_input(context)

        # Run OPA
        opa_result = self._run_opa(rule, input_data, endpoint=endpoint, policy_dir=policy_dir, timeout=timeout)
        if opa_result is None:
            return PolicyResult(
                passed=True,
                policy_name=self.name,
                enforcement=self.enforcement,
                details={"skipped": "OPA not available or evaluation failed"},
            )

        violations: List[str] = [f"[{rule}] {v}" for v in opa_result.violations]

        return PolicyResult(
            passed=len(violations) == 0,
            policy_name=self.name,
            enforcement=self.enforcement,
            violations=violations,
            details={
                "rule": rule,
                "mode": "http" if endpoint else "cli",
                "violation_count": len(violations),
            },
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _build_input(self, context: PolicyContext) -> Dict[str, Any]:
        """Serialize PolicyContext to the OPA input document."""
        doc: Dict[str, Any] = {
            "phase": context.phase,
            "work_path": str(context.work_path) if context.work_path else None,
            "build_path": str(context.build_path) if context.build_path else None,
        }

        # Platform artifact
        if context.platform_artifact is not None:
            try:
                doc["platform"] = context.platform_artifact.model_dump(mode="json")
            except Exception:
                pass

        # Configuration model
        if context.configuration_service is not None:
            try:
                model = context.configuration_service.model
                if model is not None:
                    doc["configuration"] = model.model_dump(mode="json")
            except Exception:
                pass

        # Deployment model
        if context.deployment_service is not None:
            try:
                model = context.deployment_service.model
                if model is not None:
                    doc["deployment"] = model.model_dump(mode="json")
            except Exception:
                pass

        # Terraform plan data
        if context.plan_data is not None:
            doc["plan_data"] = context.plan_data

        return doc

    def _run_opa(
        self,
        rule: str,
        input_data: Dict[str, Any],
        endpoint: Optional[str],
        policy_dir: Optional[str],
        timeout: int,
    ):
        """Instantiate OPAIntegration and evaluate. Returns None on any failure."""
        from strata.integrations.opa import OPAIntegration
        from strata.models.integration_model import IntegrationModel

        config = IntegrationModel(name="opa", type="opa")
        opa = OPAIntegration(config)

        # If no endpoint configured and CLI not available, skip gracefully
        import os

        has_endpoint = bool(endpoint or os.environ.get("OPA_ENDPOINT"))
        if not has_endpoint:
            available, _ = opa.ensure_available()
            if not available:
                self.logger.debug("opa policy: OPA not available, skipping")
                return None

        try:
            return opa.evaluate(
                rule,
                input_data,
                endpoint=endpoint,
                policy_dir=policy_dir,
                timeout=timeout,
            )
        except RuntimeError as exc:
            self.logger.warning("opa policy: evaluation failed", error=str(exc))
            return None
