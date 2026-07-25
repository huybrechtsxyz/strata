"""AI plan review policy — gates deployment based on LLM risk assessment (ADR-0025 §Phase 4).

Configuration YAML example::

    policies:
      - name: ai_plan_gate
        type: ai_review
        phase: plan
        enforcement: deny
        configuration:
          integration: ai-advisor      # name of the ai_agent integration to use
          risk_threshold: high         # deny if AI rates plan as high | critical (default: critical)
          ai_prompt: strict_review     # optional .strata/prompts override
"""

from __future__ import annotations

from typing import Any, Optional

from strata.logger import get_logger
from strata.validators.policies.base_policy import BasePolicy, PolicyContext, PolicyResult

logger = get_logger(__name__)
_RISK_LEVELS = {"low": 0, "medium": 1, "high": 2, "critical": 3}


class AiReviewPolicy(BasePolicy):
    """Evaluate a Terraform plan via ``AiAgentIntegration.analyse_plan()`` and
    gate the deployment if the assessed risk meets or exceeds ``risk_threshold``."""

    def evaluate(self, context: PolicyContext) -> PolicyResult:
        cfg = self.policy.configuration or {}
        integration_name: str = cfg.get("integration", "")
        threshold_str: str = cfg.get("risk_threshold", "critical").lower()
        threshold = _RISK_LEVELS.get(threshold_str, 3)

        # Resolve the AI integration from configuration service
        ai_integration = self._get_ai_integration(context, integration_name)
        if ai_integration is None:
            return PolicyResult(
                passed=True,
                policy_name=self.name,
                enforcement=self.enforcement,
                violations=[],
                details={"skipped": "No ai_agent integration available — policy skipped"},
            )

        ok, msg = ai_integration.ensure_available()
        if not ok:
            return PolicyResult(
                passed=True,
                policy_name=self.name,
                enforcement=self.enforcement,
                violations=[],
                details={"skipped": f"AI provider unavailable: {msg}"},
            )

        plan_data = context.plan_data or {}
        deployment_name = (
            str(context.deployment_service.model.meta.name)  # type: ignore[union-attr]
            if context.deployment_service
            else "unknown"
        )
        ai_context: dict[str, Any] = {
            "deployment": deployment_name,
            "work_path": str(context.work_path) if context.work_path else "",
        }

        try:
            response = ai_integration.analyse_plan({"stages": [plan_data]}, ai_context)
        except Exception as exc:
            logger.error("ai_review_policy_analysis_failed", policy=self.name, error=str(exc), exc_info=True)
            return PolicyResult(
                passed=False,
                policy_name=self.name,
                enforcement=self.enforcement,
                violations=[f"AI analysis failed: {exc}"],
                details={"error": str(exc)},
            )

        # Parse risk from response
        risk_str, parsed_content = _parse_risk(response.content)
        risk_level = _RISK_LEVELS.get(risk_str, 0)
        passed = risk_level < threshold

        violations: list[str] = []
        if not passed:
            concerns = parsed_content.get("concerns", [])
            violations.append(
                f"AI risk assessment: {risk_str.upper()} (threshold: {threshold_str.upper()}) — {response.model}"
            )
            violations.extend(str(c) for c in concerns)

        return PolicyResult(
            passed=passed,
            policy_name=self.name,
            enforcement=self.enforcement,
            violations=violations,
            details={
                "risk": risk_str,
                "summary": parsed_content.get("summary", ""),
                "recommendations": parsed_content.get("recommendations", []),
                "provider": response.provider,
                "model": response.model,
                "tokens": response.prompt_tokens + response.completion_tokens,
            },
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _get_ai_integration(context: PolicyContext, preferred_name: str) -> Optional[Any]:
        """Retrieve the first (or named) ai_agent integration from configuration."""
        cfg_svc = context.configuration_service
        if cfg_svc is None:
            return None
        model = cfg_svc.model
        if not model or not model.spec.integrations:
            return None

        specs = [i for i in model.spec.integrations if i.type == "ai_agent" and i.enabled]
        if not specs:
            return None

        spec = next((s for s in specs if s.name == preferred_name), specs[0])

        try:
            from strata.integrations.factory import IntegrationFactory

            return IntegrationFactory.create(spec)
        except Exception:
            return None


def _parse_risk(content: str) -> tuple[str, dict]:
    """Extract risk level from JSON response content. Returns (risk_str, full_dict)."""
    import json

    try:
        data = json.loads(content)
        risk = str(data.get("risk", "low")).lower()
        return risk, data
    except (json.JSONDecodeError, TypeError):
        # Fallback: scan for risk keywords in plain text
        lower = content.lower()
        for level in ("critical", "high", "medium", "low"):
            if level in lower:
                return level, {}
        return "low", {}
