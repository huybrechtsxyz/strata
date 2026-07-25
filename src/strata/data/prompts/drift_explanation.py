"""Built-in prompt for infrastructure drift explanation."""

from __future__ import annotations

import json
from typing import Any


class DriftExplanationPrompt:
    """Plain-language explanation of detected infrastructure drift."""

    VERSION = "1.0"

    SYSTEM = """\
You are an infrastructure compliance analyst. Infrastructure drift has been detected —
the live state of resources differs from what the IaC configuration declares.
Analyse the drift report and respond with a JSON object only.

Required fields:
  "summary"         : 2-3 sentence plain-language explanation of what drifted and why it matters.
  "severity"        : one of "low" | "medium" | "high" | "critical".
  "drifted_resources": list of objects, each with:
                        "resource"  : resource address,
                        "attributes": list of attribute names that differ.
  "likely_cause"    : most probable explanation for the drift.
  "recommendations" : list of strings — how to reconcile the drift."""

    @staticmethod
    def build_user_prompt(drift_report: dict[str, Any], context: dict[str, Any]) -> str:
        deployment = context.get("deployment", "unknown")
        environment = context.get("environment", "unknown")

        report_text = json.dumps(drift_report, indent=2, default=str)
        # Cap report size
        if len(report_text) > 8000:
            report_text = report_text[:8000] + "\n... [truncated]"

        return (
            f"Deployment: {deployment}\n"
            f"Environment: {environment}\n\n"
            f"Drift report:\n```json\n{report_text}\n```"
        )
