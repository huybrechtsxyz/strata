"""Built-in prompt for policy violation review and YAML fix suggestions."""

from __future__ import annotations

import json
from typing import Any


class PolicyReviewPrompt:
    """Plain-language explanation of policy violations with suggested fixes."""

    VERSION = "1.0"

    SYSTEM = """\
You are a platform policy advisor. Validation has detected policy violations in a
deployment configuration. Analyse each violation and respond with a JSON object only.

Required fields:
  "summary"    : 1-2 sentence overview of the violations found.
  "severity"   : overall severity — "low" | "medium" | "high" | "critical".
  "violations" : list of objects, each with:
                   "policy"      : policy name or id,
                   "description" : plain-language explanation of what the violation means,
                   "fix"         : concrete YAML change that would resolve it.
  "recommendations" : list of strings — broader advice to prevent recurrence."""

    @staticmethod
    def build_user_prompt(violations: list[Any], context: dict[str, Any]) -> str:
        deployment = context.get("deployment", "unknown")
        violations_text = json.dumps(violations, indent=2, default=str)
        if len(violations_text) > 8000:
            violations_text = violations_text[:8000] + "\n... [truncated]"

        return (
            f"Deployment: {deployment}\n\n"
            f"Policy violations ({len(violations)} total):\n```json\n{violations_text}\n```"
        )
